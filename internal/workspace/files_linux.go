//go:build linux

// Package workspace is the restricted text-file protocol used inside a sandbox.
// It deliberately has no command, environment, arbitrary root or network option.
package workspace

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"unicode/utf8"
)

const (
	MaxFileBytes      = 256 << 10
	MaxRequestBytes   = 4 << 20 // JSON escaping can expand both oldText and newText.
	MaxResponseBytes  = 2 << 20
	MaxWorkspaceBytes = 64 << 20
	MaxTreeEntries    = 512
	lockName          = ".sandboxd-lock"
)

type Request struct {
	Operation      string  `json:"operation"`
	Path           string  `json:"path"`
	Query          string  `json:"query,omitempty"`
	Content        string  `json:"content"`
	OldText        string  `json:"oldText,omitempty"`
	NewText        string  `json:"newText"`
	ExpectedSHA256 *string `json:"expectedSha256,omitempty"`
	Offset         int     `json:"offset,omitempty"`
	Limit          int     `json:"limit,omitempty"`
}

// Decode rejects additional JSON values/fields before any filesystem access.
func Decode(reader io.Reader) (Request, error) {
	var req Request
	raw, err := io.ReadAll(io.LimitReader(reader, MaxRequestBytes+1))
	if err != nil || len(raw) > MaxRequestBytes {
		return req, errors.New("request exceeds limit")
	}
	if !utf8.Valid(raw) {
		return req, errors.New("request must be UTF-8")
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		return req, errors.New("invalid file request")
	}
	if decoder.Decode(new(any)) != io.EOF {
		return req, errors.New("multiple JSON values")
	}
	// encoding/json 会把字符串字段的 null 当零值；这里必须区别“缺参数”与
	// 显式空文本，否则格式错误的写入可能静默变成清空文件。
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		return req, errors.New("file request must be an object")
	}
	for _, value := range fields {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return req, errors.New("null file arguments are not supported")
		}
	}
	required := []string{"operation", "path"}
	if req.Operation == "list_files" || req.Operation == "search_files" {
		if _, ok := fields["path"]; !ok {
			req.Path = "."
		}
		required = []string{"operation"}
	}
	switch req.Operation {
	case "write_file":
		required = append(required, "content")
	case "edit_file":
		required = append(required, "oldText", "newText", "expectedSha256")
	case "search_files":
		required = append(required, "query")
	}
	for _, key := range required {
		if _, ok := fields[key]; !ok {
			return req, errors.New("missing required file argument")
		}
	}
	if _, ok := fields["limit"]; ok && req.Limit == 0 {
		return req, errors.New("explicit read limit must be positive")
	}
	return req, req.Validate()
}

func (r Request) Validate() error {
	root := r.Operation == "list_files" || r.Operation == "search_files"
	if _, err := parts(r.Path, root); err != nil {
		return err
	}
	switch r.Operation {
	case "list_files", "read_file", "search_files", "write_file", "edit_file":
	default:
		return errors.New("unsupported file operation")
	}
	if r.Offset < 0 || r.Offset > MaxFileBytes || r.Limit < 0 || r.Limit > MaxFileBytes {
		return errors.New("invalid read range")
	}
	if len(r.Content) > MaxFileBytes || len(r.NewText) > MaxFileBytes || len(r.OldText) > MaxFileBytes {
		return errors.New("text exceeds 256 KiB")
	}
	if !utf8.ValidString(r.Content) || !utf8.ValidString(r.NewText) || !utf8.ValidString(r.OldText) {
		return errors.New("invalid UTF-8")
	}
	if r.ExpectedSHA256 != nil && !hashPattern.MatchString(*r.ExpectedSHA256) {
		return errors.New("invalid expectedSha256")
	}
	if r.Operation == "edit_file" && (r.ExpectedSHA256 == nil || r.OldText == "") {
		return errors.New("edit needs a hash and nonempty oldText")
	}
	if r.Operation == "search_files" && (len(r.Query) == 0 || len(r.Query) > 256) {
		return errors.New("query must be 1..256 bytes")
	}
	return nil
}

var hashPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)

// ExecutionError 表示存储执行失败；它不能伪装成路径/CAS策略拒绝。
type ExecutionError struct {
	Reason           string
	OutcomeUncertain bool
}

func (e *ExecutionError) Error() string { return e.Reason }

func parts(path string, allowRoot bool) ([]string, error) {
	if allowRoot && path == "." {
		return nil, nil
	}
	if len(path) == 0 || len(path) > 512 || !utf8.ValidString(path) || strings.ContainsAny(path, "\\\x00") {
		return nil, errors.New("invalid relative path")
	}
	result := strings.Split(path, "/")
	if len(result) > 8 {
		return nil, errors.New("path exceeds depth limit")
	}
	for _, p := range result {
		if p == "" || p == "." || p == ".." || strings.HasPrefix(p, ".sandboxd-") {
			return nil, errors.New("invalid or reserved path component")
		}
	}
	return result, nil
}

// Execute 的生产根固定为 /workspace。父目录逐层通过已打开的目录描述符及
// O_NOFOLLOW 访问，避免先校验字符串、随后路径被 symlink 替换的竞态。
func Execute(rootPath string, req Request) (map[string]any, error) {
	if err := req.Validate(); err != nil {
		return nil, err
	}
	fd, err := syscall.Open(rootPath, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errors.New("workspace unavailable")
	}
	root := os.NewFile(uintptr(fd), "workspace")
	defer root.Close()
	lockFD, err := syscall.Openat(fd, lockName, syscall.O_RDWR|syscall.O_CREAT|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return nil, errors.New("workspace lock unavailable")
	}
	lock := os.NewFile(uintptr(lockFD), "workspace-lock")
	defer lock.Close()
	info, err := lock.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() != 0 || info.Sys().(*syscall.Stat_t).Nlink != 1 {
		return nil, errors.New("invalid workspace lock")
	}
	// Nonblocking: an interrupted/partitioned writer cannot make the helper wait
	// indefinitely. This lock supplements, but does not replace, Go owner fencing.
	if err := syscall.Flock(lockFD, syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return nil, errors.New("workspace busy")
	}
	defer syscall.Flock(lockFD, syscall.LOCK_UN)
	switch req.Operation {
	case "write_file", "edit_file":
		return write(root, req)
	case "read_file":
		data, err := readPath(root, req.Path)
		if err != nil {
			return nil, err
		}
		chars := []rune(string(data))
		offset := min(req.Offset, len(chars))
		limit := req.Limit
		if limit == 0 {
			limit = 16384
		}
		end := min(offset+limit, len(chars))
		visible, redacted := redact(string(chars[offset:end]))
		return map[string]any{"path": req.Path, "content": visible, "sha256": hash(data), "totalChars": len(chars), "offset": req.Offset, "truncated": end < len(chars), "redacted": redacted}, nil
	default:
		return listOrSearch(root, req)
	}
}

func parent(root *os.File, path string, create bool) (*os.File, string, error) {
	items, err := parts(path, false)
	if err != nil {
		return nil, "", err
	}
	fd, err := openDirectoryAt(int(root.Fd()), ".")
	if err != nil {
		return nil, "", err
	}
	current := os.NewFile(uintptr(fd), "parent")
	for _, name := range items[:len(items)-1] {
		if create {
			err := syscall.Mkdirat(int(current.Fd()), name, 0700)
			if err != nil && err != syscall.EEXIST {
				current.Close()
				return nil, "", errors.New("cannot create parent")
			}
			if err == nil {
				if e := current.Sync(); e != nil {
					current.Close()
					return nil, "", e
				}
			}
		}
		next, err := openDirectoryAt(int(current.Fd()), name)
		current.Close()
		if err != nil {
			return nil, "", fmt.Errorf("parent missing, not a directory, or symlink: %w", err)
		}
		current = os.NewFile(uintptr(next), "parent")
	}
	return current, items[len(items)-1], nil
}

