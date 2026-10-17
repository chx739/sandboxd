//go:build linux

package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInitializePrivateWorkspaceIsIdempotent(t *testing.T) {
	root := t.TempDir()
	id := "workspace-0123456789abcdef0123456789abcdef"
	result, err := Initialize(root, id)
	if err != nil {
		t.Fatal(err)
	}
	if result["created"] != true {
		t.Fatal(result)
	}
	os.WriteFile(filepath.Join(root, id, "keep"), []byte("existing business file"), 0600)
	result, err = Initialize(root, id)
	if err != nil || result["created"] != false {
		t.Fatal(result, err)
	}
	data, _ := os.ReadFile(filepath.Join(root, id, "keep"))
	if string(data) != "existing business file" {
		t.Fatal("initialization modified existing data")
	}
}

func TestInitializeRejectsPathsLinksAndUnexpectedPermissions(t *testing.T) {
	root := t.TempDir()
	for _, id := range []string{"../escape", "workspace-demo", "/absolute", "workspace-00000000000000000000000000000001/child"} {
		if _, err := Initialize(root, id); err == nil {
			t.Fatal("unsafe identity accepted")
		}
	}
	id := "workspace-0123456789abcdef0123456789abcdef"
	outside := t.TempDir()
	os.Symlink(outside, filepath.Join(root, id))
	if _, err := Initialize(root, id); err == nil {
		t.Fatal("workspace symlink accepted")
	}
	os.Remove(filepath.Join(root, id))
	os.Mkdir(filepath.Join(root, id), 0755)
	if _, err := Initialize(root, id); err == nil {
		t.Fatal("unexpected permissions silently repaired")
	}
	info, _ := os.Stat(filepath.Join(root, id))
	if info.Mode().Perm() != 0755 {
		t.Fatal("changed an existing directory")
	}
}
