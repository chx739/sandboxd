package sandbox

import (
	"errors"
	"net"
	"path"
	"regexp"
	"strconv"
	"strings"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	LabelWorkspace           = "sandbox.io/workspace"
	LabelTask                = "sandbox.io/task"
	LabelAttempt             = "sandbox.io/attempt"
	AnnotationGeneration     = "sandbox.io/workspace-generation"
	WorkspaceWriterFinalizer = "sandbox.io/workspace-writer"
	StorageNamespace         = "sandboxd-storage"
	StorageRootClaim         = "sandboxd-workspace-root"
)

var workspaceIDPattern = regexp.MustCompile(`^workspace-[a-f0-9]{32}$`)
var runtimeIDPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,62}$`)

// WorkspaceMount must come from the trusted Go ownership registry, never an
// arbitrary LLM tool argument. A PVC always represents exactly one workspace.
type WorkspaceMount struct {
	WorkspaceID string
	TaskID      string
	AttemptID   string
	Generation  uint64
}

func (mount WorkspaceMount) Validate() error {
	if !workspaceIDPattern.MatchString(mount.WorkspaceID) ||
		!runtimeIDPattern.MatchString(mount.TaskID) || !strings.HasPrefix(mount.TaskID, "task-") ||
		!runtimeIDPattern.MatchString(mount.AttemptID) || !strings.HasPrefix(mount.AttemptID, "attempt-") || mount.Generation == 0 {
		return errors.New("invalid workspace binding identity")
	}
	return nil
}

// BuildWorkspacePod keeps the existing security baseline and bypasses the warm
// pool. Kubernetes cannot change the mounted workspace of an already created Pod.
func BuildWorkspacePod(cfg Config, id string, mount WorkspaceMount) (*corev1.Pod, error) {
	if err := mount.Validate(); err != nil {
		return nil, err
	}
	if cfg.RuntimeClass == "" {
		return nil, errors.New("persistent workspace requires an explicit sandbox RuntimeClass")
	}
	if !runtimeIDPattern.MatchString(id) {
		return nil, errors.New("invalid sandbox id")
	}
	pod := BuildPod(cfg, id)
	// 保留对象直到可信 Runtime 已记录容器终止；NotFound 不能单独证明停止。
	pod.Finalizers = []string{WorkspaceWriterFinalizer}
	pod.Labels[LabelState] = string(StateBusy)
	pod.Labels[LabelSource] = "workspace"
	pod.Labels[LabelWorkspace] = mount.WorkspaceID
	pod.Labels[LabelTask] = mount.TaskID
	pod.Labels[LabelAttempt] = mount.AttemptID
	pod.Annotations = map[string]string{AnnotationGeneration: strconv.FormatUint(mount.Generation, 10)}
	for i := range pod.Spec.Volumes {
		if pod.Spec.Volumes[i].Name == "workspace" {
			pod.Spec.Volumes[i].VolumeSource = corev1.VolumeSource{PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: mount.WorkspaceID}}
		}
	}
	return pod, nil
}

// BuildWorkspaceInitPod 是固定维护入口，永远不能使用 Agent 提交的 PodSpec。
func BuildWorkspaceInitPod(cfg Config, id, workspaceID string) (*corev1.Pod, error) {
	if !workspaceIDPattern.MatchString(workspaceID) || !runtimeIDPattern.MatchString(id) || cfg.RuntimeClass == "" || cfg.Image == "" {
		return nil, errors.New("invalid workspace initialization input")
	}
	cfg.Namespace = StorageNamespace
	pod := BuildPod(cfg, id)
	pod.Name = "workspace-init-" + id
	pod.Labels = map[string]string{LabelManagedBy: "sandboxd-storage", LabelWorkspace: workspaceID}
	pod.Spec.ServiceAccountName = "default"
	pod.Spec.Containers[0].Command = []string{"/usr/local/bin/sandbox-workspace-init", workspaceID}
	pod.Spec.Containers[0].VolumeMounts = []corev1.VolumeMount{{Name: "workspace-root", MountPath: "/workspace-root"}, {Name: "tmp", MountPath: "/tmp"}}
	pod.Spec.Volumes = []corev1.Volume{
		{Name: "workspace-root", VolumeSource: corev1.VolumeSource{PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: StorageRootClaim}}},
		pod.Spec.Volumes[2],
	}
	deadline := int64(60)
	pod.Spec.ActiveDeadlineSeconds = &deadline
	return pod, nil
}

func BuildStorageRootVolume(server string) (*corev1.PersistentVolume, *corev1.PersistentVolumeClaim, error) {
	if net.ParseIP(server) == nil {
		return nil, nil, errors.New("invalid trusted NFS server")
	}
	// Reuse mount flags/Retain and replace only trusted administrative identity.
	pv, pvc, err := BuildWorkspaceVolume(StorageNamespace, server, "/workspaces", "workspace-00000000000000000000000000000000")
	if err != nil {
		return nil, nil, err
	}
	pv.Name = StorageRootClaim
	pv.Labels = map[string]string{LabelManagedBy: "sandboxd-storage"}
	pv.Spec.NFS.Path = "/workspaces"
	pv.Spec.ClaimRef.Name = StorageRootClaim
	pvc.Name = StorageRootClaim
	pvc.Labels = map[string]string{LabelManagedBy: "sandboxd-storage"}
	pvc.Spec.VolumeName = StorageRootClaim
	return pv, pvc, nil
}

// BuildWorkspaceVolume uses a trusted server/export setting and a generated ID;
// callers cannot supply a free NFS path, storage class or volume/claim name.
// 64Mi is advisory for NFS. The helper independently enforces bounded writes;
// it is not a server-side disk quota against an arbitrary process in the Pod.
func BuildWorkspaceVolume(namespace, server, exportRoot, workspaceID string) (*corev1.PersistentVolume, *corev1.PersistentVolumeClaim, error) {
	if !workspaceIDPattern.MatchString(workspaceID) || !runtimeIDPattern.MatchString(namespace) {
		return nil, nil, errors.New("invalid workspace volume identity")
	}
	if net.ParseIP(server) == nil {
		return nil, nil, errors.New("NFS server must be a trusted explicit IP")
	}
	if exportRoot != "/workspaces" {
		return nil, nil, errors.New("NFS export must be the configured workspace root")
	}
	class := ""
	quantity := resource.MustParse("64Mi")
	labels := map[string]string{LabelManagedBy: ValueManagedBy, LabelWorkspace: workspaceID}
	pvName := "sandboxd-" + workspaceID
	pv := &corev1.PersistentVolume{
		ObjectMeta: metav1.ObjectMeta{Name: pvName, Labels: labels},
		Spec: corev1.PersistentVolumeSpec{
			Capacity:                      corev1.ResourceList{corev1.ResourceStorage: quantity},
			AccessModes:                   []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
			PersistentVolumeReclaimPolicy: corev1.PersistentVolumeReclaimRetain,
			StorageClassName:              "",
			MountOptions:                  []string{"nfsvers=4.1", "proto=tcp", "hard", "timeo=50", "retrans=2"},
			ClaimRef:                      &corev1.ObjectReference{Namespace: namespace, Name: workspaceID},
			PersistentVolumeSource:        corev1.PersistentVolumeSource{NFS: &corev1.NFSVolumeSource{Server: server, Path: path.Join(exportRoot, workspaceID)}},
		},
	}
	pvc := &corev1.PersistentVolumeClaim{
		ObjectMeta: metav1.ObjectMeta{Name: workspaceID, Namespace: namespace, Labels: map[string]string{LabelManagedBy: ValueManagedBy, LabelWorkspace: workspaceID}},
		Spec: corev1.PersistentVolumeClaimSpec{
			AccessModes:      []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
			StorageClassName: &class, VolumeName: pvName,
			Resources: corev1.VolumeResourceRequirements{Requests: corev1.ResourceList{corev1.ResourceStorage: quantity}},
		},
	}
	return pv, pvc, nil
}