func openDirectoryAt(dirFD int, name string) (int, error) {
	// 当前gVisor的O_DIRECTORY|O_NOFOLLOW仍解析父链接，openat2未实现。
	// O_PATH|O_NOFOLLOW取得链接本身的描述符，fstat必须确认为目录；
	// 再从该已打开的目录取"."，不重新解析原路径，避免检查/打开间替换。
	const oPath = 0x200000
	if name == "" || name == ".." || strings.ContainsAny(name, "/\\\x00") {
		return -1, errors.New("directory lookup requires one safe component")
	}
	fd, err := syscall.Openat(dirFD, name, oPath|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return -1, err
	}
	file := os.NewFile(uintptr(fd), "directory-identity")
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return -1, err
	}
	if !info.IsDir() {
		return -1, errors.New("parent is not a directory or is a symlink")
	}
	return syscall.Openat(fd, ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
}

func readAt(dir *os.File, name string) ([]byte, error) {
	fd, err := syscall.Openat(int(dir.Fd()), name, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), "text")
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > MaxFileBytes || info.Sys().(*syscall.Stat_t).Nlink != 1 {
		return nil, errors.New("expected bounded regular file without hard links")
	}
	data, err := io.ReadAll(io.LimitReader(file, MaxFileBytes+1))
	if err != nil || len(data) > MaxFileBytes || !utf8.Valid(data) {
		return nil, errors.New("expected UTF-8 text within 256 KiB")
	}
	return data, nil
}

func readPath(root *os.File, path string) ([]byte, error) {
	dir, leaf, err := parent(root, path, false)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	data, err := readAt(dir, leaf)
	if err != nil {
		return nil, fmt.Errorf("file unavailable, unsupported, or symlink: %w", err)
	}
	return data, nil
}

type entry struct {
	Path  string
	Bytes int64
}

func statAt(dir *os.File, name string) (os.FileInfo, error) {
	// O_PATH|O_NOFOLLOW obtains metadata without opening FIFOs/devices and
	// without resolving a leaf symlink. DirEntry.Info uses a pathname and would
	// lose the directory descriptor boundary (and can race with renames).
	const oPath = 0x200000
	fd, err := syscall.Openat(int(dir.Fd()), name, oPath|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), "metadata")
	defer file.Close()
	return file.Stat()
}

// Inventory is bounded by entry count, directory depth and total bytes, including
// empty directories. It is an application quota, not an NFS server disk quota.
func inventory(root *os.File) ([]entry, int64, int, error) {
	var files []entry
	count := 0
	total := int64(0)
	var walk func(*os.File, string, int) error
	walk = func(dir *os.File, prefix string, depth int) error {
		for {
			items, err := dir.ReadDir(64)
			if err != nil && err != io.EOF {
				return err
			}
			for _, item := range items {
				name := item.Name()
				if prefix == "" && name == lockName {
					continue
				}
				count++
				if count > MaxTreeEntries {
					return errors.New("workspace exceeds 512 entries")
				}
				path := name
				if prefix != "" {
					path = prefix + "/" + name
				}
				isTemp := strings.HasPrefix(name, ".sandboxd-tmp-")
				if !isTemp {
					if _, e := parts(path, false); e != nil {
						return e
					}
				}
				info, e := statAt(dir, name)
				if e != nil {
					return e
				}
				if isTemp && !info.Mode().IsRegular() {
					return errors.New("unsafe temporary file")
				}
				if info.IsDir() {
					if depth >= 8 {
						return errors.New("workspace exceeds depth limit")
					}
					fd, e := openDirectoryAt(int(dir.Fd()), name)
					if e != nil {
						return errors.New("unsafe workspace directory")
					}
					child := os.NewFile(uintptr(fd), "inventory")
					e = walk(child, path, depth+1)
					child.Close()
					if e != nil {
						return e
					}
				} else {
					if !info.Mode().IsRegular() || info.Size() > MaxFileBytes || info.Sys().(*syscall.Stat_t).Nlink != 1 {
						return errors.New("unsupported workspace file, link, or size")
					}
					total += info.Size()
					if total > MaxWorkspaceBytes {
						return errors.New("workspace exceeds 64 MiB")
					}
					if !isTemp {
						files = append(files, entry{path, info.Size()})
					}
				}
			}
			if err == io.EOF {
				return nil
			}
		}
	}
	// Reopen instead of dup: directory offsets must not affect later inventories.
	fd, err := syscall.Openat(int(root.Fd()), ".", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, 0, 0, err
	}
	dir := os.NewFile(uintptr(fd), "inventory")
	defer dir.Close()
	err = walk(dir, "", 0)
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
	return files, total, count, err
}

