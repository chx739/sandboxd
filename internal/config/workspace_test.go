package config

import (
	"strings"
	"testing"
)

func TestBackupOwnerConfigurationIsExplicitAndComplete(t *testing.T) {
	t.Setenv("SANDBOXD_TOKEN", "test-agent")
	t.Setenv("SANDBOXD_OPERATOR_TOKEN", "test-operator")
	base := []string{"--workspace-nfs-server", "172.23.0.3", "--workspace-image", "fixed", "--workspace-control-dir", "/home/demo/control", "--workspace-backup-image", "sandboxd-restic:0.18.1-v3", "--workspace-backup-secret", "restic-test", "--workspace-backup-minio-ip", "172.23.0.4"}
	owner := []string{"--workspace-backup-controller", "controller-12345678123442348abc123456789abc", "--workspace-backup-repository-id", strings.Repeat("2", 64), "--workspace-backup-owner-snapshot", strings.Repeat("9", 64)}
	if cfg, err := Parse(append(append([]string{}, base...), owner...)); err != nil || !cfg.BackupOwner.Valid() {
		t.Fatal("valid owner rejected", err)
	}
	for _, args := range [][]string{owner, append(append([]string{}, base...), owner[:2]...), append(append([]string{}, base...), owner[2:]...)} {
		if _, err := Parse(args); err == nil {
			t.Fatal("incomplete owner configuration accepted")
		}
	}
}

func TestWorkspaceConfigurationIsCompleteAndPersistent(t *testing.T) {
	t.Setenv("SANDBOXD_TOKEN", "test-agent")
	t.Setenv("SANDBOXD_OPERATOR_TOKEN", "test-operator")
	for _, args := range [][]string{
		{"--workspace-nfs-server", "172.18.0.3"},
		{"--workspace-nfs-server", "172.18.0.3", "--workspace-image", "fixed", "--workspace-control-dir", "/tmp/not-persistent"},
		{"--workspace-nfs-server", "172.18.0.3", "--workspace-image", "fixed", "--workspace-control-dir", "/home/demo/control", "--runtime-class", ""},
	} {
		if _, err := Parse(args); err == nil {
			t.Fatal("incomplete or temporary configuration accepted")
		}
	}
	if _, err := Parse([]string{"--workspace-nfs-server", "172.18.0.3", "--workspace-image", "fixed", "--workspace-control-dir", "/home/demo/control"}); err != nil {
		t.Fatal(err)
	}
}

func TestBackupConfigurationRequiresExplicitCompleteTrustedInputs(t *testing.T) {
	t.Setenv("SANDBOXD_TOKEN", "test-agent")
	t.Setenv("SANDBOXD_OPERATOR_TOKEN", "test-operator")
	base := []string{"--workspace-nfs-server", "172.23.0.3", "--workspace-image", "fixed", "--workspace-control-dir", "/home/demo/control"}
	for _, extra := range [][]string{
		{"--workspace-backup-image", "sandboxd-restic:0.18.1-v1"},
		{"--workspace-backup-image", "sandboxd-restic:0.18.1-v1", "--workspace-backup-secret", "restic-test", "--workspace-backup-minio-ip", "minio.example"},
		{"--workspace-backup-image", "sandboxd-restic:0.18.1-v1", "--workspace-backup-secret", "restic-test", "--workspace-backup-minio-ip", "172.23.0.4", "--runtime-class", ""},
	} {
		args := append(append([]string{}, base...), extra...)
		if _, err := Parse(args); err == nil {
			t.Fatal("invalid backup configuration accepted")
		}
	}
	args := append(append([]string{}, base...), "--workspace-backup-image", "sandboxd-restic:0.18.1-v1", "--workspace-backup-secret", "restic-test", "--workspace-backup-minio-ip", "172.23.0.4")
	if cfg, err := Parse(args); err != nil || cfg.BackupSecret != "restic-test" {
		t.Fatal(cfg, err)
	}
	if cfg, err := Parse(base); err != nil || cfg.BackupImage != "" {
		t.Fatal("V1 default unexpectedly enabled backups", err)
	}
}
