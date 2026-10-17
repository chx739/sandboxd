package sandbox

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"
)

func registryFixture(t *testing.T) (*WorkspaceRegistry, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "control", "workspaces.jsonl")
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	if _, err := r.Register(testWorkspaceID, "session-test", "branch-main"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Ready(testWorkspaceID); err != nil {
		t.Fatal(err)
	}
	return r, path
}

func accessFor(record WorkspaceRecord) WorkspaceAccess {
	return WorkspaceAccess{WorkspaceID: record.WorkspaceID, TaskID: record.Owner.TaskID, AttemptID: record.Owner.AttemptID, SandboxID: record.Owner.SandboxID, Generation: record.Generation}
}

func boundFixture(t *testing.T, r *WorkspaceRegistry) WorkspaceAccess {
	t.Helper()
	record, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", "fixed@sha256:fixture")
	if err != nil {
		t.Fatal(err)
	}
	access := accessFor(record)
	if _, err := r.CreationSubmitted(access); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Bind(access, "uid-a", "node-a", "containerd-image-id-a"); err != nil {
		t.Fatal(err)
	}
	return access
}

func releaseFixture(t *testing.T, r *WorkspaceRegistry, access WorkspaceAccess) {
	t.Helper()
	if _, err := r.StartStop(access); err != nil {
		t.Fatal(err)
	}
	if _, err := r.ConfirmStopped(access, "uid-a", "containers-terminated"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Released(access); err != nil {
		t.Fatal(err)
	}
}

func TestWorkspaceRegistryLifecycleAndRestart(t *testing.T) {
	r, path := registryFixture(t)
	old := boundFixture(t, r)
	if err := r.WithOwner(context.Background(), old, func(record WorkspaceRecord) error { return nil }); err != nil {
		t.Fatal(err)
	}
	r.Close()
	r, err := OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if _, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture"); err == nil {
		t.Fatal("active owner replaced")
	}
	releaseFixture(t, r, old)
	record, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture")
	if err != nil {
		t.Fatal(err)
	}
	if record.Generation != 2 || record.WorkspaceID != old.WorkspaceID {
		t.Fatal(record)
	}
	if err := r.WithOwner(context.Background(), old, func(record WorkspaceRecord) error { t.Fatal("stale writer ran"); return nil }); !errors.Is(err, ErrWorkspaceConflict) {
		t.Fatal(err)
	}
	current := accessFor(record)
	r.CreationSubmitted(current)
	if _, err := r.Bind(current, "uid-b", "node-a", "containerd-image-id-a"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.StartStop(old); !errors.Is(err, ErrWorkspaceConflict) {
		t.Fatal("old cleanup could affect replacement", err)
	}
}

func TestWorkspaceRegistryScopeAndUnreadyClaims(t *testing.T) {
	r, _ := registryFixture(t)
	if _, err := r.Register(testWorkspaceID, "session-other", "branch-main"); err == nil {
		t.Fatal("scope takeover")
	}
	if _, err := r.BeginClaim("workspace-ffffffffffffffffffffffffffffffff", "task-a", "attempt-a", "pod-a", "fixed"); err == nil {
		t.Fatal("unregistered workspace")
	}
	other := "workspace-11111111111111111111111111111111"
	r.Register(other, "session-test", "branch-other")
	if _, err := r.BeginClaim(other, "task-a", "attempt-a", "pod-a", "fixed"); err == nil {
		t.Fatal("uninitialized directory claimed")
	}
	if _, err := r.Register("../escape", "session-test", "branch-main"); err == nil {
		t.Fatal("bad workspace identity")
	}
}

func TestWorkspaceRegistryRequiresPositiveStopEvidence(t *testing.T) {
	r, _ := registryFixture(t)
	access := boundFixture(t, r)
	if _, err := r.Released(access); err == nil {
		t.Fatal("released active owner")
	}
	r.StartStop(access)
	for _, reason := range []string{"lease-expired", "pod-object-deleted", "never-submitted"} {
		if _, err := r.ConfirmStopped(access, "uid-a", reason); err == nil {
			t.Fatal("false stop proof accepted", reason)
		}
	}
	if _, err := r.ConfirmStopped(access, "uid-other", "containers-terminated"); err == nil {
		t.Fatal("different pod UID accepted")
	}
	if _, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture"); err == nil {
		t.Fatal("uncertain writer replaced")
	}
}

func TestWorkspaceCreateOutcomeUnknownIsNotNeverCreated(t *testing.T) {
	r, _ := registryFixture(t)
	record, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", "fixed")
	if err != nil {
		t.Fatal(err)
	}
	access := accessFor(record)
	r.CreationSubmitted(access)
	r.StartStop(access)
	if _, err := r.ConfirmStopped(access, "", "never-submitted"); err == nil {
		t.Fatal("uncertain API create treated as never submitted")
	}
	if _, err := r.Released(access); err == nil {
		t.Fatal("uncertain owner released")
	}
}

func TestWorkspaceAbortedBeforeCreateCanRelease(t *testing.T) {
	r, _ := registryFixture(t)
	record, _ := r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", "fixed")
	access := accessFor(record)
	r.StartStop(access)
	if _, err := r.ConfirmStopped(access, "", "never-submitted"); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Released(access); err != nil {
		t.Fatal(err)
	}
	if _, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", "fixed"); err == nil {
		t.Fatal("same released attempt revived")
	}
}

func TestWorkspaceRPCIsSerializedAgainstRelease(t *testing.T) {
	r, _ := registryFixture(t)
	access := boundFixture(t, r)
	entered, done := make(chan struct{}), make(chan struct{})
	finished := make(chan error, 1)
	go func() {
		finished <- r.WithOwner(context.Background(), access, func(record WorkspaceRecord) error { close(entered); <-done; return nil })
	}()
	<-entered
	stopped := make(chan error, 1)
	go func() { _, err := r.StartStop(access); stopped <- err }()
	select {
	case <-stopped:
		t.Fatal("owner changed while file RPC was running")
	case <-time.After(20 * time.Millisecond):
	}
	close(done)
	if err := <-finished; err != nil {
		t.Fatal(err)
	}
	if err := <-stopped; err != nil {
		t.Fatal(err)
	}
	if err := r.WithOwner(context.Background(), access, func(record WorkspaceRecord) error { t.Fatal("stopping owner executed file RPC"); return nil }); err == nil {
		t.Fatal("stopping owner authorized")
	}
}

func TestAutomaticRecoveryCannotChangeImageIdentity(t *testing.T) {
	r, _ := registryFixture(t)
	access := boundFixture(t, r)
	releaseFixture(t, r, access)
	if _, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "different-image"); err == nil {
		t.Fatal("changed automatic recovery image ref")
	}
	record, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture")
	if err != nil {
		t.Fatal(err)
	}
	current := accessFor(record)
	r.CreationSubmitted(current)
	if _, err := r.Bind(current, "uid-b", "node-a", "different-image-id"); err == nil {
		t.Fatal("mutable tag changed recovery image")
	}
}
