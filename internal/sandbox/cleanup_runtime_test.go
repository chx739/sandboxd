package sandbox

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/fake"
	clienttesting "k8s.io/client-go/testing"
)

const testGCID = "gc-12345678123442348abc123456789abc"

func cleanupRuntimeFixture(t *testing.T) (*BackupRuntime, *fake.Clientset, WorkspaceAccess, string) {
	t.Helper()
	w, client, _, _ := runtimeFixture(t, false)
	r, path := freshRepositoryFixture(t)
	w.registry = r
	b, err := NewBackupRuntime(w, ownedJobFixture())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = r.Register(testWorkspaceID, "session-test", "branch-main"); err != nil {
		t.Fatal(err)
	}
	if _, err = r.Ready(testWorkspaceID); err != nil {
		t.Fatal(err)
	}
	a := boundFixture(t, r)
	client.PrependReactor("create", "pods", func(action clienttesting.Action) (bool, runtime.Object, error) {
		pod := action.(clienttesting.CreateAction).GetObject().(*corev1.Pod)
		pod.UID = types.UID("uid-" + pod.Name)
		pod.ResourceVersion = "1"
		return false, nil, nil
	})
	return b, client, a, path
}

func completeCleanupFixture(t *testing.T, b *BackupRuntime, client *fake.Clientset, barrier WorkspaceBarrier) {
	t.Helper()
	completedBackupFixture(t, b, client, barrier)
	b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) {
		output := map[string]any{"ok": true, "operation": barrier.Operation, "owner": barrier.Job.Owner, "resticVersion": "0.18.1"}
		if barrier.Operation == "forget" {
			output["snapshotId"] = barrier.Cleanup.SnapshotID
			output["forgotten"] = true
		} else {
			output["pruned"] = true
			output["outputSha256"] = strings.Repeat("5", 64)
			output["outputBytes"] = 17
		}
		return json.Marshal(output)
	}
}

