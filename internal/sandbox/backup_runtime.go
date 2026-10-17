package sandbox

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"
	"regexp"
	"slices"

	"github.com/chx739/sandboxd/internal/backuphelper"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

const BackupJobFinalizer = "sandbox.io/workspace-backup-job"
const backupCheckpointAnnotation = "sandbox.io/backup-checkpoint"

var backupHash = regexp.MustCompile(`^[a-f0-9]{64}$`)

// BackupRuntime is a trusted deployment component, not an Agent tool. It keeps
// the barrier after backup completion: the caller must commit metadata (or
// durably record failure) before Finish. No filesystem or metadata is rewound.
type BackupRuntime struct {
	workspace *WorkspaceRuntime
	job       BackupJobConfig
	readLogs  func(context.Context, *corev1.Pod) ([]byte, error)
}

func NewBackupRuntime(w *WorkspaceRuntime, job BackupJobConfig) (*BackupRuntime, error) {
	if w == nil || w.manager.config.RuntimeClass != "gvisor" || job == (BackupJobConfig{}) {
		return nil, errors.New("backup requires trusted gvisor configuration")
	}
	if _, err := BuildBackupNetworkPolicy(job.EndpointIP); err != nil {
		return nil, err
	}
	if _, err := BuildBackupPod(Config{Image: job.ImageRef, RuntimeClass: "gvisor"}, "validate", "workspace-11111111111111111111111111111111", job.SecretName, backuphelper.Request{Operation: "config"}); err != nil {
		return nil, err
	}
	b := &BackupRuntime{workspace: w, job: job}
	b.readLogs = func(ctx context.Context, pod *corev1.Pod) ([]byte, error) {
		limit := int64(backuphelper.MaxOutput + 1)
		stream, err := w.manager.client.CoreV1().Pods(StorageNamespace).GetLogs(pod.Name, &corev1.PodLogOptions{Container: pod.Spec.Containers[0].Name, LimitBytes: &limit}).Stream(ctx)
		if err != nil {
			return nil, ErrWorkspaceUnavailable
		}
		defer stream.Close()
		return io.ReadAll(io.LimitReader(stream, limit))
	}
	return b, nil
}

func (b *BackupRuntime) expected(barrier WorkspaceBarrier) (*corev1.Pod, error) {
	if barrier.Job != b.job || (barrier.Operation != "backup" && barrier.Operation != "restore") {
		return nil, ErrWorkspaceConflict
	}
	request := backuphelper.Request{Operation: "backup", CheckpointID: barrier.CheckpointID}
	if barrier.Operation == "restore" {
		if !barrier.RestoreSource.valid() || barrier.RestoreSource.ImageRef != b.workspace.image {
			return nil, ErrWorkspaceConflict
		}
		request = backuphelper.Request{Operation: "restore", SnapshotID: barrier.RestoreSource.SnapshotID, ManifestSHA256: barrier.RestoreSource.ManifestSHA256, RepositoryCryptoID: barrier.RestoreSource.RepositoryCryptoID}
	}
	pod, err := BuildBackupPod(Config{Image: b.job.ImageRef, RuntimeClass: "gvisor"}, barrier.JobID, barrier.WorkspaceID, b.job.SecretName, request)
	if err != nil {
		return nil, err
	}
	pod.Finalizers = []string{BackupJobFinalizer}
	pod.Annotations = map[string]string{backupCheckpointAnnotation: barrier.CheckpointID}
	return pod, nil
}

func (b *BackupRuntime) current(expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	got, ok := b.workspace.registry.Barrier(expected.WorkspaceID)
	if !ok || got.JobID != expected.JobID || got.CheckpointID != expected.CheckpointID || got.Access != expected.Access || got.Job != b.job || got.Operation != expected.Operation || got.RestoreSource != expected.RestoreSource || (got.Operation != "backup" && got.Operation != "restore") || got.Phase == "released" {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return got, nil
}

// Reserve先冻结工作区，供协调器随后固定Session引用并写PREPARING。
// 此步骤不创建Pod、不启动restic；Start复用同一checkpoint/job才可继续。
func (b *BackupRuntime) Reserve(ctx context.Context, access WorkspaceAccess, checkpointID, jobID string) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	return w.registry.reserveConfiguredBarrier(access.WorkspaceID, checkpointID, jobID, "backup", access, b.job)
}

