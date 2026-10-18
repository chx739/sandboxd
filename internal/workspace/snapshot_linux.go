//go:build linux

package workspace

import (
	"encoding/json"
	"os"
	"sort"
	"syscall"
)

// SnapshotFile describes user files, not the helper's lock or incomplete private
// temporary files. Those reserved names stay in the original workspace and are
// excluded explicitly from restic. No user path can create these names via RPC.
type SnapshotFile struct {
	Path   string `json:"path"`
	Bytes  int64  `json:"bytes"`
	Mode   uint32 `json:"mode"`
	SHA256 string `json:"sha256"`
}

type SnapshotInventory struct {
	Files   []SnapshotFile `json:"files"`
	SHA256  string         `json:"sha256"`
	Entries int            `json:"entries"`
	Bytes   int64          `json:"bytes"`
}

// SnapshotManifest is read-only; the caller must hold the workspace write
// barrier. It reuses the file RPC's descriptor traversal, quotas and link checks.
// It does not create a lock on a read-only backup mount.
func SnapshotManifest(rootPath string) (SnapshotInventory, error) {
	fd, err := syscall.Open(rootPath, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return SnapshotInventory{}, err
	}
	root := os.NewFile(uintptr(fd), "snapshot-root")
	defer root.Close()
	files, total, count, err := inventory(root)
	if err != nil {
		return SnapshotInventory{}, err
	}
	result := SnapshotInventory{Files: []SnapshotFile{}, Entries: count, Bytes: total}
	for _, file := range files {
		data, err := readPath(root, file.Path)
		if err != nil {
			return SnapshotInventory{}, err
		}
		dir, leaf, err := parent(root, file.Path, false)
		if err != nil {
			return SnapshotInventory{}, err
		}
		info, err := statAt(dir, leaf)
		dir.Close()
		if err != nil {
			return SnapshotInventory{}, err
		}
		result.Files = append(result.Files, SnapshotFile{Path: file.Path, Bytes: int64(len(data)), Mode: uint32(info.Mode().Perm()), SHA256: hash(data)})
	}
	sort.Slice(result.Files, func(i, j int) bool { return result.Files[i].Path < result.Files[j].Path })
	encoded, err := json.Marshal(result.Files)
	if err != nil {
		return SnapshotInventory{}, err
	}
	result.SHA256 = hash(encoded)
	return result, nil
}
