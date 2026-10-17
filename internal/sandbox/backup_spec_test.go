package sandbox

import (
	"testing"

	"github.com/chx739/sandboxd/internal/backuphelper"
	corev1 "k8s.io/api/core/v1"
)

func TestBackupPodKeepsIsolationAndCredentialSeparation(t *testing.T) {
	cfg := Config{Image: "sandboxd-restic:0.18.1-v1", RuntimeClass: "gvisor"}
	request := backuphelper.Request{Operation: "backup", CheckpointID: "cp-123456789abc4def8123456789abcdef"}
	pod, err := BuildBackupPod(cfg, "test", "workspace-123456789abcdef0123456789abcdef0", "restic-test", request)
	if err != nil {
		t.Fatal(err)
	}
	c := pod.Spec.Containers[0]
	if pod.APIVersion != "v1" || pod.Kind != "Pod" {
		t.Fatal("serialized maintenance Pod lacks TypeMeta")
	}
	if pod.Namespace != StorageNamespace || pod.Spec.AutomountServiceAccountToken == nil || *pod.Spec.AutomountServiceAccountToken ||
		!c.VolumeMounts[0].ReadOnly || c.VolumeMounts[0].SubPath != "workspace-123456789abcdef0123456789abcdef0" ||
		*c.SecurityContext.AllowPrivilegeEscalation || !*c.SecurityContext.ReadOnlyRootFilesystem || *pod.Spec.SecurityContext.RunAsUser != 65532 {
		t.Fatal("backup baseline weakened")
	}
	if len(c.SecurityContext.Capabilities.Drop) != 1 || c.SecurityContext.Capabilities.Drop[0] != corev1.Capability("ALL") {
		t.Fatal("capabilities")
	}
	for _, v := range pod.Spec.Volumes {
		if v.Projected != nil || v.HostPath != nil {
			t.Fatal("unexpected API token or host mount")
		}
	}
	for _, v := range c.Env {
		if v.Name == "AWS_SECRET_ACCESS_KEY" && (v.Value != "" || v.ValueFrom.SecretKeyRef.Name != "restic-test") {
			t.Fatal("credential inline")
		}
	}
	request = backuphelper.Request{Operation: "restore", SnapshotID: hashForBackupTest(), ManifestSHA256: hashForBackupTest()}
	restore, err := BuildBackupPod(cfg, "restore", "workspace-123456789abcdef0123456789abcdef0", "restic-test", request)
	if err != nil || restore.Spec.Containers[0].VolumeMounts[0].ReadOnly {
		t.Fatal("restore not writable", err)
	}
	if _, err = BuildBackupPod(cfg, "bad", "../../host", "restic-test", request); err == nil {
		t.Fatal("path escape")
	}
}

func hashForBackupTest() string {
	return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

func TestBackupNetworkOnlyAllowsConfiguredMinIO(t *testing.T) {
	p, err := BuildBackupNetworkPolicy("172.23.0.4")
	if err == nil && (p.APIVersion != "networking.k8s.io/v1" || p.Kind != "NetworkPolicy") {
		t.Fatal("serialized policy lacks TypeMeta")
	}
	if err != nil || p.Spec.PodSelector.MatchLabels[LabelManagedBy] != BackupManagedBy || len(p.Spec.Ingress) != 0 ||
		len(p.Spec.Egress) != 1 || p.Spec.Egress[0].To[0].IPBlock.CIDR != "172.23.0.4/32" || p.Spec.Egress[0].Ports[0].Port.IntVal != 9000 {
		t.Fatalf("%+v %v", p, err)
	}
	if _, err = BuildBackupNetworkPolicy("minio.example"); err == nil {
		t.Fatal("arbitrary hostname")
	}
}
