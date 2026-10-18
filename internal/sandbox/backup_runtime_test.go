package sandbox

import (
	"context"
	"errors"
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

func backupRuntimeFixture(t *testing.T) (*BackupRuntime, *fake.Clientset, WorkspaceAccess) {
	t.Helper()
	w, client, a, _ := runtimeFixture(t, false)
	b, err := NewBackupRuntime(w, BackupJobConfig{ImageRef: "sandboxd-restic:0.18.1-v1", SecretName: "restic-fixture", EndpointIP: "172.23.0.4"})
	if err != nil {
		t.Fatal(err)
	}
	client.PrependReactor("create", "pods", func(action clienttesting.Action) (bool, runtime.Object, error) {
		p := action.(clienttesting.CreateAction).GetObject().(*corev1.Pod)
		p.UID = types.UID("backup-uid")
		p.ResourceVersion = "1"
		return false, nil, nil
	})
	return b, client, a
}

func completedBackupFixture(t *testing.T, b *BackupRuntime, client *fake.Clientset, barrier WorkspaceBarrier) {
	t.Helper()
	p, err := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	p.Spec.NodeName = "node-a"
	p.Status.Phase = corev1.PodSucceeded
	p.Status.ContainerStatuses = []corev1.ContainerStatus{{Name: "sandbox", ImageID: "fixed-maintenance-image-id", State: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 0}}}}
	if _, err := client.CoreV1().Pods(StorageNamespace).Update(context.Background(), p, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) {
		return []byte(`{"ok":true,"operation":"backup","checkpointId":"` + barrier.CheckpointID + `","snapshotId":"` + strings.Repeat("1", 64) + `","repository":{"repositoryCryptoId":"` + strings.Repeat("2", 64) + `"},"manifest":{"sha256":"` + strings.Repeat("3", 64) + `"}}`), nil
	}
}

func TestBackupRuntimeKeepsBarrierUntilExplicitFinish(t *testing.T) {
	b, client, a := backupRuntimeFixture(t)
	reserved, err := b.Reserve(context.Background(), a, testCheckpointID, "backup-a")
	if err != nil || reserved.Phase != "reserved" {
		t.Fatal(reserved, err)
	}
	if pods, err := client.CoreV1().Pods(StorageNamespace).List(context.Background(), metav1.ListOptions{}); err != nil || len(pods.Items) != 0 {
		t.Fatal("Reserve started execution before PREPARING", err)
	}
	if err := b.workspace.registry.WithOwner(context.Background(), a, func(WorkspaceRecord) error { t.Fatal("Reserve allowed file RPC"); return nil }); err == nil {
		t.Fatal("Reserve did not freeze source")
	}
	barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
	if err != nil {
		t.Fatal(err)
	}
	if barrier.Phase != "identified" || barrier.PodUID != "backup-uid" {
		t.Fatal(barrier)
	}
	completedBackupFixture(t, b, client, barrier)
	stopped, result, err := b.Wait(context.Background(), barrier)
	if err != nil {
		t.Fatal(err)
	}
	if result.SnapshotID != strings.Repeat("1", 64) || stopped.Phase != "stopped" {
		t.Fatal(result, stopped)
	}
	if err := b.workspace.registry.WithOwner(context.Background(), a, func(WorkspaceRecord) error { t.Fatal("uncommitted backup allowed file RPC"); return nil }); err == nil {
		t.Fatal("barrier released too soon")
	}
	if _, err := b.Finish(context.Background(), stopped); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Finish(context.Background(), stopped); err != nil {
		t.Fatal("lost Finish acknowledgement was not idempotent", err)
	}
	if err := b.workspace.registry.WithOwner(context.Background(), a, func(WorkspaceRecord) error { return nil }); err != nil {
		t.Fatal(err)
	}
}

func TestBackupRuntimeUnknownCreateNotRetriedOrReleased(t *testing.T) {
	b, client, a := backupRuntimeFixture(t)
	count := 0
	client.PrependReactor("create", "pods", func(clienttesting.Action) (bool, runtime.Object, error) {
		count++
		return true, nil, errors.New("synthetic unknown API result")
	})
	barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
	if err == nil || barrier.Phase != "submitted" {
		t.Fatal(barrier, err)
	}
	if _, err := b.Start(context.Background(), a, testCheckpointID, "backup-a"); err == nil {
		t.Fatal("missing job adopted")
	}
	if count != 1 {
		t.Fatal("unknown Create replayed", count)
	}
	if _, err := b.Abort(context.Background(), barrier); err == nil {
		t.Fatal("NotFound released unknown job")
	}
	if _, err := b.Finish(context.Background(), barrier); err == nil {
		t.Fatal("unknown job released")
	}
}

func TestBackupRuntimeChangedConfigurationAndUIDRejected(t *testing.T) {
	b, client, a := backupRuntimeFixture(t)
	barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
	if err != nil {
		t.Fatal(err)
	}
	changed := b.job
	changed.SecretName = "restic-other"
	other, err := NewBackupRuntime(b.workspace, changed)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := other.Start(context.Background(), a, testCheckpointID, "backup-a"); err == nil {
		t.Fatal("repository secret switched during retry")
	}
	p, _ := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
	p.UID = "replacement-uid"
	client.CoreV1().Pods(StorageNamespace).Update(context.Background(), p, metav1.UpdateOptions{})
	if _, _, err := b.Wait(context.Background(), barrier); err == nil {
		t.Fatal("replacement UID adopted")
	}
}

