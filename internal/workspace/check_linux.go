//go:build linux

package workspace

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"syscall"
)

// Check 是绑定前的可信存储探测，不接受文件路径、不修改业务文件。
// 短暂写入私有随机文件并 fsync/unlink，区别于只检查“目录能列出来”。
func Check(rootPath string) error {
	if _, err := Execute(rootPath, Request{Operation: "list_files", Path: "."}); err != nil {
		return err
	}
	fd, err := syscall.Open(rootPath, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return errors.New("workspace check root unavailable")
	}
	root := os.NewFile(uintptr(fd), "workspace-check")
	defer root.Close()
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return err
	}
	name := ".sandboxd-tmp-" + hex.EncodeToString(nonce[:])
	childFD, err := syscall.Openat(fd, name, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return errors.New("workspace is not writable")
	}
	file := os.NewFile(uintptr(childFD), "workspace-check-file")
	defer syscall.Unlinkat(fd, name)
	_, err = io.WriteString(file, "sandboxd workspace probe\n")
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return errors.New("workspace write sync failed")
	}
	if err := syscall.Unlinkat(fd, name); err != nil {
		return errors.New("workspace probe cleanup failed")
	}
	if err := root.Sync(); err != nil {
		return errors.New("workspace metadata sync failed")
	}
	return nil
}
