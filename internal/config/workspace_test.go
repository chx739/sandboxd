package config

import "testing"

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
