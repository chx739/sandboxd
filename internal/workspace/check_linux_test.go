//go:build linux

package workspace

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStorageCheckPreservesBusinessFilesAndCleansProbe(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, "report"), []byte("preserve"), 0600)
	if err := Check(root); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(root, "report"))
	if string(data) != "preserve" {
		t.Fatal("business file modified")
	}
	items, _ := os.ReadDir(root)
	if len(items) != 2 {
		t.Fatal("probe artifact not cleaned")
	}
}