// Start在创建Pod前先fsync submitted；创建结果不明时只核对原名称/spec/UID，
// 不能把超时当作未创建，再启动另一个执行者。
func (b *BackupRuntime) Start(ctx context.Context, access WorkspaceAccess, checkpointID, jobID string) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	barrier, err := w.registry.reserveConfiguredBarrier(access.WorkspaceID, checkpointID, jobID, "backup", access, b.job)
	if err != nil {
		return barrier, err
	}
	return b.startReserved(ctx, barrier)
}

func (b *BackupRuntime) startReserved(ctx context.Context, barrier WorkspaceBarrier) (WorkspaceBarrier, error) {
	w := b.workspace
	pod, err := b.expected(barrier)
	if err != nil {
		return barrier, err
	}
	if barrier.Phase != "reserved" {
		return b.observe(ctx, barrier)
	}
	policy, _ := BuildBackupNetworkPolicy(b.job.EndpointIP)
	actual, err := w.manager.client.NetworkingV1().NetworkPolicies(StorageNamespace).Create(ctx, policy, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(err) {
		actual, err = w.manager.client.NetworkingV1().NetworkPolicies(StorageNamespace).Get(ctx, policy.Name, metav1.GetOptions{})
	}
	if err != nil {
		return barrier, ErrWorkspaceUnavailable
	}
	if actual.Labels[LabelManagedBy] != BackupManagedBy || !reflect.DeepEqual(actual.Spec, policy.Spec) {
		return barrier, ErrWorkspaceConflict
	}
	barrier, err = w.registry.submitBarrier(barrier)
	if err != nil {
		return barrier, err
	}
	if _, err = w.manager.client.CoreV1().Pods(StorageNamespace).Create(ctx, pod, metav1.CreateOptions{}); err != nil {
		return barrier, ErrWorkspaceUnavailable
	}
	return b.observe(ctx, barrier)
}

func (b *BackupRuntime) ownedPod(ctx context.Context, barrier WorkspaceBarrier) (*corev1.Pod, error) {
	pod, err := b.workspace.manager.client.CoreV1().Pods(StorageNamespace).Get(ctx, barrier.PodName, metav1.GetOptions{})
	if err != nil {
		return nil, ErrWorkspaceUnavailable
	}
	expected, err := b.expected(barrier)
	if err != nil {
		return nil, err
	}
	if pod.UID == "" || (barrier.PodUID != "" && string(pod.UID) != barrier.PodUID) || pod.Name != expected.Name || pod.Namespace != StorageNamespace ||
		pod.Labels[LabelManagedBy] != BackupManagedBy || pod.Labels[LabelWorkspace] != barrier.WorkspaceID || pod.Annotations[backupCheckpointAnnotation] != barrier.CheckpointID ||
		(barrier.Phase != "stopped" && !slices.Contains(pod.Finalizers, BackupJobFinalizer)) ||
		pod.Spec.RuntimeClassName == nil || *pod.Spec.RuntimeClassName != "gvisor" || pod.Spec.ServiceAccountName != "default" || pod.Spec.AutomountServiceAccountToken == nil || *pod.Spec.AutomountServiceAccountToken ||
		pod.Spec.HostNetwork || pod.Spec.HostPID || pod.Spec.HostIPC || len(pod.Spec.Containers) != 1 || len(pod.Spec.InitContainers) != 0 || len(pod.Spec.EphemeralContainers) != 0 || pod.Spec.RestartPolicy != corev1.RestartPolicyNever ||
		!reflect.DeepEqual(pod.Spec.SecurityContext, expected.Spec.SecurityContext) || !reflect.DeepEqual(pod.Spec.Volumes, expected.Spec.Volumes) {
		return nil, ErrWorkspaceConflict
	}
	c, e := pod.Spec.Containers[0], expected.Spec.Containers[0]
	if c.Image != e.Image || !reflect.DeepEqual(c.Command, e.Command) || !reflect.DeepEqual(c.Args, e.Args) || !reflect.DeepEqual(c.Env, e.Env) || !reflect.DeepEqual(c.VolumeMounts, e.VolumeMounts) || !reflect.DeepEqual(c.SecurityContext, e.SecurityContext) {
		return nil, ErrWorkspaceConflict
	}
	return pod, nil
}

func (b *BackupRuntime) observe(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	barrier, err := b.current(expected)
	if err != nil {
		return barrier, err
	}
	if barrier.Phase == "reserved" || barrier.Phase == "stopped" {
		return barrier, nil
	}
	pod, err := b.ownedPod(ctx, barrier)
	if err != nil {
		return barrier, err
	}
	if barrier.Phase == "submitted" {
		barrier, err = b.workspace.registry.identifyBarrier(barrier, string(pod.UID))
		if err != nil {
			return barrier, err
		}
	}
	if containersStopped(pod) {
		if err := b.workspace.healthyNode(ctx, pod.Spec.NodeName); err != nil {
			return barrier, err
		}
		return b.workspace.registry.stopBarrier(barrier, "containers-terminated")
	}
	return barrier, nil
}

type BackupResult struct {
	SnapshotID         string `json:"snapshotId"`
	RepositoryCryptoID string `json:"repositoryCryptoId"`
	ManifestSHA256     string `json:"manifestSha256"`
	ImageID            string `json:"maintenanceImageId"`
	PodUID             string `json:"podUid"`
}

func (b *BackupRuntime) Wait(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, BackupResult, error) {
	for {
		barrier, err := b.observe(ctx, expected)
		if err != nil {
			return barrier, BackupResult{}, err
		}
		if barrier.Phase == "stopped" {
			if barrier.StopReason != "containers-terminated" {
				return barrier, BackupResult{}, ErrWorkspaceUnavailable
			}
			pod, err := b.ownedPod(ctx, barrier)
			if err != nil {
				return barrier, BackupResult{}, err
			}
			// Kubelet may publish container termination before the Pod's terminal
			// phase. Keep the durable barrier and wait within the caller's deadline.
			if containersStopped(pod) && pod.Status.ContainerStatuses[0].State.Terminated.ExitCode == 0 && (pod.Status.Phase == corev1.PodRunning || pod.Status.Phase == corev1.PodPending) {
				if err := pollWorkspace(ctx); err != nil {
					return barrier, BackupResult{}, err
				}
				continue
			}
			if !containersStopped(pod) || pod.Status.Phase != corev1.PodSucceeded || len(pod.Status.ContainerStatuses) != 1 || pod.Status.ContainerStatuses[0].State.Terminated.ExitCode != 0 || pod.Status.ContainerStatuses[0].ImageID == "" {
				return barrier, BackupResult{}, fmt.Errorf("%w: maintenance completion status", ErrWorkspaceUnavailable)
			}
			raw, err := b.readLogs(ctx, pod)
			if err != nil || len(raw) > backuphelper.MaxOutput {
				return barrier, BackupResult{}, fmt.Errorf("%w: bounded maintenance log read", ErrWorkspaceUnavailable)
			}
			var result struct {
				OK           bool   `json:"ok"`
				Operation    string `json:"operation"`
				CheckpointID string `json:"checkpointId"`
				SnapshotID   string `json:"snapshotId"`
				Repository   struct {
					ID string `json:"repositoryCryptoId"`
				} `json:"repository"`
				Manifest struct {
					SHA256 string `json:"sha256"`
				} `json:"manifest"`
			}
			if json.Unmarshal(raw, &result) != nil || !result.OK || result.Operation != barrier.Operation || (barrier.Operation == "backup" && result.CheckpointID != barrier.CheckpointID) || !backupHash.MatchString(result.SnapshotID) || !backupHash.MatchString(result.Repository.ID) || !backupHash.MatchString(result.Manifest.SHA256) || (barrier.Operation == "restore" && (result.SnapshotID != barrier.RestoreSource.SnapshotID || result.Repository.ID != barrier.RestoreSource.RepositoryCryptoID || result.Manifest.SHA256 != barrier.RestoreSource.ManifestSHA256)) {
				return barrier, BackupResult{}, fmt.Errorf("%w: maintenance result contract", ErrWorkspaceUnavailable)
			}
			return barrier, BackupResult{result.SnapshotID, result.Repository.ID, result.Manifest.SHA256, pod.Status.ContainerStatuses[0].ImageID, string(pod.UID)}, nil
		}
		if err := pollWorkspace(ctx); err != nil {
			return barrier, BackupResult{}, err
		}
	}
}

// Abort只确认维护进程终止，不删除快照或强行unlock。调用方仍需持久化
// 失败/取消结果，再解除屏障；网络超时本身不能作为终止证明。
func (b *BackupRuntime) Abort(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	barrier, err := b.current(expected)
	if err != nil {
		return barrier, err
	}
	if barrier.Phase == "reserved" {
		return b.workspace.registry.stopBarrier(barrier, "never-submitted")
	}
	for {
		barrier, err = b.observe(ctx, barrier)
		if err != nil {
			return barrier, err
		}
		if barrier.Phase == "stopped" {
			return barrier, nil
		}
		pod, err := b.ownedPod(ctx, barrier)
		if err != nil {
			return barrier, err
		}
		if pod.DeletionTimestamp == nil {
			uid := pod.UID
			if err := b.workspace.manager.client.CoreV1().Pods(StorageNamespace).Delete(ctx, pod.Name, metav1.DeleteOptions{Preconditions: &metav1.Preconditions{UID: &uid}}); err != nil {
				return barrier, ErrWorkspaceUnavailable
			}
		}
		if err := pollWorkspace(ctx); err != nil {
			return barrier, err
		}
	}
}

// Finish须在上层持久化提交/失败结果之后调用。停止证明已经fsync，
// 因此清理重试才可接受NotFound；结果不明的执行者不能进入这里。
func (b *BackupRuntime) Finish(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	if expected.Operation != "backup" {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return b.finish(ctx, expected, false)
}

func (b *BackupRuntime) finish(ctx context.Context, expected WorkspaceBarrier, verifiedRestore bool) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	if old, ok := w.registry.Barrier(expected.WorkspaceID); ok && old.Phase == "released" && old.JobID == expected.JobID && old.CheckpointID == expected.CheckpointID && old.Access == expected.Access && old.Job == b.job && old.Operation == expected.Operation && old.RestoreSource == expected.RestoreSource && old.RestoreVerified == verifiedRestore {
		return old, nil
	}
	barrier, err := b.current(expected)
	if err != nil {
		return barrier, err
	}
	if verifiedRestore && !barrier.RestoreVerified {
		var result BackupResult
		barrier, result, err = b.Wait(ctx, barrier)
		if err != nil || result.RepositoryCryptoID != barrier.RestoreSource.RepositoryCryptoID {
			return barrier, ErrWorkspaceConflict
		}
		barrier, err = w.registry.verifyRestore(barrier)
		if err != nil {
			return barrier, err
		}
	}
	if barrier.Phase != "stopped" {
		return barrier, ErrWorkspaceConflict
	}
	if barrier.StopReason == "containers-terminated" {
		pod, err := b.ownedPod(ctx, barrier)
		if err != nil {
			_, getErr := w.manager.client.CoreV1().Pods(StorageNamespace).Get(ctx, barrier.PodName, metav1.GetOptions{})
			if !apierrors.IsNotFound(getErr) {
				return barrier, err
			}
		} else {
			finalizers := slices.DeleteFunc(slices.Clone(pod.Finalizers), func(f string) bool { return f == BackupJobFinalizer })
			patch, _ := json.Marshal([]map[string]any{{"op": "test", "path": "/metadata/uid", "value": string(pod.UID)}, {"op": "test", "path": "/metadata/resourceVersion", "value": pod.ResourceVersion}, {"op": "replace", "path": "/metadata/finalizers", "value": finalizers}})
			if _, err = w.manager.client.CoreV1().Pods(StorageNamespace).Patch(ctx, pod.Name, types.JSONPatchType, patch, metav1.PatchOptions{}); err != nil && !apierrors.IsNotFound(err) {
				return barrier, ErrWorkspaceUnavailable
			}
			uid := pod.UID
			if err = w.manager.client.CoreV1().Pods(StorageNamespace).Delete(ctx, pod.Name, metav1.DeleteOptions{Preconditions: &metav1.Preconditions{UID: &uid}}); err != nil && !apierrors.IsNotFound(err) {
				return barrier, ErrWorkspaceUnavailable
			}
		}
	}
	if verifiedRestore {
		return w.registry.completeRestore(barrier)
	}
	return w.registry.releaseBarrier(barrier)
}
