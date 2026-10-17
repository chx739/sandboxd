package sandbox

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	coordv1 "k8s.io/api/coordination/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/fake"
	clienttesting "k8s.io/client-go/testing"
)

func runtimeFixture(t *testing.T, terminated bool) (*WorkspaceRuntime, *fake.Clientset, WorkspaceAccess, *corev1.Pod) {
	t.Helper()
	registry, _ := registryFixture(t)
	access := boundFixture(t, registry)
	pod, err := BuildWorkspacePod(Config{Namespace: "sandboxd-demo", Image: "fixed@sha256:fixture", RuntimeClass: "gvisor"}, "pod-a", WorkspaceMount{WorkspaceID: testWorkspaceID, TaskID: "task-a", AttemptID: "attempt-a", Generation: 1})
	if err != nil {
		t.Fatal(err)
	}
	pod.UID = types.UID("uid-a")
	pod.ResourceVersion = "1"
	pod.Spec.NodeName = "node-a"
	pod.Status.Phase = corev1.PodRunning
	pod.Status.Conditions = []corev1.PodCondition{{Type: corev1.PodReady, Status: corev1.ConditionTrue}}
	state := corev1.ContainerState{Running: &corev1.ContainerStateRunning{}}
	if terminated {
		state = corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 0}}
		pod.Status.Phase = corev1.PodSucceeded
	}
	pod.Status.ContainerStatuses = []corev1.ContainerStatus{{Name: "sandbox", ImageID: "containerd-image-id-a", State: state}}
	node := &corev1.Node{ObjectMeta: metav1.ObjectMeta{Name: "node-a", UID: "node-uid-a"}, Status: corev1.NodeStatus{Conditions: []corev1.NodeCondition{{Type: corev1.NodeReady, Status: corev1.ConditionTrue}}}}
	now := metav1.NewMicroTime(time.Now())
	holder := "node-a"
	lease := &coordv1.Lease{ObjectMeta: metav1.ObjectMeta{Name: "node-a", Namespace: "kube-node-lease", OwnerReferences: []metav1.OwnerReference{{Kind: "Node", UID: node.UID, Name: node.Name}}}, Spec: coordv1.LeaseSpec{HolderIdentity: &holder, RenewTime: &now}}
	client := fake.NewSimpleClientset(pod.DeepCopy(), node, lease)
	manager := NewManager(client, nil, Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	w, err := NewWorkspaceRuntime(manager, registry, "172.18.0.3", "fixed@sha256:fixture")
	if err != nil {
		t.Fatal(err)
	}
	return w, client, access, pod
}

func TestWorkspaceStopPersistsTerminationBeforeReleasing(t *testing.T) {
	w, client, access, pod := runtimeFixture(t, true)
	deletedWithUID := false
	client.PrependReactor("delete", "pods", func(action clienttesting.Action) (bool, runtime.Object, error) {
		options := action.(clienttesting.DeleteAction).GetDeleteOptions()
		deletedWithUID = options.Preconditions != nil && options.Preconditions.UID != nil && *options.Preconditions.UID == pod.UID
		return true, nil, nil // fake API holds object until finalizer patch
	})
	record, err := w.Stop(context.Background(), access)
	if err != nil {
		t.Fatal(err)
	}
	if !deletedWithUID || !record.Owner.StopConfirmed || record.Phase != "released" || record.StopReason != "containers-terminated" {
		t.Fatal(record)
	}
	actual, _ := client.CoreV1().Pods(pod.Namespace).Get(context.Background(), pod.Name, metav1.GetOptions{})
	if len(actual.Finalizers) != 0 {
		t.Fatal("writer finalizer not removed")
	}
	if _, err := w.registry.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture"); err != nil {
		t.Fatal(err)
	}
}

func TestWorkspaceMissingPodIsNotPhysicalStopProof(t *testing.T) {
	w, client, access, pod := runtimeFixture(t, true)
	client.CoreV1().Pods(pod.Namespace).Delete(context.Background(), pod.Name, metav1.DeleteOptions{})
	if _, err := w.Stop(context.Background(), access); !errors.Is(err, ErrWorkspaceUnavailable) {
		t.Fatal(err)
	}
	record, _ := w.registry.Get(testWorkspaceID)
	if record.Owner.StopConfirmed || record.Phase != "stopping" {
		t.Fatal("NotFound incorrectly released writer", record)
	}
}

