//go:build linux

package control

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRoundTripAndExclusiveOwner(t *testing.T) {
	path := filepath.Join(t.TempDir(), "private", "workspaces.jsonl")
	j, err := OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := OpenJournal(path); err == nil {
		t.Fatal("duplicate owner allowed")
	}
	entry, err := j.Append("workspace.state", map[string]string{"workspaceId": "demo"})
	if err != nil {
		t.Fatal(err)
	}
	entry.Data[0] = 'x'
	j.Entries()[0].Data[0] = 'y'
	if !json.Valid(j.Entries()[0].Data) {
		t.Fatal("mutable projection")
	}
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}
	j, err = OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	defer j.Close()
	if len(j.Entries()) != 1 {
		t.Fatal("record lost")
	}
	entry, err = j.Append("workspace.bound", map[string]int{"generation": 2})
	if err != nil || entry.Seq != 2 {
		t.Fatal(entry, err)
	}
	info, _ := os.Stat(path)
	if info.Mode().Perm() != 0600 {
		t.Fatal(info.Mode())
	}
}

func TestTruncatedTailAndCompleteCorruption(t *testing.T) {
	path := filepath.Join(t.TempDir(), "journal")
	j, err := OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	j.Append("workspace.state", map[string]int{"generation": 1})
	j.Close()
	f, _ := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0600)
	f.WriteString(`{"seq":2`)
	f.Close()
	j, err = OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(j.Entries()) != 1 {
		t.Fatal("partial record published")
	}
	if _, err = j.Append("workspace.bound", map[string]int{"generation": 2}); err != nil {
		t.Fatal(err)
	}
	j.Close()
	data, _ := os.ReadFile(path)
	data = []byte(strings.Replace(string(data), `"generation":1`, `"generation":9`, 1))
	os.WriteFile(path, data, 0600)
	for range 2 {
		if j, err := OpenJournal(path); err == nil {
			j.Close()
			t.Fatal("complete corruption silently skipped")
		}
	}
}

func TestInvalidPayloadAndUnsafeFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "journal")
	j, err := OpenJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []any{nil, []string{"bad"}, "text", map[string]string{"huge": strings.Repeat("x", maxRecordBytes)}} {
		if _, err := j.Append("workspace.state", value); err == nil {
			t.Fatal("invalid payload accepted")
		}
	}
	if _, err := j.Append("bad event", map[string]int{}); err == nil {
		t.Fatal("bad type accepted")
	}
	j.Close()
	if err := os.Symlink(path, filepath.Join(dir, "link")); err != nil {
		t.Fatal(err)
	}
	if j, err := OpenJournal(filepath.Join(dir, "link")); err == nil {
		j.Close()
		t.Fatal("symlink allowed")
	}
	if err := os.Link(path, filepath.Join(dir, "hardlink")); err != nil {
		t.Fatal(err)
	}
	if j, err := OpenJournal(path); err == nil {
		j.Close()
		t.Fatal("hardlink allowed")
	}
}

func TestAppendFailurePoisonsWriter(t *testing.T) {
	j, err := OpenJournal(filepath.Join(t.TempDir(), "journal"))
	if err != nil {
		t.Fatal(err)
	}
	j.file.Close() // Inject a failed descriptor without making a fake successful fsync.
	if _, err := j.Append("workspace.state", map[string]int{}); err == nil {
		t.Fatal("expected write failure")
	}
	if !j.poisoned {
		t.Fatal("writer was not poisoned")
	}
	if _, err := j.Append("workspace.state", map[string]int{}); err == nil {
		t.Fatal("poisoned writer reused")
	}
	j.Close()
}
