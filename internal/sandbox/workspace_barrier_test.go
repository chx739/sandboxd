package sandbox

import (
	"context"
	"errors"
	"testing"
	"time"
)

const testCheckpointID = "cp-12345678123442348abc123456789abc"
const nextCheckpointID = "cp-22345678123442348abc123456789abc"

func reserveFixture(t *testing.T, r *WorkspaceRegistry, a WorkspaceAccess) WorkspaceBarrier {
	t.Helper()
	b, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "backup-a", "backup", a)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestBarrierSurvivesRestartAndRejectsFilesAndClaims(t *testing.T) {
	r, path := registryFixture(t)
	a := boundFixture(t, r)
	b := reserveFixture(t, r, a)
	r.Close()
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if got, ok := r.Barrier(a.WorkspaceID); !ok || got != b {
		t.Fatal(got, ok)
	}
	if err := r.WithOwner(context.Background(), a, func(WorkspaceRecord) error { t.Fatal("file RPC passed persistent barrier"); return nil }); !errors.Is(err, ErrWorkspaceConflict) {
		t.Fatal(err)
	}
	if _, err := r.BeginClaim(a.WorkspaceID, a.TaskID, a.AttemptID, a.SandboxID, "fixed@sha256:fixture"); err == nil {
		t.Fatal("idempotent claim bypassed barrier")
	}
	// Cancelling the business owner must remain possible while backup is frozen.
	releaseFixture(t, r, a)
	if _, err := r.BeginClaim(a.WorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture"); err == nil {
		t.Fatal("replacement bypassed persistent barrier")
	}
}

func TestBarrierSubmittedUnknownCannotBeReleased(t *testing.T) {
	r, path := registryFixture(t)
	a := boundFixture(t, r)
	b, err := r.submitBarrier(reserveFixture(t, r, a))
	if err != nil {
		t.Fatal(err)
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	for _, reason := range []string{"never-submitted", "pod-object-deleted", "lease-expired", "containers-terminated"} {
		if _, err := r.stopBarrier(b, reason); err == nil {
			t.Fatal("unknown maintenance pod released", reason)
		}
	}
	if _, err := r.releaseBarrier(b); err == nil {
		t.Fatal("unknown job released")
	}
	b, err = r.identifyBarrier(b, "maintenance-uid")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.identifyBarrier(b, "replacement-uid"); err == nil {
		t.Fatal("maintenance pod UID changed")
	}
	b, err = r.stopBarrier(b, "containers-terminated")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.releaseBarrier(b); err != nil {
		t.Fatal(err)
	}
	if err := r.WithOwner(context.Background(), a, func(WorkspaceRecord) error { return nil }); err != nil {
		t.Fatal(err)
	}
}

func TestBarrierIdempotencyAndStaleCleanup(t *testing.T) {
	r, _ := registryFixture(t)
	a := boundFixture(t, r)
	b := reserveFixture(t, r, a)
	submitted, err := r.submitBarrier(b)
	if err != nil {
		t.Fatal(err)
	}
	if got := reserveFixture(t, r, a); got != submitted {
		t.Fatal("retry reset submitted status")
	}
	if _, err := r.stopBarrier(b, "never-submitted"); err == nil {
		t.Fatal("stale reservation cancelled submitted pod")
	}
	b, err = r.identifyBarrier(submitted, "maintenance-uid")
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.stopBarrier(b, "containers-terminated")
	if err != nil {
		t.Fatal(err)
	}
	old, err := r.releaseBarrier(b)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "backup-b", "backup", a); err == nil {
		t.Fatal("released checkpoint reused without checkpoint reconciliation")
	}
	current, err := r.reserveBarrier(a.WorkspaceID, nextCheckpointID, "backup-b", "backup", a)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.releaseBarrier(old); err == nil {
		t.Fatal("stale cleanup released new barrier")
	}
	if got, _ := r.Barrier(a.WorkspaceID); got != current {
		t.Fatal(got)
	}
}

func TestBarrierRestoreOnlyNeverClaimedWorkspace(t *testing.T) {
	r, path := registryFixture(t)
	b, err := r.reserveBarrier(testWorkspaceID, testCheckpointID, "restore-a", "restore", WorkspaceAccess{})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", "fixed"); err == nil {
		t.Fatal("claimed restore target during barrier")
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	b, err = r.stopBarrier(b, "never-submitted")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := r.releaseBarrier(b); err != nil {
		t.Fatal(err)
	}
	a := boundFixture(t, r)
	releaseFixture(t, r, a)
	if _, err := r.reserveBarrier(testWorkspaceID, nextCheckpointID, "restore-b", "restore", WorkspaceAccess{}); err == nil {
		t.Fatal("old released workspace accepted as empty restore target")
	}
}

func TestBarrierWaitsForInflightFileRPC(t *testing.T) {
	r, _ := registryFixture(t)
	a := boundFixture(t, r)
	entered, unblock, finished := make(chan struct{}), make(chan struct{}), make(chan error, 1)
	go func() {
		finished <- r.WithOwner(context.Background(), a, func(WorkspaceRecord) error { close(entered); <-unblock; return nil })
	}()
	<-entered
	reserved := make(chan error, 1)
	go func() {
		_, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "backup-a", "backup", a)
		reserved <- err
	}()
	select {
	case err := <-reserved:
		t.Fatal("barrier passed running RPC", err)
	case <-time.After(20 * time.Millisecond):
	}
	close(unblock)
	if err := <-finished; err != nil {
		t.Fatal(err)
	}
	if err := <-reserved; err != nil {
		t.Fatal(err)
	}
}

func TestBarrierInvalidIdentitiesAndScope(t *testing.T) {
	r, _ := registryFixture(t)
	a := boundFixture(t, r)
	for _, id := range []string{"../escape", "latest", "cp-12345678123412348abc123456789abc"} {
		if _, err := r.reserveBarrier(a.WorkspaceID, id, "backup-a", "backup", a); err == nil {
			t.Fatal("bad checkpoint accepted", id)
		}
	}
	other := a
	other.AttemptID = "attempt-other"
	if _, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "backup-a", "backup", other); err == nil {
		t.Fatal("wrong owner froze workspace")
	}
	if _, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "backup-a", "shell", a); err == nil {
		t.Fatal("invalid maintenance operation")
	}
}

func TestBarrierReplayRejectsIllegalStateEvenWithValidChecksum(t *testing.T) {
	r, path := registryFixture(t)
	a := boundFixture(t, r)
	b := reserveFixture(t, r, a)
	b.Phase = "released"
	b.StopReason = "never-submitted"
	if _, err := r.journal.Append("workspace.barrier", b); err != nil {
		t.Fatal(err)
	}
	r.Close()
	if reopened, err := OpenWorkspaceRegistry(path); err == nil {
		reopened.Close()
		t.Fatal("illegal barrier transition replayed")
	}
}

func TestBarrierFailedFsyncDoesNotPublishReservation(t *testing.T) {
	r, _ := registryFixture(t)
	a := boundFixture(t, r)
	r.Close()
	if _, err := r.reserveBarrier(a.WorkspaceID, testCheckpointID, "backup-a", "backup", a); err == nil {
		t.Fatal("closed journal accepted reservation")
	}
	if _, ok := r.Barrier(a.WorkspaceID); ok {
		t.Fatal("failed append published barrier")
	}
}
