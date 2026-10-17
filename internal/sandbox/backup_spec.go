package sandbox

import (
	"errors"
	"net"

	"github.com/chx739/sandboxd/internal/backuphelper"
	corev1 "k8s.io/api/core/v1"
	networkv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

const BackupManagedBy = "sandboxd-backup"

// BuildBackupPod is trusted-only. Neither the image/secret nor this PodSpec is an
// Agent tool parameter. The actual caller must hold Go's workspace write barrier.
func BuildBackupPod(cfg Config, id, workspaceID, secretName string, request backuphelper.Request) (*corev1.Pod, error) {
	if cfg.Image == "" || cfg.RuntimeClass == "" || !runtimeIDPattern.MatchString(id) || !workspaceIDPattern.MatchString(workspaceID) ||
		!runtimeIDPattern.MatchString(secretName) || len(secretName) < 8 || secretName[:7] != "restic-" {
		return nil, errors.New("invalid trusted backup configuration")
	}
	args, err := backuphelper.FixedArgs(request)
	if err != nil {
		return nil, err
	}
	cfg.Namespace = StorageNamespace
	pod := BuildPod(cfg, id)
	pod.TypeMeta = metav1.TypeMeta{APIVersion: "v1", Kind: "Pod"}
	pod.Name = "workspace-backup-" + id
	pod.Labels = map[string]string{LabelManagedBy: BackupManagedBy, LabelWorkspace: workspaceID}
	pod.Spec.ServiceAccountName = "default"
	deadline := int64(120)
	pod.Spec.ActiveDeadlineSeconds = &deadline
	container := &pod.Spec.Containers[0]
	container.Command = []string{"/usr/local/bin/sandbox-restic"}
	container.Args = args
	container.Resources.Limits[corev1.ResourceMemory] = resource.MustParse("512Mi")
	container.Env = []corev1.EnvVar{
		{Name: "HOME", Value: "/tmp"}, {Name: "TMPDIR", Value: "/tmp"}, {Name: "GOMAXPROCS", Value: "1"},
		{Name: "GOMEMLIMIT", Value: "192MiB"}, {Name: "AWS_EC2_METADATA_DISABLED", Value: "true"},
		{Name: "AWS_DEFAULT_REGION", Value: "us-east-1"},
		{Name: "AWS_ACCESS_KEY_ID", ValueFrom: &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: secretName}, Key: "access-key"}}},
		{Name: "AWS_SECRET_ACCESS_KEY", ValueFrom: &corev1.EnvVarSource{SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: secretName}, Key: "secret-key"}}},
	}
	container.VolumeMounts = []corev1.VolumeMount{
		{Name: "source", MountPath: "/workspace", SubPath: workspaceID, ReadOnly: request.Operation != "restore"},
		{Name: "credentials", MountPath: "/restic-secrets", ReadOnly: true}, {Name: "tmp", MountPath: "/tmp"},
	}
	mode := int32(0444)
	pod.Spec.Volumes = []corev1.Volume{
		{Name: "source", VolumeSource: corev1.VolumeSource{PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: StorageRootClaim, ReadOnly: request.Operation != "restore"}}},
		{Name: "credentials", VolumeSource: corev1.VolumeSource{Secret: &corev1.SecretVolumeSource{SecretName: secretName, DefaultMode: &mode,
			Items: []corev1.KeyToPath{{Key: "repository", Path: "repository"}, {Key: "password", Path: "password"}}}}},
		{Name: "tmp", VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{SizeLimit: quantity("64Mi")}}},
	}
	return pod, nil
}

// The existing storage deny policy stays unchanged. This additive policy only
// permits trusted backup-labelled Pods to the configured MinIO IPv4 on9000.
func BuildBackupNetworkPolicy(endpointIP string) (*networkv1.NetworkPolicy, error) {
	ip := net.ParseIP(endpointIP)
	if ip == nil || ip.To4() == nil {
		return nil, errors.New("backup endpoint must be explicit IPv4")
	}
	port := intstr.FromInt32(9000)
	protocol := corev1.ProtocolTCP
	return &networkv1.NetworkPolicy{TypeMeta: metav1.TypeMeta{APIVersion: "networking.k8s.io/v1", Kind: "NetworkPolicy"}, ObjectMeta: metav1.ObjectMeta{Name: "backup-minio-only", Namespace: StorageNamespace, Labels: map[string]string{LabelManagedBy: BackupManagedBy}},
		Spec: networkv1.NetworkPolicySpec{PodSelector: metav1.LabelSelector{MatchLabels: map[string]string{LabelManagedBy: BackupManagedBy}},
			PolicyTypes: []networkv1.PolicyType{networkv1.PolicyTypeIngress, networkv1.PolicyTypeEgress},
			Egress: []networkv1.NetworkPolicyEgressRule{{To: []networkv1.NetworkPolicyPeer{{IPBlock: &networkv1.IPBlock{CIDR: ip.String() + "/32"}}},
				Ports: []networkv1.NetworkPolicyPort{{Protocol: &protocol, Port: &port}}}}}}, nil
}
