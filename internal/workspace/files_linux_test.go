//go:build linux

package workspace

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
)

func pointer(s string) *string { return &s }

func mustRun(t *testing.T, root string, req Request) map[string]any {
	t.Helper()
	r, err := Execute(root, req)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func TestWriteReadEditAndCAS(t *testing.T) {
	root := t.TempDir()
	r := mustRun(t, root, Request{Operation: "write_file", Path: "notes/report.md", Content: "Pod 未就绪\n"})
	if r["created"] != true || r["oldSha256"] != nil {
		t.Fatal(r)
	}
	sha := r["newSha256"].(string)
	r = mustRun(t, root, Request{Operation: "read_file", Path: "notes/report.md", Offset: 4, Limit: 3})
	if r["content"] != "未就绪" || r["sha256"] != sha {
		t.Fatal(r)
	}
	for _, expected := range []*string{nil, pointer(strings.Repeat("0", 64))} {
		if _, err := Execute(root, Request{Operation: "write_file", Path: "notes/report.md", Content: "overwrite", ExpectedSHA256: expected}); err == nil {
			t.Fatal("CAS bypass")
		}
	}
	r = mustRun(t, root, Request{Operation: "edit_file", Path: "notes/report.md", OldText: "未就绪", NewText: "已就绪", ExpectedSHA256: &sha})
	if r["newSha256"] == sha || !strings.Contains(r["diff"].(string), "+Pod 已就绪") {
		t.Fatal(r)
	}
	if _, err := Execute(root, Request{Operation: "edit_file", Path: "notes/report.md", OldText: "Pod", NewText: "X", ExpectedSHA256: &sha}); err == nil {
		t.Fatal("stale edit")
	}
	info, _ := os.Stat(filepath.Join(root, "notes/report.md"))
	if info.Mode().Perm() != 0600 {
		t.Fatal(info.Mode())
	}
}

func TestPathsAndSymlinks(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	os.WriteFile(filepath.Join(outside, "secret"), []byte("untouched"), 0600)
	for _, path := range []string{"", "/etc/passwd", "../secret", "a/../b", "a//b", "a/./b", "a/", "a\\b", "a\x00b", strings.Repeat("x", 513), "a/b/c/d/e/f/g/h/i", ".sandboxd-lock"} {
		if _, err := Execute(root, Request{Operation: "write_file", Path: path, Content: "bad"}); err == nil {
			t.Fatalf("allowed %q", path)
		}
	}
	os.Symlink(outside, filepath.Join(root, "link"))
	for _, op := range []string{"read_file", "write_file", "list_files", "search_files"} {
		if _, err := Execute(root, Request{Operation: op, Path: "link/secret", Query: "untouched", Content: "bad"}); err == nil {
			t.Fatal("symlink allowed", op)
		}
	}
	data, _ := os.ReadFile(filepath.Join(outside, "secret"))
	if string(data) != "untouched" {
		t.Fatal("escaped")
	}
}

func TestSpecialFilesDoNotBlock(t *testing.T) {
	for _, kind := range []string{"fifo", "symlink", "hardlink"} {
		t.Run(kind, func(t *testing.T) {
			root := t.TempDir()
			name := filepath.Join(root, "special")
			switch kind {
			case "fifo":
				if err := syscall.Mkfifo(name, 0600); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				os.WriteFile(filepath.Join(root, "other"), []byte("x"), 0600)
				os.Symlink("other", name)
			case "hardlink":
				os.WriteFile(filepath.Join(root, "other"), []byte("x"), 0600)
				os.Link(filepath.Join(root, "other"), name)
			}
			if _, err := Execute(root, Request{Operation: "read_file", Path: "special"}); err == nil {
				t.Fatal("special read")
			}
			if _, err := Execute(root, Request{Operation: "write_file", Path: "special", Content: "x"}); err == nil {
				t.Fatal("special write")
			}
		})
	}
}

func TestSizeUTF8AndUniqueEdit(t *testing.T) {
	root := t.TempDir()
	for _, content := range []string{strings.Repeat("x", MaxFileBytes+1), string([]byte{255})} {
		if _, err := Execute(root, Request{Operation: "write_file", Path: "large", Content: content}); err == nil {
			t.Fatal("invalid text accepted")
		}
	}
	r := mustRun(t, root, Request{Operation: "write_file", Path: "exact", Content: strings.Repeat("a", MaxFileBytes)})
	sha := r["newSha256"].(string)
	if _, err := Execute(root, Request{Operation: "edit_file", Path: "exact", OldText: "a", NewText: "b", ExpectedSHA256: &sha}); err == nil {
		t.Fatal("nonunique edit")
	}
	if len([]rune(r["diff"].(string))) > 4200 {
		t.Fatal("unbounded diff")
	}
	os.WriteFile(filepath.Join(root, "binary"), []byte{255}, 0600)
	if _, err := Execute(root, Request{Operation: "read_file", Path: "binary"}); err == nil {
		t.Fatal("binary read")
	}
}

func TestListSearchRedactionAndLimits(t *testing.T) {
	root := t.TempDir()
	mustRun(t, root, Request{Operation: "write_file", Path: "b", Content: "error api_key=canary-secret\n"})
	mustRun(t, root, Request{Operation: "write_file", Path: "a", Content: strings.Repeat("error\n", 60)})
	r := mustRun(t, root, Request{Operation: "list_files", Path: "."})
	entries := r["entries"].([]map[string]any)
	if len(entries) != 2 || entries[0]["path"] != "a" {
		t.Fatal(r)
	}
	r = mustRun(t, root, Request{Operation: "search_files", Path: "b", Query: "error"})
	encoded, _ := json.Marshal(r)
	if strings.Contains(string(encoded), "canary-secret") || !strings.Contains(string(encoded), "REDACTED") {
		t.Fatal(string(encoded))
	}
	r = mustRun(t, root, Request{Operation: "search_files", Path: ".", Query: "error"})
	if len(r["matches"].([]map[string]any)) != 50 || r["truncated"] != true {
		t.Fatal(r)
	}
	if _, err := Execute(root, Request{Operation: "list_files", Path: "missing"}); err == nil {
		t.Fatal("missing path silently accepted")
	}
}

func TestStrictProtocol(t *testing.T) {
	for _, input := range []string{
		`{"operation":"shell","path":"."}`, `{"operation":"read_file","path":"a","argv":[]}`,
		`{"operation":"list_files","path":"."} {}`, `{"operation":"read_file","path":"a","offset":true}`,
		`{"operation":"edit_file","path":"a","oldText":"a"}`, strings.Repeat(" ", MaxRequestBytes+1),
	} {
		if _, err := Decode(strings.NewReader(input)); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
	if _, err := Decode(strings.NewReader(`{"operation":"list_files","path":"."}`)); err != nil {
		t.Fatal(err)
	}
}

func TestFileProtocolDistinguishesMissingNullAndEmptyContent(t *testing.T) {
	for _, input := range []string{
		`{"operation":"write_file","path":"a"}`,
		`{"operation":"write_file","path":"a","content":null}`,
		`{"operation":"read_file","path":"a","limit":0}`,
		`{"operation":"read_file","path":"a","offset":null}`,
		"{\"operation\":\"write_file\",\"path\":\"a\",\"content\":\"" + string([]byte{255}) + "\"}",
	} {
		if _, err := Decode(strings.NewReader(input)); err == nil {
			t.Fatal("malformed file request accepted")
		}
	}
	raw, err := json.Marshal(Request{Operation: "write_file", Path: "empty", Content: ""})
	if err != nil {
		t.Fatal(err)
	}
	req, err := Decode(strings.NewReader(string(raw)))
	if err != nil {
		t.Fatal("explicit empty content must survive Go transport", err)
	}
	result := mustRun(t, t.TempDir(), req)
	if result["bytes"] != 0 {
		t.Fatal(result)
	}
	if req, err := Decode(strings.NewReader(`{"operation":"list_files"}`)); err != nil || req.Path != "." {
		t.Fatal("default list path not applied", err)
	}
}

func TestSingleWriterCAS(t *testing.T) {
	root := t.TempDir()
	r := mustRun(t, root, Request{Operation: "write_file", Path: "report", Content: "old"})
	sha := r["newSha256"].(string)
	var wg sync.WaitGroup
	successes := make(chan bool, 2)
	for range 2 {
		wg.Go(func() {
			_, err := Execute(root, Request{Operation: "write_file", Path: "report", Content: "new", ExpectedSHA256: &sha})
			successes <- err == nil
		})
	}
	wg.Wait()
	close(successes)
	n := 0
	for success := range successes {
		if success {
			n++
		}
	}
	if n != 1 {
		t.Fatal("expected one CAS winner", n)
	}
}

func TestInterruptedTemporaryFileIsNotAUserFile(t *testing.T) {
	root := t.TempDir()
	os.WriteFile(filepath.Join(root, ".sandboxd-tmp-dead"), []byte("uncommitted"), 0600)
	r := mustRun(t, root, Request{Operation: "list_files", Path: "."})
	if len(r["entries"].([]map[string]any)) != 0 {
		t.Fatal(r)
	}
	mustRun(t, root, Request{Operation: "write_file", Path: "ok", Content: "committed"})
	if _, err := os.Stat(filepath.Join(root, ".sandboxd-tmp-dead")); err != nil {
		t.Fatal("must preserve uncertain write artifact")
	}
}

func TestEntryQuotaCountsEmptyDirectories(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < MaxTreeEntries; i++ {
		if err := os.Mkdir(filepath.Join(root, fmtName(i)), 0700); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := Execute(root, Request{Operation: "write_file", Path: "new", Content: "x"}); err == nil {
		t.Fatal("directory quota ignored")
	}
}

func fmtName(i int) string {
	const digits = "0123456789abcdef"
	return "d" + string([]byte{digits[(i>>8)&15], digits[(i>>4)&15], digits[i&15]})
}