func TestBackupRuntimeTimeoutKeepsBarrierAndAbortStopsWithUID(t *testing.T) {
	b, client, a := backupRuntimeFixture(t)
	barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	if _, _, err := b.Wait(ctx, barrier); err == nil {
		t.Fatal("running backup ignored timeout")
	}
	if current, _ := b.workspace.registry.Barrier(a.WorkspaceID); current.Phase != "identified" {
		t.Fatal(current)
	}
	uidDelete := false
	p, _ := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
	p.Spec.NodeName = "node-a"
	p.Status.Phase = corev1.PodFailed
	p.Status.ContainerStatuses = []corev1.ContainerStatus{{Name: "sandbox", State: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 137}}}}
	client.PrependReactor("delete", "pods", func(action clienttesting.Action) (bool, runtime.Object, error) {
		op := action.(clienttesting.DeleteAction).GetDeleteOptions()
		uidDelete = op.Preconditions != nil && op.Preconditions.UID != nil && *op.Preconditions.UID == "backup-uid"
		// Direct tracker update avoids re-entering the fake client's reactor lock.
		return true, nil, client.Tracker().Update(corev1.SchemeGroupVersion.WithResource("pods"), p, StorageNamespace)
	})
	stopped, err := b.Abort(context.Background(), barrier)
	if err != nil || stopped.Phase != "stopped" || !uidDelete {
		t.Fatal(stopped, err, uidDelete)
	}
}

func TestBackupRuntimeFailureAndMalformedLogsNeverReturnSnapshot(t *testing.T) {
	for _, mode := range []string{"nonzero", "malformed", "wrong-checkpoint", "expired-lease"} {
		t.Run(mode, func(t *testing.T) {
			b, client, a := backupRuntimeFixture(t)
			barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
			if err != nil {
				t.Fatal(err)
			}
			completedBackupFixture(t, b, client, barrier)
			switch mode {
			case "nonzero":
				p, _ := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
				p.Status.ContainerStatuses[0].State.Terminated.ExitCode = 3
				client.CoreV1().Pods(StorageNamespace).Update(context.Background(), p, metav1.UpdateOptions{})
			case "malformed":
				b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) { return []byte(`not json`), nil }
			case "wrong-checkpoint":
				b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) {
					return []byte(`{"ok":true,"checkpointId":"other"}`), nil
				}
			case "expired-lease":
				lease, _ := client.CoordinationV1().Leases("kube-node-lease").Get(context.Background(), "node-a", metav1.GetOptions{})
				old := metav1.NewMicroTime(time.Now().Add(-time.Minute))
				lease.Spec.RenewTime = &old
				client.CoordinationV1().Leases("kube-node-lease").Update(context.Background(), lease, metav1.UpdateOptions{})
			}
			if _, result, err := b.Wait(context.Background(), barrier); err == nil || result.SnapshotID != "" {
				t.Fatal("failed backup accepted", result, err)
			}
			if got, _ := b.workspace.registry.Barrier(a.WorkspaceID); got.Phase == "released" {
				t.Fatal("failure silently unfroze workspace")
			}
		})
	}
}

func TestBackupRuntimeWaitsForTerminalPodPhase(t *testing.T) {
	b, client, a := backupRuntimeFixture(t)
	barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
	if err != nil {
		t.Fatal(err)
	}
	completedBackupFixture(t, b, client, barrier)
	p, _ := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
	p.Status.Phase = corev1.PodRunning
	client.CoreV1().Pods(StorageNamespace).Update(context.Background(), p, metav1.UpdateOptions{})
	done := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	go func() { _, _, err := b.Wait(ctx, barrier); done <- err }()
	select {
	case err := <-done:
		t.Fatal("terminated container treated as final Pod status", err)
	case <-time.After(20 * time.Millisecond):
	}
	p.Status.Phase = corev1.PodSucceeded
	if _, err := client.CoreV1().Pods(StorageNamespace).Update(context.Background(), p, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestBackupRuntimeRejectsAlteredMountAndOversizedOutput(t *testing.T) {
	for _, mode := range []string{"mount", "oversize"} {
		t.Run(mode, func(t *testing.T) {
			b, client, a := backupRuntimeFixture(t)
			barrier, err := b.Start(context.Background(), a, testCheckpointID, "backup-a")
			if err != nil {
				t.Fatal(err)
			}
			completedBackupFixture(t, b, client, barrier)
			if mode == "mount" {
				p, _ := client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
				p.Spec.Containers[0].VolumeMounts[0].ReadOnly = false
				client.CoreV1().Pods(StorageNamespace).Update(context.Background(), p, metav1.UpdateOptions{})
			} else {
				b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) { return make([]byte, 3<<20), nil }
			}
			if _, _, err := b.Wait(context.Background(), barrier); err == nil {
				t.Fatal("invalid maintenance result accepted")
			}
		})
	}
}
