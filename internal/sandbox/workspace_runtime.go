package sandbox

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"strconv"
	"sync"
	"time"

	filetool "github.com/chx739/sandboxd/internal/workspace"
	corev1 "k8s.io/api/core/v1"
	networkv1 "k8s.io/api/networking/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

var ErrWorkspaceUnavailable = errors.New("workspace execution unavailable or outcome uncertain")

type WorkspaceRuntime struct {
	mu       sync.Mutex // single Worker MVP: serialize create/stop and maintenance jobs
	manager  *Manager
	registry *WorkspaceRegistry
	server   string
	image    string
}

func NewWorkspaceRuntime(manager *Manager, registry *WorkspaceRegistry, server, image string) (*WorkspaceRuntime, error) {
	if manager == nil || registry == nil || manager.config.RuntimeClass == "" || image == "" {
		return nil, errors.New("persistent runtime requires registry, image and RuntimeClass")
	}
	if _, _, err := BuildStorageRootVolume(server); err != nil {
		return nil, err
	}
	return &WorkspaceRuntime{manager: manager, registry: registry, server: server, image: image}, nil
}

func (w *WorkspaceRuntime) Registry() *WorkspaceRegistry { return w.registry }

func (w *WorkspaceRuntime) Prepare(ctx context.Context, workspaceID, sessionID, branchID string) (WorkspaceRecord, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	record, err := w.registry.Register(workspaceID, sessionID, branchID)
	if err != nil {
		return record, err
	}
	if record.Phase != "registered" {
		return record, nil
	}
	if err := w.ensureStorage(ctx); err != nil {
		return record, err
	}
	id, err := randomID()
	if err != nil {
		return record, err
	}
	pod, err := BuildWorkspaceInitPod(Config{Image: w.image, RuntimeClass: w.manager.config.RuntimeClass}, id, workspaceID)
	if err != nil {
		return record, err
	}
	created, err := w.manager.client.CoreV1().Pods(StorageNamespace).Create(ctx, pod, metav1.CreateOptions{})
	if err != nil {
		return record, err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		uid := created.UID
		_ = w.manager.client.CoreV1().Pods(StorageNamespace).Delete(cleanup, created.Name, metav1.DeleteOptions{Preconditions: &metav1.Preconditions{UID: &uid}})
	}()
	for {
		current, err := w.manager.client.CoreV1().Pods(StorageNamespace).Get(ctx, created.Name, metav1.GetOptions{})
		if err != nil {
			return record, err
		}
		if current.UID != created.UID {
			return record, ErrWorkspaceConflict
		}
		if current.Status.Phase == corev1.PodSucceeded {
			break
		}
		if current.Status.Phase == corev1.PodFailed {
			return record, errors.New("trusted workspace initialization failed")
		}
		if err := pollWorkspace(ctx); err != nil {
			return record, err
		}
	}
	pv, pvc, err := BuildWorkspaceVolume(w.manager.config.Namespace, w.server, "/workspaces", workspaceID)
	if err != nil {
		return record, err
	}
	if err := w.ensureVolume(ctx, pv, pvc); err != nil {
		return record, err
	}
	return w.registry.Ready(workspaceID)
}

