package sandbox

import (
	"context"
	"path/filepath"
	"strings"
	"testing"

	"github.com/chx739/sandboxd/internal/backuphelper"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func ownedJobFixture() BackupJobConfig {
	return BackupJobConfig{
		ImageRef: "sandboxd-restic:0.18.1-v3", SecretName: "restic-fixture", EndpointIP: "172.23.0.4",
		Owner: backuphelper.RepositoryOwner{ControllerID: "controller-12345678123442348abc123456789abc", RepositoryCryptoID: strings.Repeat("2", 64), OwnerSnapshotID: strings.Repeat("9", 64)},
	}
}

func freshRepositoryFixture(t *testing.T) (*WorkspaceRegistry, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "control", "workspaces.jsonl")
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	return r, path
}

func TestRepositoryBindingPersistentImmutableAndNoDowngrade(t *testing.T) {
	r, path := freshRepositoryFixture(t)
	job := ownedJobFixture()
	if err := r.bindRepository(job); err != nil {
		t.Fatal(err)
	}
	if err := r.bindRepository(job); err != nil || len(r.journal.Entries()) != 1 {
		t.Fatal("binding retry appended", err)
	}
	r.Close()
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	for _, field := range []string{"controller", "repository", "marker", "secret", "endpoint", "missing-owner", "empty"} {
		t.Run(field, func(t *testing.T) {
			other := job
			switch field {
			case "controller":
				other.Owner.ControllerID = "controller-22345678123442348abc123456789abc"
			case "repository":
				other.Owner.RepositoryCryptoID = strings.Repeat("3", 64)
			case "marker":
				other.Owner.OwnerSnapshotID = strings.Repeat("8", 64)
			case "secret":
				other.SecretName = "another-secret"
			case "endpoint":
				other.EndpointIP = "172.23.0.5"
			case "missing-owner":
				other.Owner = backuphelper.RepositoryOwner{}
			case "empty":
				other = BackupJobConfig{}
			}
			if err := r.bindRepository(other); err == nil {
				t.Fatal("changed binding accepted")
			}
		})
	}
	job.ImageRef = "sandboxd-restic:future-fixed-version"
	if err := r.bindRepository(job); err != nil || len(r.journal.Entries()) != 1 {
		t.Fatal("image upgrade changed repository binding", err)
	}
}

func TestRepositoryBindingRejectsAdoptionInvalidAndCorruptReplay(t *testing.T) {
	t.Run("old-control", func(t *testing.T) {
		r, _ := registryFixture(t)
		if err := r.bindRepository(ownedJobFixture()); err == nil {
			t.Fatal("existing workspace auto-adopted")
		}
	})
	for _, mode := range []string{"bad-owner", "bad-secret", "bad-endpoint", "duplicate", "late"} {
		t.Run(mode, func(t *testing.T) {
			r, path := freshRepositoryFixture(t)
			binding := bindingFor(ownedJobFixture())
			switch mode {
			case "bad-owner":
				binding.Owner.ControllerID = "controller-invalid"
			case "bad-secret":
				binding.SecretName = "../secret"
			case "bad-endpoint":
				binding.EndpointIP = "https://example.com"
			case "duplicate":
				if err := r.bindRepository(ownedJobFixture()); err != nil {
					t.Fatal(err)
				}
			case "late":
				if _, err := r.Register(testWorkspaceID, "session-test", "branch-main"); err != nil {
					t.Fatal(err)
				}
			}
			if _, err := r.journal.Append("repository.binding", binding); err != nil {
				t.Fatal(err)
			}
			r.Close()
			if reopened, err := OpenWorkspaceRegistry(path); err == nil {
				reopened.Close()
				t.Fatal("invalid complete event accepted")
			}
		})
	}
}

func TestOwnedBackupRuntimeUsesFixedOwnerAndChecksRepository(t *testing.T) {
	w, client, _, _ := runtimeFixture(t, false)
	r, _ := freshRepositoryFixture(t)
	w.registry = r
	job := ownedJobFixture()
	b, err := NewBackupRuntime(w, job)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.Register(testWorkspaceID, "session-test", "branch-main"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Ready(testWorkspaceID); err != nil {
		t.Fatal(err)
	}
	a := boundFixture(t, r)
	if _, err := NewBackupRuntime(w, BackupJobConfig{ImageRef: job.ImageRef, SecretName: job.SecretName, EndpointIP: job.EndpointIP}); err == nil {
		t.Fatal("runtime downgraded to unowned backup")
	}
	if _, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "legacy", "backup", a); err == nil {
		t.Fatal("legacy reservation bypassed binding")
	}
	barrier, err := b.Reserve(context.Background(), a, testCheckpointID, "owned-a")
	if err != nil {
		t.Fatal(err)
	}
	pod, err := b.expected(barrier)
	if err != nil {
		t.Fatal(err)
	}
	request, err := backuphelper.Parse(pod.Spec.Containers[0].Args)
	if err != nil || request.Owner != job.Owner || request.Operation != "backup" {
		t.Fatal("owned helper arguments not fixed", request, err)
	}
	barrier, err = b.Start(context.Background(), a, testCheckpointID, "owned-a")
	// This fake fixture has no API UID assignment; supply it before observing.
	if err == nil || barrier.Phase != "submitted" {
		t.Fatal("fixture unexpectedly assigned UID", barrier, err)
	}
	pod, err = client.CoreV1().Pods(StorageNamespace).Get(context.Background(), barrier.PodName, metav1.GetOptions{})
	if err != nil {
		t.Fatal(err)
	}
	pod.UID = "owned-fixture-uid"
	if _, err := client.CoreV1().Pods(StorageNamespace).Update(context.Background(), pod, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	completedBackupFixture(t, b, client, barrier)
	if _, result, err := b.Wait(context.Background(), barrier); err != nil || result.RepositoryCryptoID != job.Owner.RepositoryCryptoID {
		t.Fatal(result, err)
	}
	b.readLogs = func(context.Context, *corev1.Pod) ([]byte, error) {
		return []byte(`{"ok":true,"operation":"backup","checkpointId":"` + testCheckpointID + `","snapshotId":"` + strings.Repeat("1", 64) + `","repository":{"repositoryCryptoId":"` + strings.Repeat("4", 64) + `"},"manifest":{"sha256":"` + strings.Repeat("3", 64) + `"}}`), nil
	}
	if _, _, err := b.Wait(context.Background(), barrier); err == nil {
		t.Fatal("wrong repository result accepted")
	}
}

func TestOwnedRepositoryRestoreRejectsForeignSourceBeforeReservation(t *testing.T) {
	r, path := freshRepositoryFixture(t)
	job := ownedJobFixture()
	if err := r.bindRepository(job); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Register(testWorkspaceID, "session-test", "branch-main"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Ready(testWorkspaceID); err != nil {
		t.Fatal(err)
	}
	source := restoreFixtureSource()
	if _, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", job, source); err == nil {
		t.Fatal("foreign source accepted")
	}
	if _, exists := r.Barrier(testWorkspaceID); exists {
		t.Fatal("rejection persisted a reservation")
	}
	source.RepositoryCryptoID = job.Owner.RepositoryCryptoID
	b, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", job, source)
	if err != nil {
		t.Fatal(err)
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if restored, exists := r.Barrier(testWorkspaceID); !exists || restored != b {
		t.Fatal("owned restore reservation replay changed", restored)
	}
}