func TestCleanupDurableGlobalBarrierAndProofBeforeRelease(t *testing.T) {
	b, client, a, path := cleanupRuntimeFixture(t)
	r := b.workspace.registry
	barrier, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "forget", strings.Repeat("1", 64))
	if err != nil || barrier.Phase != "reserved" {
		t.Fatal(barrier, err)
	}
	if pods, _ := client.CoreV1().Pods(StorageNamespace).List(context.Background(), metav1.ListOptions{}); len(pods.Items) != 0 {
		t.Fatal("reserve started deletion")
	}
	if _, err := r.Register(barrier.WorkspaceID, "session-maintenance", "branch-main"); err == nil {
		t.Fatal("maintenance ID became business workspace")
	}
	if _, err := b.Reserve(context.Background(), a, nextCheckpointID, "backup-other"); err == nil {
		t.Fatal("backup bypassed GC")
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	b.workspace.registry = r
	if restored, ok := b.CleanupBarrier(); !ok || restored != barrier {
		t.Fatal("GC restart lost binding", restored)
	}
	if _, err := b.Reserve(context.Background(), a, nextCheckpointID, "backup-other"); err == nil {
		t.Fatal("restart lost freeze")
	}
	if _, err := b.FinishCleanup(context.Background(), barrier); err == nil {
		t.Fatal("unsubmitted job released")
	}
	barrier, err = b.StartCleanup(context.Background(), barrier)
	if err != nil {
		t.Fatal(err)
	}
	pod, err := b.expected(barrier)
	if err != nil || len(pod.Spec.Volumes) != 2 || len(pod.Spec.Containers[0].VolumeMounts) != 2 || pod.Spec.Containers[0].Args[0] != "forget" {
		t.Fatal("maintenance exposed source PVC", pod, err)
	}
	completeCleanupFixture(t, b, client, barrier)
	barrier, result, err := b.WaitCleanup(context.Background(), barrier)
	if err != nil || result.SnapshotID != strings.Repeat("1", 64) || barrier.CleanupResult != result {
		t.Fatal(result, err)
	}
	if _, err := b.RetryCleanup(context.Background(), barrier, "gc-b"); err == nil {
		t.Fatal("verified success was repeated")
	}
	// 模拟已清理Pod但Finish元数据尚未释放时崩溃。
	if err := b.cleanupStoppedPod(context.Background(), barrier); err != nil {
		t.Fatal(err)
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	b.workspace.registry = r
	if _, got, err := b.WaitCleanup(context.Background(), barrier); err != nil || got != result {
		t.Fatal("persisted proof unavailable without Pod", got, err)
	}
	for range 2 {
		if _, err := b.FinishCleanup(context.Background(), barrier); err != nil {
			t.Fatal("finish not restart/idempotent", err)
		}
	}
	if _, err := b.Reserve(context.Background(), a, nextCheckpointID, "backup-other"); err != nil {
		t.Fatal("finished GC did not release repository", err)
	}
}

func TestCleanupFailureRetryKeepsTargetAndFreeze(t *testing.T) {
	b, client, a, path := cleanupRuntimeFixture(t)
	barrier, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "forget", strings.Repeat("1", 64))
	if err != nil {
		t.Fatal(err)
	}
	barrier, err = b.StartCleanup(context.Background(), barrier)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.RetryCleanup(context.Background(), barrier, "gc-b"); err == nil {
		t.Fatal("running cleanup replaced")
	}
	completeCleanupFixture(t, b, client, barrier)
	pod, err := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	pod.Status.Phase = corev1.PodFailed
	pod.Status.ContainerStatuses[0].State.Terminated.ExitCode = 1
	if _, err := client.CoreV1().Pods(StorageNamespace).Update(context.Background(), pod, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	stopped, _, err := b.WaitCleanup(context.Background(), barrier)
	if err == nil || stopped.Phase != "stopped" || stopped.CleanupResult != (CleanupResult{}) {
		t.Fatal("failure became proof", stopped, err)
	}
	if _, err := b.FinishCleanup(context.Background(), stopped); err == nil {
		t.Fatal("failure released freeze")
	}
	old := stopped
	stopped, err = b.RetryCleanup(context.Background(), stopped, "gc-b")
	if err != nil || stopped.Phase != "reserved" || stopped.Cleanup != old.Cleanup {
		t.Fatal(stopped, err)
	}
	if _, err := b.ReserveCleanup(context.Background(), testGCID, "gc-c", "forget", strings.Repeat("3", 64)); err == nil {
		t.Fatal("retry changed target")
	}
	if _, err := b.FinishCleanup(context.Background(), old); err == nil {
		t.Fatal("old attempt released replacement")
	}
	b.workspace.registry.Close()
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	b.workspace.registry = r
	if _, err := b.Reserve(context.Background(), a, nextCheckpointID, "backup-other"); err == nil {
		t.Fatal("retry restart lost freeze")
	}
	stopped, err = b.StartCleanup(context.Background(), stopped)
	if err != nil {
		t.Fatal(err)
	}
	completeCleanupFixture(t, b, client, stopped)
	stopped, _, err = b.WaitCleanup(context.Background(), stopped)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.FinishCleanup(context.Background(), stopped); err != nil {
		t.Fatal(err)
	}
	if _, err := r.reserveCleanup("gc-22345678123442348abc123456789abc", "gc-a", "prune", "", b.job); err == nil {
		t.Fatal("old attempt name reused")
	}
}

func TestCleanupRejectsWrongResultsAndUnknownStop(t *testing.T) {
	for _, mode := range []string{"wrong-owner", "bad-success", "wrong-snapshot", "wrong-version", "unknown-pod"} {
		t.Run(mode, func(t *testing.T) {
			b, client, _, _ := cleanupRuntimeFixture(t)
			barrier, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "forget", strings.Repeat("1", 64))
			if err != nil {
				t.Fatal(err)
			}
			barrier, err = b.StartCleanup(context.Background(), barrier)
			if err != nil {
				t.Fatal(err)
			}
			if mode == "unknown-pod" {
				if err := client.CoreV1().Pods(StorageNamespace).Delete(context.Background(), barrier.PodName, metav1.DeleteOptions{}); err != nil {
					t.Fatal(err)
				}
				if _, err := b.Abort(context.Background(), barrier); err == nil {
					t.Fatal("missing Pod accepted as stop")
				}
			} else {
				completeCleanupFixture(t, b, client, barrier)
				read := b.readLogs
				b.readLogs = func(ctx context.Context, p *corev1.Pod) ([]byte, error) {
					raw, _ := read(ctx, p)
					var out map[string]any
					json.Unmarshal(raw, &out)
					switch mode {
					case "wrong-owner":
						out["owner"] = ownedJobFixture().Owner.ControllerID
					case "bad-success":
						out["forgotten"] = false
					case "wrong-snapshot":
						out["snapshotId"] = strings.Repeat("8", 64)
					case "wrong-version":
						out["resticVersion"] = "other"
					}
					return json.Marshal(out)
				}
				ctx, cancel := context.WithTimeout(context.Background(), time.Second)
				defer cancel()
				if _, _, err := b.WaitCleanup(ctx, barrier); err == nil {
					t.Fatal("invalid result accepted")
				}
			}
			if got, _ := b.CleanupBarrier(); got.Phase == "released" || got.CleanupResult != (CleanupResult{}) {
				t.Fatal("unknown result released", got)
			}
		})
	}
}