func TestWorkspaceCanFinishReleaseAfterFinalizerWasAlreadyRemoved(t *testing.T) {
	w, client, access, pod := runtimeFixture(t, true)
	w.registry.StartStop(access)
	if _, err := w.registry.ConfirmStopped(access, string(pod.UID), "containers-terminated"); err != nil {
		t.Fatal(err)
	}
	pod.Finalizers = nil
	if _, err := client.CoreV1().Pods(pod.Namespace).Update(context.Background(), pod, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	record, err := w.Stop(context.Background(), access)
	if err != nil || record.Phase != "released" || !record.Owner.StopConfirmed {
		t.Fatal(record, err)
	}
}

func TestWorkspaceLeaseExpiredOrNodeNotReadyBlocksStop(t *testing.T) {
	for _, mode := range []string{"expired", "not-ready"} {
		t.Run(mode, func(t *testing.T) {
			w, client, access, _ := runtimeFixture(t, true)
			if mode == "expired" {
				lease, _ := client.CoordinationV1().Leases("kube-node-lease").Get(context.Background(), "node-a", metav1.GetOptions{})
				old := metav1.NewMicroTime(time.Now().Add(-time.Minute))
				lease.Spec.RenewTime = &old
				client.CoordinationV1().Leases("kube-node-lease").Update(context.Background(), lease, metav1.UpdateOptions{})
			} else {
				node, _ := client.CoreV1().Nodes().Get(context.Background(), "node-a", metav1.GetOptions{})
				node.Status.Conditions[0].Status = corev1.ConditionUnknown
				client.CoreV1().Nodes().UpdateStatus(context.Background(), node, metav1.UpdateOptions{})
			}
			if _, err := w.Stop(context.Background(), access); !errors.Is(err, ErrWorkspaceUnavailable) {
				t.Fatal(err)
			}
			record, _ := w.registry.Get(testWorkspaceID)
			if record.Owner.StopConfirmed {
				t.Fatal("unhealthy node authorized takeover")
			}
		})
	}
}

func TestWorkspaceStopRejectsReusedPodUID(t *testing.T) {
	w, client, access, pod := runtimeFixture(t, true)
	pod.UID = "uid-other"
	client.CoreV1().Pods(pod.Namespace).Update(context.Background(), pod, metav1.UpdateOptions{})
	if _, err := w.Stop(context.Background(), access); !errors.Is(err, ErrWorkspaceConflict) {
		t.Fatal(err)
	}
	for _, action := range client.Actions() {
		if action.GetVerb() == "delete" {
			t.Fatal("deleted replacement UID")
		}
	}
}

func TestWorkspaceRunningWriterCannotBeReleasedOnTimeout(t *testing.T) {
	w, client, access, _ := runtimeFixture(t, false)
	client.PrependReactor("delete", "pods", func(action clienttesting.Action) (bool, runtime.Object, error) { return true, nil, nil })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
	defer cancel()
	if _, err := w.Stop(ctx, access); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	record, _ := w.registry.Get(testWorkspaceID)
	if record.Owner.StopConfirmed {
		t.Fatal("timed out writer marked stopped")
	}
	if _, err := w.registry.BeginClaim(testWorkspaceID, "task-a", "attempt-b", "pod-b", "fixed@sha256:fixture"); err == nil {
		t.Fatal("timeout permitted another writer")
	}
}

func TestWorkspaceLifecycleWaitHonorsCancellation(t *testing.T) {
	w, _, access, _ := runtimeFixture(t, false)
	if err := w.enter(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer func() { <-w.gate }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
	defer cancel()
	if _, err := w.Stop(ctx, access); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	record, _ := w.registry.Get(testWorkspaceID)
	if record.Phase != "bound" {
		t.Fatal("cancelled waiter changed ownership")
	}
}

func TestWorkspaceOutputLimitCannotBeBypassedByReaderFrom(t *testing.T) {
	output := &boundedWorkspaceOutput{limit: 32}
	if _, err := io.Copy(output, io.LimitReader(strings.NewReader(strings.Repeat("x", 1024)), 1024)); err != nil {
		t.Fatal(err)
	}
	if len(output.Bytes()) != 32 || !output.overflow {
		t.Fatal("unbounded exec output")
	}
}

func TestWorkspaceInitializationPodHasNoAPICredentials(t *testing.T) {
	pod, err := BuildWorkspaceInitPod(Config{Image: "fixed", RuntimeClass: "gvisor"}, "test", testWorkspaceID)
	if err != nil {
		t.Fatal(err)
	}
	if pod.Namespace != StorageNamespace || pod.Spec.AutomountServiceAccountToken == nil || *pod.Spec.AutomountServiceAccountToken {
		t.Fatal("maintenance credentials exposed")
	}
	for _, volume := range pod.Spec.Volumes {
		if volume.Projected != nil {
			t.Fatal("maintenance API token projected")
		}
	}
	if len(pod.Spec.Containers[0].Command) != 2 || pod.Spec.Containers[0].Command[0] != "/usr/local/bin/sandbox-workspace-init" {
		t.Fatal("free maintenance command")
	}
	if _, err := BuildWorkspaceInitPod(Config{Image: "fixed", RuntimeClass: "gvisor"}, "test", "../escape"); err == nil {
		t.Fatal("unsafe init ID")
	}
}
