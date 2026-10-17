// workspace-fixture renders trusted administrative smoke manifests using the
// same constructors as sandboxd; it does not grant an Agent a free PodSpec.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/chx739/sandboxd/internal/sandbox"
)

func main() {
	workspaceID := os.Getenv("SANDBOXD_SMOKE_WORKSPACE_ID")
	pv, pvc, err := sandbox.BuildWorkspaceVolume("sandboxd-demo", os.Getenv("SANDBOXD_NFS_SERVER"), "/workspaces", workspaceID)
	if err != nil {
		fatal(err)
	}
	pv.APIVersion, pv.Kind = "v1", "PersistentVolume"
	pvc.APIVersion, pvc.Kind = "v1", "PersistentVolumeClaim"
	pod, err := sandbox.BuildWorkspacePod(sandbox.Config{Namespace: "sandboxd-demo", Image: os.Getenv("SANDBOXD_WORKSPACE_IMAGE"), RuntimeClass: "gvisor"}, "workspace-probe", sandbox.WorkspaceMount{
		WorkspaceID: workspaceID, TaskID: "task-workspace-probe", AttemptID: "attempt-workspace-probe", Generation: 1,
	})
	if err != nil {
		fatal(err)
	}
	pod.APIVersion, pod.Kind = "v1", "Pod"
	if pod.Spec.Containers[0].Image == "" {
		fatal(fmt.Errorf("SANDBOXD_WORKSPACE_IMAGE is required"))
	}
	pod.Spec.Containers[0].ImagePullPolicy = "Never"
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"apiVersion": "v1", "kind": "List", "items": []any{pv, pvc, pod}}); err != nil {
		fatal(err)
	}
}

func fatal(err error) { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
