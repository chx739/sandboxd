package backuphelper

import "testing"

const testCheckpoint = "cp-123456789abc4def8123456789abcdef"

func TestFixedMaintenanceArguments(t *testing.T) {
	for _, args := range [][]string{{"manifest"}, {"init"}, {"backup", testCheckpoint}, {"restore", string64("a"), string64("b")}, {"snapshots"}, {"check"}, {"stats"}, {"config"}} {
		r, err := Parse(args)
		if err != nil {
			t.Fatal(args, err)
		}
		got, err := FixedArgs(r)
		if err != nil || len(got) != len(args) {
			t.Fatal(got, err)
		}
	}
	for _, args := range [][]string{{}, {"unlock"}, {"forget", string64("a")}, {"backup", "../../x"}, {"backup", "cp-00000000000000000000000000000000"}, {"restore", "latest", string64("a")}, {"manifest", "/host"}, {"restore", string64("a"), "invalid"}, {"init", "--repo", "s3:other"}} {
		if _, err := Parse(args); err == nil {
			t.Fatalf("accepted %v", args)
		}
	}
	if _, err := FixedArgs(Request{Operation: "manifest", SnapshotID: string64("a")}); err == nil {
		t.Fatal("unused fields accepted")
	}
}

func string64(char string) string {
	result := ""
	for range 64 {
		result += char
	}
	return result
}

func TestSnapshotTagAndBoundedOutput(t *testing.T) {
	snapshot := map[string]any{"tags": []any{"checkpoint:" + testCheckpoint, "manifest:" + string64("a")}}
	if !hasTag(snapshot, "checkpoint:"+testCheckpoint) || hasTag(snapshot, "checkpoint:other") {
		t.Fatal("tag mismatch")
	}
	var b bounded
	if _, err := b.Write(make([]byte, MaxOutput)); err != nil {
		t.Fatal(err)
	}
	if _, err := b.Write([]byte{1}); err == nil || !b.overflow || b.Len() != MaxOutput {
		t.Fatal("output not bounded")
	}
}

func TestRepositoryBoundRestoreContract(t *testing.T) {
	r := Request{Operation: "restore", SnapshotID: string64("a"), ManifestSHA256: string64("b"), RepositoryCryptoID: string64("c")}
	args, err := FixedArgs(r)
	if err != nil || len(args) != 4 || args[3] != r.RepositoryCryptoID {
		t.Fatal(args, err)
	}
	parsed, err := Parse(args)
	if err != nil || parsed != r {
		t.Fatal(parsed, err)
	}
	if _, err = Parse([]string{"restore", r.SnapshotID, r.ManifestSHA256, "s3:other"}); err == nil {
		t.Fatal("accepted arbitrary repository")
	}
	if err = checkRestoreRepository(map[string]any{"repositoryCryptoId": r.RepositoryCryptoID}, r.RepositoryCryptoID); err != nil {
		t.Fatal(err)
	}
	if err = checkRestoreRepository(map[string]any{"repositoryCryptoId": string64("d")}, r.RepositoryCryptoID); err == nil {
		t.Fatal("accepted another repository")
	}
	if err = checkRestoreRepository(map[string]any{}, r.RepositoryCryptoID); err == nil {
		t.Fatal("accepted missing identity")
	}
	// 旧三参数管理组件保留兼容；可信恢复Runtime必须传非空完整身份。
	if err = checkRestoreRepository(map[string]any{}, ""); err != nil {
		t.Fatal(err)
	}
}