func write(root *os.File, req Request) (map[string]any, error) {
	_, total, count, err := inventory(root)
	if err != nil {
		return nil, err
	}
	// Validate old content before creating parents or writing temporary data.
	old, err := readPath(root, req.Path)
	created := false
	if err != nil {
		// NFS目录枚举可能晚于直接路径查询，不能用inventory里“没看到”证明
		// 文件不存在。只有路径查询明确ENOENT才允许创建；ELOOP/ENOTDIR、
		// 权限和读入失败都必须拒绝，避免把新出现的链接当作新文件替换。
		if !errors.Is(err, syscall.ENOENT) || req.ExpectedSHA256 != nil || req.Operation == "edit_file" {
			return nil, errors.New("existing file unreadable or CAS target missing")
		}
		created = true
	} else if req.ExpectedSHA256 == nil || hash(old) != *req.ExpectedSHA256 {
		return nil, errors.New("expectedSha256 mismatch or absent")
	}
	content := req.Content
	if req.Operation == "edit_file" {
		if strings.Count(string(old), req.OldText) != 1 {
			return nil, errors.New("oldText must occur exactly once")
		}
		content = strings.Replace(string(old), req.OldText, req.NewText, 1)
	}
	if len(content) > MaxFileBytes || total-int64(len(old))+int64(len(content)) > MaxWorkspaceBytes {
		return nil, errors.New("file or workspace quota exceeded")
	}
	// Reserve entries for any newly created parent directories plus the file.
	if created {
		items, _ := parts(req.Path, false)
		// Conservative reservation also counts existing parents. This may refuse
		// a nearly full tree early; it never exceeds the stated entry limit.
		if count+len(items) > MaxTreeEntries {
			return nil, errors.New("workspace entry quota exceeded")
		}
	}
	dir, leaf, err := parent(root, req.Path, true)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	// Leaf might be an existing directory; rename will safely refuse to replace it.
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return nil, err
	}
	temp := ".sandboxd-tmp-" + hex.EncodeToString(nonce[:])
	fd, err := syscall.Openat(int(dir.Fd()), temp, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return nil, errors.New("cannot create file")
	}
	file := os.NewFile(uintptr(fd), "temporary")
	defer syscall.Unlinkat(int(dir.Fd()), temp)
	_, err = io.WriteString(file, content)
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, &ExecutionError{Reason: "file write or sync failed", OutcomeUncertain: false}
	}
	if err := syscall.Renameat(int(dir.Fd()), temp, int(dir.Fd()), leaf); err != nil {
		return nil, &ExecutionError{Reason: "atomic replace failed", OutcomeUncertain: true}
	}
	if err := dir.Sync(); err != nil {
		return nil, &ExecutionError{Reason: "directory sync failed; outcome requires hash inspection", OutcomeUncertain: true}
	}
	diff, redacted := redact(boundedDiff(req.Path, string(old), content))
	var oldHash any
	if !created {
		oldHash = hash(old)
	}
	return map[string]any{"path": req.Path, "created": created, "oldSha256": oldHash, "newSha256": hash([]byte(content)), "bytes": len(content), "diff": diff, "redacted": redacted}, nil
}

