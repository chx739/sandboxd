//go:build linux

// Package control implements the single-writer durable metadata used by Go.
// Python and Go must never share a journal file or depend on an in-memory owner.
package control

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"syscall"
	"time"
)

const maxRecordBytes = 2 << 20

var eventType = regexp.MustCompile(`^[a-z][a-z0-9_.-]{0,63}$`)

type Entry struct {
	Seq       uint64          `json:"seq"`
	Type      string          `json:"type"`
	Timestamp string          `json:"timestamp"`
	Data      json.RawMessage `json:"data"`
}

type diskEntry struct {
	Entry
	Checksum string `json:"sha256"`
}

type Journal struct {
	mu       sync.Mutex
	file     *os.File
	entries  []Entry
	validEnd int64
	poisoned bool
}

// OpenJournal 用进程锁防止双写；只有缺换行的断尾可恢复，完整坏行必须拒绝。
// fsync 后才发布内存状态，不能让 HTTP 成功响应领先于可恢复的控制记录。
func OpenJournal(path string) (_ *Journal, err error) {
	parent := filepath.Dir(path)
	if err := os.MkdirAll(parent, 0700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(parent)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("control parent must be a directory")
	}
	if err := os.Chmod(parent, 0700); err != nil {
		return nil, err
	}
	fd, err := syscall.Open(path, syscall.O_RDWR|syscall.O_CREAT|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(fd), path)
	journal := &Journal{file: file}
	defer func() {
		if err != nil {
			journal.Close()
		}
	}()
	info, err = file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Sys().(*syscall.Stat_t).Nlink != 1 {
		return nil, errors.New("control journal must be a regular file without hard links")
	}
	if err = syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return nil, errors.New("control journal already has an owner")
	}
	if err = file.Chmod(0600); err != nil {
		return nil, err
	}
	info, err = file.Stat()
	if err != nil || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("control journal needs private Linux permissions")
	}
	if err = journal.load(); err != nil {
		return nil, err
	}
	dir, err := os.Open(parent)
	if err != nil {
		return nil, err
	}
	err = dir.Sync()
	closeErr := dir.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, err
	}
	return journal, nil
}

func checksum(entry Entry) string {
	data, _ := json.Marshal(entry)
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func (j *Journal) load() error {
	reader := bufio.NewReaderSize(j.file, 32<<10)
	for {
		var line []byte
		var readErr error
		for {
			fragment, err := reader.ReadSlice('\n')
			if len(line)+len(fragment) > maxRecordBytes {
				return errors.New("control record exceeds limit")
			}
			line = append(line, fragment...)
			if err == bufio.ErrBufferFull {
				continue
			}
			readErr = err
			break
		}
		if readErr == io.EOF {
			return nil
		} // Do not publish a truncated tail.
		if readErr != nil {
			return readErr
		}
		var record diskEntry
		decoder := json.NewDecoder(bytes.NewReader(line))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&record); err != nil {
			return errors.New("invalid complete control record")
		}
		if decoder.Decode(new(any)) != io.EOF || record.Seq != uint64(len(j.entries)+1) || !eventType.MatchString(record.Type) || record.Checksum != checksum(record.Entry) {
			return errors.New("control record sequence or checksum mismatch")
		}
		if _, err := time.Parse(time.RFC3339Nano, record.Timestamp); err != nil {
			return errors.New("invalid control timestamp")
		}
		var data map[string]json.RawMessage
		if err := json.Unmarshal(record.Data, &data); err != nil || data == nil {
			return errors.New("control record data must be an object")
		}
		j.entries = append(j.entries, record.Entry)
		j.validEnd += int64(len(line))
	}
}

func (j *Journal) Entries() []Entry {
	j.mu.Lock()
	defer j.mu.Unlock()
	result := make([]Entry, len(j.entries))
	for i, e := range j.entries {
		result[i] = e
		result[i].Data = bytes.Clone(e.Data)
	}
	return result
}

func (j *Journal) Append(kind string, data any) (Entry, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.file == nil || j.poisoned {
		return Entry{}, errors.New("control journal is closed or requires recovery")
	}
	if !eventType.MatchString(kind) {
		return Entry{}, errors.New("invalid control event type")
	}
	raw, err := json.Marshal(data)
	if err != nil {
		return Entry{}, err
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil || object == nil {
		return Entry{}, errors.New("control data must be an object")
	}
	entry := Entry{Seq: uint64(len(j.entries) + 1), Type: kind, Timestamp: time.Now().UTC().Format(time.RFC3339Nano), Data: raw}
	line, err := json.Marshal(diskEntry{Entry: entry, Checksum: checksum(entry)})
	if err != nil {
		return Entry{}, err
	}
	line = append(line, '\n')
	if len(line) > maxRecordBytes {
		return Entry{}, errors.New("control record exceeds limit")
	}
	if err = j.file.Truncate(j.validEnd); err == nil {
		_, err = j.file.Seek(j.validEnd, io.SeekStart)
	}
	if err == nil {
		var n int
		n, err = j.file.Write(line)
		if err == nil && n != len(line) {
			err = io.ErrShortWrite
		}
	}
	if err == nil {
		err = j.file.Sync()
	}
	if err != nil {
		j.poisoned = true
		return Entry{}, fmt.Errorf("control append requires recovery: %w", err)
	}
	j.entries = append(j.entries, entry)
	j.validEnd += int64(len(line))
	entry.Data = bytes.Clone(entry.Data)
	return entry, nil
}

func (j *Journal) Close() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.file == nil {
		return nil
	}
	err := j.file.Close()
	j.file = nil
	return err
}