func TestCleanupPruneAndMarkerProtection(t *testing.T) {
	b, client, _, _ := cleanupRuntimeFixture(t)
	if _, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "forget", b.job.Owner.OwnerSnapshotID); err == nil {
		t.Fatal("marker eligible")
	}
	barrier, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "prune", "")
	if err != nil {
		t.Fatal(err)
	}
	barrier, err = b.StartCleanup(context.Background(), barrier)
	if err != nil {
		t.Fatal(err)
	}
	completeCleanupFixture(t, b, client, barrier)
	barrier, result, err := b.WaitCleanup(context.Background(), barrier)
	if err != nil || result.OutputBytes != 17 || result.OutputSHA256 != strings.Repeat("5", 64) {
		t.Fatal(result, err)
	}
	if _, err := b.FinishCleanup(context.Background(), barrier); err != nil {
		t.Fatal(err)
	}
}

func TestCleanupCannotInterruptPendingBackupOrRestore(t *testing.T) {
	b, _, a, _ := cleanupRuntimeFixture(t)
	backup, err := b.Reserve(context.Background(), a, testCheckpointID, "backup-first")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "prune", ""); err == nil {
		t.Fatal("GC bypassed pending backup")
	}
	backup, err = b.Abort(context.Background(), backup)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.Finish(context.Background(), backup); err != nil {
		t.Fatal(err)
	}
	r := b.workspace.registry
	target := "workspace-223456789abcdef0123456789abcdef0"
	if _, err := r.Register(target, "session-target", "branch-target"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Ready(target); err != nil {
		t.Fatal(err)
	}
	source := restoreFixtureSource()
	source.RepositoryCryptoID = b.job.Owner.RepositoryCryptoID
	if _, err := r.reserveRestore(target, nextCheckpointID, "restore-first", b.job, source); err != nil {
		t.Fatal(err)
	}
	if _, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "prune", ""); err == nil {
		t.Fatal("GC bypassed pending restore")
	}
}

func TestCleanupReplayRejectsUnverifiedReleaseAndHistoricalRebind(t *testing.T) {
	t.Run("unverified-release", func(t *testing.T) {
		b, _, _, path := cleanupRuntimeFixture(t)
		barrier, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "prune", "")
		if err != nil {
			t.Fatal(err)
		}
		barrier, err = b.Abort(context.Background(), barrier)
		if err != nil {
			t.Fatal(err)
		}
		barrier.Phase = "released"
		if _, err := b.workspace.registry.journal.Append("workspace.barrier", barrier); err != nil {
			t.Fatal(err)
		}
		b.workspace.registry.Close()
		if reopened, err := OpenWorkspaceRegistry(path); err == nil {
			reopened.Close()
			t.Fatal("complete unverified release accepted")
		}
	})
	t.Run("historical-rebind", func(t *testing.T) {
		b, client, _, path := cleanupRuntimeFixture(t)
		for i, gcID := range []string{testGCID, "gc-22345678123442348abc123456789abc"} {
			jobID := []string{"gc-a", "gc-b"}[i]
			barrier, err := b.ReserveCleanup(context.Background(), gcID, jobID, "prune", "")
			if err != nil {
				t.Fatal(err)
			}
			barrier, err = b.StartCleanup(context.Background(), barrier)
			if err != nil {
				t.Fatal(err)
			}
			completeCleanupFixture(t, b, client, barrier)
			barrier, _, err = b.WaitCleanup(context.Background(), barrier)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := b.FinishCleanup(context.Background(), barrier); err != nil {
				t.Fatal(err)
			}
		}
		b.workspace.registry.Close()
		r, err := OpenWorkspaceRegistry(path)
		if err != nil {
			t.Fatal(err)
		}
		defer r.Close()
		b.workspace.registry = r
		if _, err := b.ReserveCleanup(context.Background(), testGCID, "gc-c", "prune", ""); err == nil {
			t.Fatal("historical completed GC rebound")
		}
	})
}

func TestCleanupCancelReservationOnlyBeforeSubmission(t *testing.T) {
	b, _, a, path := cleanupRuntimeFixture(t)
	barrier, err := b.ReserveCleanup(context.Background(), testGCID, "gc-a", "prune", "")
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		barrier, err = b.CancelCleanupReservation(context.Background(), barrier)
		if err != nil || !barrier.CleanupCancelled || barrier.Phase != "released" {
			t.Fatal(barrier, err)
		}
	}
	b.workspace.registry.Close()
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	b.workspace.registry = r
	backup, err := b.Reserve(context.Background(), a, testCheckpointID, "backup-after-cancel")
	if err != nil {
		t.Fatal("cancelled pre-intent reservation froze backup", err)
	}
	backup, err = b.Abort(context.Background(), backup)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.Finish(context.Background(), backup); err != nil {
		t.Fatal(err)
	}
	barrier, err = b.ReserveCleanup(context.Background(), "gc-22345678123442348abc123456789abc", "gc-b", "prune", "")
	if err != nil {
		t.Fatal(err)
	}
	barrier, err = b.StartCleanup(context.Background(), barrier)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := b.CancelCleanupReservation(context.Background(), barrier); err == nil {
		t.Fatal("submitted cleanup cancelled as never executed")
	}
}
