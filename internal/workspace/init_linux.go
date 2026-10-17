//go:build linux

package workspace

import (
	"errors"
	"os"
	"regexp"
	"syscall"
)

var workspaceIdentity = regexp.MustCompile(`^workspace-[a-f0-9]{32}$`)

// Initialize 只供可信维护 Pod 初始化目录；业务 Pod 不能挂载导出父目录。
// 固定 ID 与逐层目录描述符避免把初始化入口变成自由路径写入接口。
func Initialize(rootPath, workspaceID string) (map[string]any, error) {
	if !workspaceIdentity.MatchString(workspaceID) {
		return nil, errors.New("invalid workspace identity")
	}
	fd, err := syscall.Open(rootPath, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errors.New("workspace export root unavailable")
	}
	root := os.NewFile(uintptr(fd), "workspace-export")
	defer root.Close()
	err = syscall.Mkdirat(fd, workspaceID, 0700)
	created := err == nil
	if err != nil && err != syscall.EEXIST {
		return nil, errors.New("cannot initialize workspace directory")
	}
	childFD, err := syscall.Openat(fd, workspaceID, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, errors.New("workspace exists but is not a safe directory")
	}
	child := os.NewFile(uintptr(childFD), "workspace-directory")
	defer child.Close()
	info, err := child.Stat()
	if err != nil {
		return nil, err
	}
	stat := info.Sys().(*syscall.Stat_t)
	if info.Mode().Perm() != 0700 || stat.Uid != uint32(os.Geteuid()) || stat.Gid != uint32(os.Getegid()) {
		return nil, errors.New("workspace has unexpected owner or permissions")
	}
	if err := child.Sync(); err != nil {
		return nil, errors.New("workspace directory sync failed")
	}
	if err := root.Sync(); err != nil {
		return nil, errors.New("workspace export sync failed")
	}
	return map[string]any{"workspaceId": workspaceID, "created": created, "uid": stat.Uid, "gid": stat.Gid}, nil
}