func listOrSearch(root *os.File, req Request) (map[string]any, error) {
	files, _, _, err := inventory(root)
	if err != nil {
		return nil, err
	}
	selected := []entry{}
	for _, f := range files {
		if req.Path == "." || f.Path == req.Path || strings.HasPrefix(f.Path, req.Path+"/") {
			selected = append(selected, f)
		}
	}
	if req.Path != "." {
		// Empty/missing directory must not silently become a successful no-result.
		dir, leaf, e := parent(root, req.Path, false)
		if e != nil {
			return nil, e
		}
		fd, e := syscall.Openat(int(dir.Fd()), leaf, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
		dir.Close()
		if e != nil {
			return nil, errors.New("path unavailable")
		}
		syscall.Close(fd)
	}
	entries := []map[string]any{}
	matches := []map[string]any{}
	scanned, bytes := 0, 0
	truncated := len(selected) > 100
	for _, f := range selected[:min(len(selected), 100)] {
		data, err := readPath(root, f.Path)
		if err != nil {
			return nil, err
		}
		if req.Operation == "list_files" {
			entries = append(entries, map[string]any{"path": f.Path, "bytes": len(data), "sha256": hash(data)})
			continue
		}
		if bytes+len(data) > 1<<20 {
			truncated = true
			break
		}
		bytes += len(data)
		scanned++
		for number, line := range strings.Split(string(data), "\n") {
			if !strings.Contains(line, req.Query) {
				continue
			}
			runes := []rune(line)
			visible, redacted := redact(string(runes[:min(len(runes), 512)]))
			matches = append(matches, map[string]any{"path": f.Path, "line": number + 1, "text": visible, "redacted": redacted})
			if len(matches) >= 50 {
				truncated = true
				break
			}
		}
		if len(matches) >= 50 {
			break
		}
	}
	if req.Operation == "list_files" {
		return map[string]any{"path": req.Path, "entries": entries, "truncated": truncated}, nil
	}
	return map[string]any{"query": req.Query, "matches": matches, "scannedFiles": scanned, "scannedBytes": bytes, "truncated": truncated}, nil
}

func hash(data []byte) string { return fmt.Sprintf("%x", sha256.Sum256(data)) }

var redactors = []struct {
	pattern     *regexp.Regexp
	replacement string
}{
	{regexp.MustCompile(`(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;'”}]+`), "${1}[REDACTED]"},
	{regexp.MustCompile(`(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;'”}]+`), "${1}[REDACTED]"},
	{regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+`), "[REDACTED]"},
	{regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{8,}\b`), "[REDACTED]"},
}

func redact(text string) (string, bool) {
	result := text
	for _, r := range redactors {
		result = r.pattern.ReplaceAllString(result, r.replacement)
	}
	return result, result != text
}

// A single unified hunk is intentionally simple; it is not a minimal edit diff.
func boundedDiff(path, old, updated string) string {
	if old == updated {
		return ""
	}
	var result strings.Builder
	result.WriteString("--- " + path + ":old\n+++ " + path + ":new\n")
	oldLines, newLines := strings.SplitAfter(old, "\n"), strings.SplitAfter(updated, "\n")
	if old == "" {
		oldLines = nil
	}
	if updated == "" {
		newLines = nil
	}
	result.WriteString(fmt.Sprintf("@@ -1,%d +1,%d @@\n", len(oldLines), len(newLines)))
	for _, group := range []struct {
		prefix string
		lines  []string
	}{{"-", oldLines}, {"+", newLines}} {
		for _, line := range group.lines {
			result.WriteString(group.prefix + line)
			if !strings.HasSuffix(line, "\n") {
				result.WriteByte('\n')
			}
			if result.Len() > 8192 {
				break
			}
		}
	}
	runes := []rune(result.String())
	if len(runes) > 4096 {
		return string(runes[:4096]) + "\n...[diff truncated]"
	}
	return string(runes)
}