func (w *WorkspaceRuntime) ensureStorage(ctx context.Context) error {
	labels := map[string]string{LabelManagedBy: "sandboxd-storage", "pod-security.kubernetes.io/enforce": "restricted"}
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: StorageNamespace, Labels: labels}}
	existing, err := w.manager.client.CoreV1().Namespaces().Create(ctx, ns, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(err) {
		existing, err = w.manager.client.CoreV1().Namespaces().Get(ctx, StorageNamespace, metav1.GetOptions{})
	}
	if err != nil {
		return err
	}
	for key, value := range labels {
		if existing.Labels[key] != value {
			return errors.New("storage namespace ownership or security differs")
		}
	}
	policy := &networkv1.NetworkPolicy{ObjectMeta: metav1.ObjectMeta{Name: "storage-deny-network", Namespace: StorageNamespace, Labels: map[string]string{LabelManagedBy: "sandboxd-storage"}}, Spec: networkv1.NetworkPolicySpec{PodSelector: metav1.LabelSelector{}, PolicyTypes: []networkv1.PolicyType{networkv1.PolicyTypeIngress, networkv1.PolicyTypeEgress}}}
	actual, err := w.manager.client.NetworkingV1().NetworkPolicies(StorageNamespace).Create(ctx, policy, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(err) {
		actual, err = w.manager.client.NetworkingV1().NetworkPolicies(StorageNamespace).Get(ctx, policy.Name, metav1.GetOptions{})
	}
	if err != nil {
		return err
	}
	if actual.Labels[LabelManagedBy] != "sandboxd-storage" || !reflect.DeepEqual(actual.Spec, policy.Spec) {
		return errors.New("storage network policy differs")
	}
	pv, pvc, err := BuildStorageRootVolume(w.server)
	if err != nil {
		return err
	}
	return w.ensureVolume(ctx, pv, pvc)
}

func (w *WorkspaceRuntime) ensureVolume(ctx context.Context, pv *corev1.PersistentVolume, pvc *corev1.PersistentVolumeClaim) error {
	actual, err := w.manager.client.CoreV1().PersistentVolumes().Create(ctx, pv, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(err) {
		actual, err = w.manager.client.CoreV1().PersistentVolumes().Get(ctx, pv.Name, metav1.GetOptions{})
	}
	if err != nil {
		return err
	}
	if actual.Labels[LabelManagedBy] != pv.Labels[LabelManagedBy] || actual.Labels[LabelWorkspace] != pv.Labels[LabelWorkspace] || !reflect.DeepEqual(actual.Spec.NFS, pv.Spec.NFS) || !reflect.DeepEqual(actual.Spec.MountOptions, pv.Spec.MountOptions) || actual.Spec.PersistentVolumeReclaimPolicy != corev1.PersistentVolumeReclaimRetain || actual.Spec.ClaimRef == nil || actual.Spec.ClaimRef.Name != pvc.Name || actual.Spec.ClaimRef.Namespace != pvc.Namespace {
		return errors.New("existing NFS volume does not match its durable workspace")
	}
	claim, err := w.manager.client.CoreV1().PersistentVolumeClaims(pvc.Namespace).Create(ctx, pvc, metav1.CreateOptions{})
	if apierrors.IsAlreadyExists(err) {
		claim, err = w.manager.client.CoreV1().PersistentVolumeClaims(pvc.Namespace).Get(ctx, pvc.Name, metav1.GetOptions{})
	}
	if err != nil {
		return err
	}
	if claim.Labels[LabelManagedBy] != pvc.Labels[LabelManagedBy] || claim.Labels[LabelWorkspace] != pvc.Labels[LabelWorkspace] || claim.Spec.VolumeName != pv.Name || claim.Spec.StorageClassName == nil || *claim.Spec.StorageClassName != "" {
		return errors.New("existing workspace PVC does not match")
	}
	return nil
}

func (w *WorkspaceRuntime) Claim(ctx context.Context, workspaceID, taskID, attemptID string) (WorkspaceRecord, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	id, err := randomID()
	if err != nil {
		return WorkspaceRecord{}, err
	}
	record, err := w.registry.BeginClaim(workspaceID, taskID, attemptID, id, w.image)
	if err != nil {
		return record, err
	}
	access := accessForRecord(record)
	if record.Phase == "bound" {
		_, err := w.livePod(ctx, record)
		return record, err
	}
	var pod *corev1.Pod
	if record.Owner.CreateSubmitted {
		// 请求结果不明时只能查找同名、同归属对象，不能再创建另一个写入者。
		pod, err = w.manager.client.CoreV1().Pods(w.manager.config.Namespace).Get(ctx, record.Owner.PodName, metav1.GetOptions{})
		if err != nil {
			return record, ErrWorkspaceUnavailable
		}
	} else {
		cfg := w.manager.config
		cfg.Image = w.image
		pod, err = BuildWorkspacePod(cfg, record.Owner.SandboxID, WorkspaceMount{WorkspaceID: workspaceID, TaskID: taskID, AttemptID: attemptID, Generation: record.Generation})
		if err != nil {
			return record, err
		}
		if _, err = w.registry.CreationSubmitted(access); err != nil {
			return record, err
		}
		pod, err = w.manager.client.CoreV1().Pods(cfg.Namespace).Create(ctx, pod, metav1.CreateOptions{})
		if err != nil {
			return record, ErrWorkspaceUnavailable
		}
	}
	if !ownedWorkspacePod(pod, record) {
		return record, ErrWorkspaceConflict
	}
	record, err = w.registry.Identify(access, string(pod.UID), pod.Spec.NodeName)
	if err != nil {
		return record, err
	}
	for {
		pod, err = w.manager.client.CoreV1().Pods(w.manager.config.Namespace).Get(ctx, record.Owner.PodName, metav1.GetOptions{})
		if err != nil {
			return record, err
		}
		if !ownedWorkspacePod(pod, record) {
			return record, ErrWorkspaceConflict
		}
		if pod.DeletionTimestamp != nil || pod.Status.Phase == corev1.PodFailed || pod.Status.Phase == corev1.PodSucceeded {
			return record, ErrWorkspaceUnavailable
		}
		if isPodReady(pod) && len(pod.Status.ContainerStatuses) == 1 && pod.Status.ContainerStatuses[0].State.Running != nil && pod.Status.ContainerStatuses[0].ImageID != "" {
			break
		}
		if err := pollWorkspace(ctx); err != nil {
			return record, err
		}
	}
	// 绑定前必须实际验证持久目录，而不是把 PodReady 当存储健康。
	out, stderr := &boundedWorkspaceOutput{limit: 4096}, &boundedWorkspaceOutput{limit: 4096}
	code, err := w.manager.Exec(ctx, pod.Name, []string{"/usr/local/bin/sandbox-filetool", "--check"}, nil, out, stderr)
	if err != nil || code != 0 || out.overflow {
		return record, ErrWorkspaceUnavailable
	}
	var health struct {
		Writable bool `json:"writable"`
	}
	if json.Unmarshal(out.Bytes(), &health) != nil || !health.Writable {
		return record, ErrWorkspaceUnavailable
	}
	return w.registry.Bind(access, string(pod.UID), pod.Spec.NodeName, pod.Status.ContainerStatuses[0].ImageID)
}

func accessForRecord(record WorkspaceRecord) WorkspaceAccess {
	return WorkspaceAccess{WorkspaceID: record.WorkspaceID, TaskID: record.Owner.TaskID, AttemptID: record.Owner.AttemptID, SandboxID: record.Owner.SandboxID, Generation: record.Generation}
}

func ownedWorkspacePod(pod *corev1.Pod, record WorkspaceRecord) bool {
	return pod.Name == record.Owner.PodName && (record.Owner.PodUID == "" || string(pod.UID) == record.Owner.PodUID) &&
		pod.Labels[LabelManagedBy] == ValueManagedBy && pod.Labels[LabelWorkspace] == record.WorkspaceID && pod.Labels[LabelTask] == record.Owner.TaskID && pod.Labels[LabelAttempt] == record.Owner.AttemptID && pod.Annotations[AnnotationGeneration] == strconv.FormatUint(record.Generation, 10) && (record.Owner.StopConfirmed || slices.Contains(pod.Finalizers, WorkspaceWriterFinalizer))
}

func (w *WorkspaceRuntime) livePod(ctx context.Context, record WorkspaceRecord) (*corev1.Pod, error) {
	pod, err := w.manager.client.CoreV1().Pods(w.manager.config.Namespace).Get(ctx, record.Owner.PodName, metav1.GetOptions{})
	if err != nil {
		return nil, ErrWorkspaceUnavailable
	}
	if !ownedWorkspacePod(pod, record) || pod.DeletionTimestamp != nil || !isPodReady(pod) || pod.Status.Phase != corev1.PodRunning || len(pod.Status.ContainerStatuses) != 1 || pod.Status.ContainerStatuses[0].State.Running == nil || pod.Status.ContainerStatuses[0].ImageID != record.ImageID {
		return nil, ErrWorkspaceUnavailable
	}
	return pod, nil
}

// Stop 把物理终止证明先落盘，再移除 finalizer。对象消失、租约过期都不算证明。
func (w *WorkspaceRuntime) Stop(ctx context.Context, access WorkspaceAccess) (WorkspaceRecord, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	record, err := w.registry.Get(access.WorkspaceID)
	if err != nil || !matches(record, access) {
		return record, ErrWorkspaceConflict
	}
	if record.Phase == "released" {
		return record, nil
	}
	record, err = w.registry.StartStop(access)
	if err != nil {
		return record, err
	}
	if !record.Owner.CreateSubmitted {
		if _, err = w.registry.ConfirmStopped(access, "", "never-submitted"); err != nil {
			return record, err
		}
		return w.registry.Released(access)
	}
	for {
		pod, err := w.manager.client.CoreV1().Pods(w.manager.config.Namespace).Get(ctx, record.Owner.PodName, metav1.GetOptions{})
		if apierrors.IsNotFound(err) && record.Owner.StopConfirmed {
			return w.registry.Released(access)
		}
		if err != nil {
			return record, ErrWorkspaceUnavailable
		}
		if !ownedWorkspacePod(pod, record) {
			return record, ErrWorkspaceConflict
		}
		if !record.Owner.StopConfirmed && containersStopped(pod) {
			if err := w.healthyNode(ctx, pod.Spec.NodeName); err != nil {
				return record, err
			}
			record, err = w.registry.ConfirmStopped(access, string(pod.UID), "containers-terminated")
			if err != nil {
				return record, err
			}
		}
		if pod.DeletionTimestamp == nil {
			uid := pod.UID
			if err := w.manager.client.CoreV1().Pods(pod.Namespace).Delete(ctx, pod.Name, metav1.DeleteOptions{Preconditions: &metav1.Preconditions{UID: &uid}}); err != nil {
				return record, err
			}
		}
		if record.Owner.StopConfirmed {
			finalizers := []string{}
			for _, value := range pod.Finalizers {
				if value != WorkspaceWriterFinalizer {
					finalizers = append(finalizers, value)
				}
			}
			patch, _ := json.Marshal([]map[string]any{{"op": "test", "path": "/metadata/uid", "value": string(pod.UID)}, {"op": "test", "path": "/metadata/resourceVersion", "value": pod.ResourceVersion}, {"op": "replace", "path": "/metadata/finalizers", "value": finalizers}})
			_, err := w.manager.client.CoreV1().Pods(pod.Namespace).Patch(ctx, pod.Name, types.JSONPatchType, patch, metav1.PatchOptions{})
			if apierrors.IsConflict(err) || apierrors.IsInvalid(err) {
				if err := pollWorkspace(ctx); err != nil {
					return record, err
				}
				continue
			}
			if err != nil && !apierrors.IsNotFound(err) {
				return record, err
			}
			return w.registry.Released(access)
		}
		if err := pollWorkspace(ctx); err != nil {
			return record, err
		}
	}
}

func containersStopped(pod *corev1.Pod) bool {
	if pod.Spec.RestartPolicy != corev1.RestartPolicyNever || len(pod.Status.ContainerStatuses) != len(pod.Spec.Containers) || len(pod.Spec.Containers) == 0 {
		return false
	}
	for _, status := range pod.Status.ContainerStatuses {
		if status.State.Terminated == nil {
			return false
		}
	}
	if len(pod.Status.InitContainerStatuses) != len(pod.Spec.InitContainers) || len(pod.Status.EphemeralContainerStatuses) != len(pod.Spec.EphemeralContainers) {
		return false
	}
	for _, status := range append(slices.Clone(pod.Status.InitContainerStatuses), pod.Status.EphemeralContainerStatuses...) {
		if status.State.Terminated == nil {
			return false
		}
	}
	return true
}

func (w *WorkspaceRuntime) healthyNode(ctx context.Context, name string) error {
	node, err := w.manager.client.CoreV1().Nodes().Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return ErrWorkspaceUnavailable
	}
	ready := false
	for _, condition := range node.Status.Conditions {
		if condition.Type == corev1.NodeReady && condition.Status == corev1.ConditionTrue {
			ready = true
		}
	}
	lease, err := w.manager.client.CoordinationV1().Leases("kube-node-lease").Get(ctx, name, metav1.GetOptions{})
	if err != nil || !ready || lease.Spec.RenewTime == nil || time.Since(lease.Spec.RenewTime.Time) > 30*time.Second || time.Until(lease.Spec.RenewTime.Time) > 5*time.Second {
		return ErrWorkspaceUnavailable
	}
	return nil
}

type WorkspaceFileResult struct {
	Status int
	Body   json.RawMessage
}

func (w *WorkspaceRuntime) Files(ctx context.Context, access WorkspaceAccess, request filetool.Request) (WorkspaceFileResult, error) {
	if err := request.Validate(); err != nil {
		return WorkspaceFileResult{}, err
	}
	var result WorkspaceFileResult
	err := w.registry.WithOwner(ctx, access, func(record WorkspaceRecord) error {
		pod, err := w.livePod(ctx, record)
		if err != nil {
			return err
		}
		body, err := json.Marshal(request)
		if err != nil {
			return err
		}
		out, stderr := &boundedWorkspaceOutput{limit: filetool.MaxResponseBytes}, &boundedWorkspaceOutput{limit: 4096}
		code, execErr := w.manager.Exec(ctx, pod.Name, []string{"/usr/local/bin/sandbox-filetool"}, bytes.NewReader(body), out, stderr)
		if out.overflow || !json.Valid(out.Bytes()) {
			return ErrWorkspaceUnavailable
		}
		result = WorkspaceFileResult{Status: 200, Body: bytes.Clone(out.Bytes())}
		if code == 2 {
			result.Status = 403
			return nil
		}
		if execErr != nil || code != 0 {
			return ErrWorkspaceUnavailable
		}
		return nil
	})
	return result, err
}

type boundedWorkspaceOutput struct {
	buffer   bytes.Buffer
	limit    int
	overflow bool
}

func (b *boundedWorkspaceOutput) Bytes() []byte { return b.buffer.Bytes() }
func (b *boundedWorkspaceOutput) Write(data []byte) (int, error) {
	n := len(data)
	remaining := max(0, b.limit-b.buffer.Len())
	if len(data) > remaining {
		data = data[:remaining]
		b.overflow = true
	}
	b.buffer.Write(data)
	return n, nil
}

func pollWorkspace(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return fmt.Errorf("workspace operation interrupted: %w", ctx.Err())
	case <-time.After(100 * time.Millisecond):
		return nil
	}
}
