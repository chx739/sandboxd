//go:build linux

package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSnapshotManifestIsReadOnlyBoundedAndHashStable(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "a.txt"), []byte("original"), 0600); err != nil {
		t.Fatal(err)
	}
	first, err := SnapshotManifest(root)
	if err != nil || len(first.Files) != 1 || first.Files[0].SHA256 != hash([]byte("original")) {
		t.Fatalf("%+v %v", first, err)
	}
	if _, err := os.Stat(filepath.Join(root, lockName)); !os.IsNotExist(err) {
		t.Fatal("manifest created lock")
	}
	if err := os.WriteFile(filepath.Join(root, lockName), nil, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, ".sandboxd-tmp-held"), []byte("unknown-write"), 0600); err != nil {
		t.Fatal(err)
	}
	second, err := SnapshotManifest(root)
	if err != nil || second.SHA256 != first.SHA256 || second.Entries != 2 {
		t.Fatalf("%+v %v", second, err)
	}
	if _, err := os.Stat(filepath.Join(root, ".sandboxd-tmp-held")); err != nil {
		t.Fatal("removed old private temp")
	}
}

func TestSnapshotManifestRejectsLinksAndTracksMutation(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "a"), []byte("one"), 0600)
	first, err := SnapshotManifest(root)
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(root, "a"), []byte("two"), 0600)
	second, err := SnapshotManifest(root)
	if err != nil || first.SHA256 == second.SHA256 {
		t.Fatalf("mutation not tracked: %v", err)
	}
	if err := os.Symlink("a", filepath.Join(root, "link")); err != nil {
		t.Fatal(err)
	}
	if _, err := SnapshotManifest(root); err == nil {
		t.Fatal("accepted symlink")
	}
}
