package sandbox

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

const restoreTarget = "workspace-22222222222222222222222222222222"

func restoreRuntimeFixture(t *testing.T) (*BackupRuntime, *fake.Clientset, RestoreSource) {
	b, client, _ := backupRuntimeFixture(t)
	if _, err := b.workspace.registry.Register(restoreTarget, "session-test", "branch-restored"); err != nil {
		t.Fatal(err)
	}
	if _, err := b.workspace.registry.Ready(restoreTarget); err != nil {
		t.Fatal(err)
	}
	source := restoreFixtureSource()
	source.ImageRef = b.workspace.image
	return b, client, source
}

func completedRestoreFixture(t *testing.T, b *BackupRuntime, client *fake.Clientset, barrier WorkspaceBarrier, source RestoreSource) {
	completedBackupFixture(t, b, client, barrier)
	b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) {
		return json.Marshal(map[string]any{"ok": true, "operation": "restore", "snapshotId": source.SnapshotID,
			"repository": map[string]string{"repositoryCryptoId": source.RepositoryCryptoID}, "manifest": map[string]string{"sha256": source.ManifestSHA256}})
	}
}

func TestRestoreRuntimeVerifiesResultBeforeUnfreezing(t *testing.T) {
	b, client, source := restoreRuntimeFixture(t)
	ctx := context.Background()
	reserved, err := b.ReserveRestore(ctx, restoreTarget, testCheckpointID, "restore-a", source)
	if err != nil || reserved.Phase != "reserved" {
		t.Fatal(reserved, err)
	}
	if pods, _ := client.CoreV1().Pods(StorageNamespace).List(ctx, metav1.ListOptions{}); len(pods.Items) != 0 {
		t.Fatal("reserve started Pod")
	}
	barrier, err := b.StartRestore(ctx, restoreTarget, testCheckpointID, "restore-a", source)
	if err != nil {
		t.Fatal(err)
	}
	pod, err := client.CoreV1().Pods(StorageNamespace).Get(ctx, barrier.PodName, metav1.GetOptions{})
	if err != nil || len(pod.Spec.Containers[0].Args) != 4 || pod.Spec.Containers[0].Args[3] != source.RepositoryCryptoID || pod.Spec.Containers[0].VolumeMounts[0].ReadOnly {
		t.Fatal(pod, err)
	}
	completedRestoreFixture(t, b, client, barrier, source)
	stopped, result, err := b.Wait(ctx, barrier)
	if err != nil || result.SnapshotID != source.SnapshotID || stopped.RestoreVerified {
		t.Fatal(stopped, result, err)
	}
	if _, err = b.workspace.registry.BeginClaim(restoreTarget, "task-restored", "attempt-a", "pod-restored", source.ImageRef); err == nil {
		t.Fatal("Wait prematurely released target")
	}
	done, err := b.FinishRestore(ctx, stopped, true)
	if err != nil || !done.RestoreVerified || done.Phase != "released" {
		t.Fatal(done, err)
	}
	if _, err = b.FinishRestore(ctx, stopped, true); err != nil {
		t.Fatal("Finish acknowledgement lost", err)
	}
	record, err := b.workspace.registry.BeginClaim(restoreTarget, "task-restored", "attempt-a", "pod-restored", source.ImageRef)
	if err != nil || record.ImageID != source.ImageID {
		t.Fatal(record, err)
	}
}

func TestRestoreRuntimeMismatchCannotPromoteAndFailedTargetStaysIsolated(t *testing.T) {
	for _, field := range []string{"repo", "snapshot", "manifest"} {
		t.Run(field, func(t *testing.T) {
			b, client, source := restoreRuntimeFixture(t)
			ctx := context.Background()
			barrier, err := b.StartRestore(ctx, restoreTarget, testCheckpointID, "restore-a", source)
			if err != nil {
				t.Fatal(err)
			}
			bad := source
			switch field {
			case "repo":
				bad.RepositoryCryptoID = strings.Repeat("9", 64)
			case "snapshot":
				bad.SnapshotID = strings.Repeat("9", 64)
			case "manifest":
				bad.ManifestSHA256 = strings.Repeat("9", 64)
			}
			completedRestoreFixture(t, b, client, barrier, bad)
			if _, _, err = b.Wait(ctx, barrier); err == nil {
				t.Fatal("mismatch accepted")
			}
			if _, err = b.FinishRestore(ctx, barrier, true); err == nil {
				t.Fatal("caller self-report bypassed result validation")
			}
			stopped, err := b.Abort(ctx, barrier)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = b.FinishRestore(ctx, stopped, false); err != nil {
				t.Fatal(err)
			}
			if _, err = b.workspace.registry.BeginClaim(restoreTarget, "task-a", "attempt-a", "pod-a", source.ImageRef); err == nil {
				t.Fatal("failed target claimed")
			}
		})
	}
}

func TestRestoreRuntimeDurableVerificationSurvivesPodCleanup(t *testing.T) {
	b, client, source := restoreRuntimeFixture(t)
	ctx := context.Background()
	barrier, err := b.StartRestore(ctx, restoreTarget, testCheckpointID, "restore-a", source)
	if err != nil {
		t.Fatal(err)
	}
	completedRestoreFixture(t, b, client, barrier, source)
	stopped, _, err := b.Wait(ctx, barrier)
	if err != nil {
		t.Fatal(err)
	}
	proof, err := b.workspace.registry.verifyRestore(stopped)
	if err != nil {
		t.Fatal(err)
	}
	// 合成清理后/元数据提交前窗口；真实进程恢复另行验收。
	if err = client.CoreV1().Pods(StorageNamespace).Delete(ctx, barrier.PodName, metav1.DeleteOptions{}); err != nil {
		t.Fatal(err)
	}
	b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) {
		t.Fatal("durable proof tried to reread deleted Pod")
		return nil, nil
	}
	if done, err := b.FinishRestore(ctx, proof, true); err != nil || !done.RestoreVerified {
		t.Fatal(done, err)
	}
}

func TestRestoreRuntimeCannotChangeConfiguredBusinessImage(t *testing.T) {
	b, _, source := restoreRuntimeFixture(t)
	source.ImageRef = "another-image"
	if _, err := b.ReserveRestore(context.Background(), restoreTarget, testCheckpointID, "restore-a", source); err == nil {
		t.Fatal("changed business image accepted")
	}
}
