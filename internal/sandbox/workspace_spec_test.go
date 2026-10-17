package sandbox

import (
	"reflect"
	"testing"

	corev1 "k8s.io/api/core/v1"
)

const testWorkspaceID = "workspace-0123456789abcdef0123456789abcdef"

func TestWorkspacePodPreservesSecurityAndDedicatedVolume(t *testing.T) {
	cfg := Config{Namespace: "sandboxd-demo", Image: "test:fixed", RuntimeClass: "gvisor"}
	baseline := BuildPod(cfg, "test")
	pod, err := BuildWorkspacePod(cfg, "test", WorkspaceMount{WorkspaceID: testWorkspaceID, TaskID: "task-demo", AttemptID: "attempt-demo", Generation: 2})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(pod.Spec.SecurityContext, baseline.Spec.SecurityContext) || !reflect.DeepEqual(pod.Spec.Containers[0].SecurityContext, baseline.Spec.Containers[0].SecurityContext) {
		t.Fatal("security profile changed")
	}
	if pod.Spec.RuntimeClassName == nil || *pod.Spec.RuntimeClassName != "gvisor" {
		t.Fatal("runtime lost")
	}
	if pod.Labels[LabelState] != "busy" || pod.Labels[LabelSource] != "workspace" || pod.Annotations[AnnotationGeneration] != "2" {
		t.Fatal(pod.ObjectMeta)
	}
	for _, volume := range pod.Spec.Volumes {
		if volume.Name == "workspace" && (volume.EmptyDir != nil || volume.PersistentVolumeClaim == nil || volume.PersistentVolumeClaim.ClaimName != testWorkspaceID) {
			t.Fatal(volume)
		}
		if volume.Name == "tmp" && volume.EmptyDir == nil {
			t.Fatal("tmp must remain disposable")
		}
	}
}

func TestWorkspaceBindingCannotChooseFreePaths(t *testing.T) {
	cfg := Config{RuntimeClass: "gvisor"}
	for _, mount := range []WorkspaceMount{
		{WorkspaceID: "../escape", TaskID: "task-demo", AttemptID: "attempt-demo", Generation: 1},
		{WorkspaceID: testWorkspaceID, TaskID: "other", AttemptID: "attempt-demo", Generation: 1},
		{WorkspaceID: testWorkspaceID, TaskID: "task-demo", AttemptID: "attempt-demo", Generation: 0},
	} {
		if _, err := BuildWorkspacePod(cfg, "test", mount); err == nil {
			t.Fatal("unsafe binding accepted")
		}
	}
	if _, err := BuildWorkspacePod(Config{}, "test", WorkspaceMount{WorkspaceID: testWorkspaceID, TaskID: "task-demo", AttemptID: "attempt-demo", Generation: 1}); err == nil {
		t.Fatal("implicit runc allowed")
	}
}

func TestNFSVolumePinsVersionAndRetainsWorkspace(t *testing.T) {
	pv, pvc, err := BuildWorkspaceVolume("sandboxd-demo", "172.18.0.5", "/workspaces", testWorkspaceID)
	if err != nil {
		t.Fatal(err)
	}
	if pv.Spec.NFS.Path != "/workspaces/"+testWorkspaceID || pv.Spec.PersistentVolumeReclaimPolicy != corev1.PersistentVolumeReclaimRetain {
		t.Fatal(pv.Spec)
	}
	if pv.Spec.ClaimRef.Name != pvc.Name || pv.Spec.ClaimRef.Namespace != pvc.Namespace || pvc.Spec.VolumeName != pv.Name {
		t.Fatal("volume binding mismatch")
	}
	if !reflect.DeepEqual(pv.Spec.MountOptions, []string{"nfsvers=4.1", "proto=tcp", "hard", "timeo=50", "retrans=2"}) {
		t.Fatal(pv.Spec.MountOptions)
	}
	for _, args := range [][4]string{
		{"sandboxd-demo", "attacker.example", "/workspaces", testWorkspaceID},
		{"sandboxd-demo", "127.0.0.1", "/etc", testWorkspaceID},
		{"sandboxd-demo", "127.0.0.1", "/workspaces", "workspace-../bad"},
	} {
		if _, _, err := BuildWorkspaceVolume(args[0], args[1], args[2], args[3]); err == nil {
			t.Fatal("unsafe volume accepted")
		}
	}
}
