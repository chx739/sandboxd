package sandbox

import (
	"encoding/json"
	"strings"
	"testing"
)

func restoreFixtureSource() RestoreSource {
	return RestoreSource{SnapshotID: strings.Repeat("1", 64), ManifestSHA256: strings.Repeat("2", 64), RepositoryCryptoID: strings.Repeat("3", 64), ImageRef: "sandboxd-workspace:runtime-v1.5", ImageID: "sha256:" + strings.Repeat("4", 64)}
}

func restoreFixtureJob() BackupJobConfig {
	return BackupJobConfig{ImageRef: "sandboxd-restic:0.18.1-v1", SecretName: "restic-fixture", EndpointIP: "172.23.0.4"}
}

func TestConfiguredRestoreDescriptorSurvivesRestartAndCannotChange(t *testing.T) {
	r, path := registryFixture(t)
	source, job := restoreFixtureSource(), restoreFixtureJob()
	b, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", job, source)
	if err != nil {
		t.Fatal(err)
	}
	if again, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", job, source); err != nil || again != b {
		t.Fatal(again, err)
	}
	changed := source
	changed.SnapshotID = strings.Repeat("5", 64)
	if _, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", job, changed); err == nil {
		t.Fatal("changed restore source accepted")
	}
	if _, err := r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", source.ImageRef); err == nil {
		t.Fatal("restore target claimed while frozen")
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if got, ok := r.Barrier(testWorkspaceID); !ok || got != b {
		t.Fatal(got, ok)
	}
	if _, err := r.completeRestore(b); err == nil {
		t.Fatal("unsubmitted restore counted as verified")
	}
}

func TestFailedRestoreQuarantinesTargetWithoutBlockingOtherWorkspaces(t *testing.T) {
	r, path := registryFixture(t)
	source, job := restoreFixtureSource(), restoreFixtureJob()
	b, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", job, source)
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.stopBarrier(b, "never-submitted")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = r.releaseBarrier(b); err != nil {
		t.Fatal(err)
	}
	if _, err = r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", source.ImageRef); err == nil {
		t.Fatal("unverified target reused")
	}
	if _, err = r.reserveRestore(testWorkspaceID, nextCheckpointID, "restore-b", job, source); err == nil {
		t.Fatal("failed target reused for another version")
	}
	if _, err = r.reserveBarrier(testWorkspaceID, nextCheckpointID, "restore-b", "restore", WorkspaceAccess{}); err == nil {
		t.Fatal("legacy path bypassed quarantine")
	}
	other := "workspace-22222222222222222222222222222222"
	if _, err = r.Register(other, "session-test", "branch-other"); err != nil {
		t.Fatal(err)
	}
	if _, err = r.Ready(other); err != nil {
		t.Fatal(err)
	}
	if _, err = r.reserveRestore(other, nextCheckpointID, "restore-b", job, source); err != nil {
		t.Fatal("failed released job blocked independent target", err)
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if _, err = r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", source.ImageRef); err == nil {
		t.Fatal("quarantine lost on restart")
	}
}

func TestVerifiedRestorePinsBusinessImageAndReplaysMetadata(t *testing.T) {
	r, path := registryFixture(t)
	source := restoreFixtureSource()
	b, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", restoreFixtureJob(), source)
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.submitBarrier(b)
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.identifyBarrier(b, "restore-pod-uid")
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.stopBarrier(b, "containers-terminated")
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.completeRestore(b)
	if err != nil || !b.RestoreVerified || b.Phase != "released" {
		t.Fatal(b, err)
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if _, err = r.BeginClaim(testWorkspaceID, "task-restored", "attempt-a", "pod-a", "other-image"); err == nil {
		t.Fatal("restored workspace selected another image")
	}
	record, err := r.BeginClaim(testWorkspaceID, "task-restored", "attempt-a", "pod-a", source.ImageRef)
	if err != nil || record.ImageID != source.ImageID || record.RestoredFrom != source {
		t.Fatal(record, err)
	}
	a := accessFor(record)
	if _, err = r.CreationSubmitted(a); err != nil {
		t.Fatal(err)
	}
	if _, err = r.Bind(a, "uid-a", "node-a", "sha256:"+strings.Repeat("9", 64)); err == nil {
		t.Fatal("actual changed image accepted")
	}
	if _, err = r.Bind(a, "uid-a", "node-a", source.ImageID); err != nil {
		t.Fatal(err)
	}
}

func TestRestoreSourceValidationAndLegacyJSONEncoding(t *testing.T) {
	source := restoreFixtureSource()
	if !source.valid() {
		t.Fatal("valid fixture rejected")
	}
	for _, change := range []func(*RestoreSource){
		func(s *RestoreSource) { s.SnapshotID = "latest" },
		func(s *RestoreSource) { s.ManifestSHA256 = "short" },
		func(s *RestoreSource) { s.RepositoryCryptoID = "s3:untrusted" },
		func(s *RestoreSource) { s.ImageID = "mutable-tag" },
		func(s *RestoreSource) { s.ImageRef = "image\nextra" },
	} {
		bad := source
		change(&bad)
		if bad.valid() {
			t.Fatal("bad restore descriptor accepted")
		}
	}
	for _, value := range []any{WorkspaceRecord{}, WorkspaceBarrier{}} {
		raw, err := json.Marshal(value)
		if err != nil || strings.Contains(string(raw), "restoredFrom") || strings.Contains(string(raw), "restoreSource") || strings.Contains(string(raw), "restoreVerified") {
			t.Fatal(string(raw), err)
		}
	}
}

func TestRestorePinPublicationCrashWindowStaysFrozen(t *testing.T) {
	r, path := registryFixture(t)
	source := restoreFixtureSource()
	b, err := r.reserveRestore(testWorkspaceID, testCheckpointID, "restore-a", restoreFixtureJob(), source)
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.submitBarrier(b)
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.identifyBarrier(b, "restore-pod-uid")
	if err != nil {
		t.Fatal(err)
	}
	b, err = r.stopBarrier(b, "containers-terminated")
	if err != nil {
		t.Fatal(err)
	}
	// 构造completeRestore两次fsync之间的合法持久前缀；不是实际进程SIGKILL。
	record, err := r.Get(testWorkspaceID)
	if err != nil {
		t.Fatal(err)
	}
	record.RestoredFrom = source
	if _, err = r.save(record); err != nil {
		t.Fatal(err)
	}
	r.Close()
	r, err = OpenWorkspaceRegistry(path)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Close()
	if _, err = r.BeginClaim(testWorkspaceID, "task-a", "attempt-a", "pod-a", source.ImageRef); err == nil {
		t.Fatal("pin publication prematurely unblocked target")
	}
	if done, err := r.completeRestore(b); err != nil || !done.RestoreVerified {
		t.Fatal(done, err)
	}
}
