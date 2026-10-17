// Trusted component probe. No Session/Agent checkpoint acceptance is claimed.
package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
	"github.com/chx739/sandboxd/internal/workspace"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

func id(prefix string) string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return prefix + "-" + hex.EncodeToString(b[:])
}
func must(err error) {
	if err != nil {
		panic(err)
	}
}
func save(path string, value any) {
	data, err := json.MarshalIndent(value, "", "  ")
	must(err)
	must(os.MkdirAll(filepath.Dir(path), 0700))
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	must(err)
	_, err = f.Write(append(data, '\n'))
	must(err)
	must(f.Sync())
	must(f.Close())
	dir, err := os.Open(filepath.Dir(path))
	must(err)
	must(dir.Sync())
	must(dir.Close())
}

func actualRuntime(ctx context.Context, uid string) string {
	// CRI inspect may contain resolved credential env; keep raw bytes only in
	// memory and publish the runtimeType field. This is an administrative probe.
	raw, err := exec.CommandContext(ctx, "docker", "exec", "sandboxd-control-plane", "crictl", "pods", "-o", "json").Output()
	must(err)
	var pods struct {
		Items []struct {
			ID       string `json:"id"`
			Metadata struct {
				UID string `json:"uid"`
			} `json:"metadata"`
		} `json:"items"`
	}
	must(json.Unmarshal(raw, &pods))
	for _, p := range pods.Items {
		if p.Metadata.UID == uid {
			raw, err = exec.CommandContext(ctx, "docker", "exec", "sandboxd-control-plane", "crictl", "inspectp", p.ID).Output()
			must(err)
			var info struct {
				Info struct {
					RuntimeType string `json:"runtimeType"`
				} `json:"info"`
			}
			must(json.Unmarshal(raw, &info))
			if !strings.Contains(info.Info.RuntimeType, "runsc") {
				panic("maintenance runtime is not verified runsc")
			}
			return info.Info.RuntimeType
		}
	}
	panic("maintenance CRI sandbox missing before fixture cleanup")
}

func main() {
	server := flag.String("nfs-server", "", "verified project NFS IPv4")
	endpoint := flag.String("minio-ip", "", "verified project MinIO IPv4")
	secret := flag.String("secret-name", "", "existing dedicated repository Secret; no credential values")
	control := flag.String("control-dir", "", "new private native Linux fixture directory")
	output := flag.String("output", "", "new public result file")
	recoverWorkspace := flag.String("recover-workspace", "", "reconcile only this prior synthetic fixture barrier")
	httpMode := flag.Bool("http", false, "exercise authenticated trusted backup HTTP routes")
	flag.Parse()
	if *control == "" || *output == "" {
		panic("control-dir/output required")
	}
	if _, err := os.Lstat(filepath.Join(*control, "workspaces.jsonl")); *recoverWorkspace == "" && !os.IsNotExist(err) {
		panic("fixture requires new control journal")
	}
	report := map[string]any{"mode": "real-go-gvisor-nfs-restic-minio-persistent-barrier-component", "passed": false, "externalModelCalls": 0, "scope": "backend write barrier and registry restart only; no Session joint commit or process SIGKILL claim"}
	defer func() {
		if value := recover(); value != nil {
			report["error"] = fmt.Sprint(value)
			save(*output, report)
			fmt.Fprintln(os.Stderr, "component failed; inspect report and retained control state")
			os.Exit(1)
		}
	}()
	raw, err := clientcmd.LoadFromFile(clientcmd.RecommendedHomeFile)
	must(err)
	if raw.CurrentContext != "kind-sandboxd" {
		panic("requires kind-sandboxd")
	}
	rest, err := clientcmd.BuildConfigFromFlags("", clientcmd.RecommendedHomeFile)
	must(err)
	client, err := kubernetes.NewForConfig(rest)
	must(err)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	nodes, err := client.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	must(err)
	if len(nodes.Items) != 1 || nodes.Items[0].Name != "sandboxd-control-plane" {
		panic("requires project single-node cluster")
	}
	registry, err := sandbox.OpenWorkspaceRegistry(filepath.Join(*control, "workspaces.jsonl"))
	must(err)
	defer func() { registry.Close() }()
	manager := sandbox.NewManager(client, rest, sandbox.Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	run, err := sandbox.NewWorkspaceRuntime(manager, registry, *server, "sandboxd-workspace:runtime-v1.5")
	must(err)
	config := sandbox.BackupJobConfig{ImageRef: "sandboxd-restic:0.18.1-v1", SecretName: *secret, EndpointIP: *endpoint}
	backup, err := sandbox.NewBackupRuntime(run, config)
	must(err)
	var transport backupTransport = backup
	var closeHTTP func()
	configureHTTP := func() {
		if closeHTTP != nil {
			closeHTTP()
			closeHTTP = nil
		}
		transport = backup
		if *httpMode {
			transport, closeHTTP = newHTTPBackup(run, backup)
			report["transport"] = "authenticated real loopback HTTP"
			report["unauthorizedAndOperatorRejected"] = true
		}
	}
	defer func() {
		if closeHTTP != nil {
			closeHTTP()
		}
	}()
	configureHTTP()
	if *recoverWorkspace != "" {
		barrier, ok := registry.Barrier(*recoverWorkspace)
		if !ok {
			panic("fixture barrier missing")
		}
		report["mode"] = "real-backend-postfailure-reconciliation"
		report["controlRoot"] = *control
		barrier, result, err := backup.Wait(ctx, barrier)
		report["observedBarrier"] = barrier
		must(err)
		save(filepath.Join(*control, "backend-postfailure-result.json"), result)
		finished, err := backup.Finish(ctx, barrier)
		must(err)
		report["backupResult"] = result
		report["finishedBarrier"] = finished
		report["passed"] = true
		save(*output, report)
		fmt.Println("prior synthetic fixture reconciled; original failure preserved")
		return
	}
	wid := id("workspace")
	_, err = run.Prepare(ctx, wid, id("session"), id("branch"))
	must(err)
	owner, err := run.Claim(ctx, wid, id("task"), id("attempt"))
	must(err)
	a := sandbox.WorkspaceAccess{WorkspaceID: wid, TaskID: owner.Owner.TaskID, AttemptID: owner.Owner.AttemptID, SandboxID: owner.Owner.SandboxID, Generation: owner.Generation}
	report["workspaceId"] = wid
	report["controlRoot"] = *control
	report["owner"] = owner
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		_, err := run.Stop(cleanup, a)
		if err != nil {
			fmt.Fprintln(os.Stderr, "fixture business-owner cleanup incomplete")
		}
	}()
	written, err := run.Files(ctx, a, workspace.Request{Operation: "write_file", Path: "report.md", Content: "persistent backup barrier synthetic fixture\n"})
	must(err)
	if written.Status != 200 {
		panic("write failed")
	}
	checkpoint, job := id("cp"), id("probe")
	reserved, err := transport.Reserve(ctx, a, checkpoint, job)
	must(err)
	if reserved.Phase != "reserved" {
		panic("Reserve did not freeze source")
	}
	report["reservedBeforeStart"] = true
	barrier, err := transport.Start(ctx, a, checkpoint, job)
	report["startedBarrier"] = barrier
	must(err)
	_, err = run.Files(ctx, a, workspace.Request{Operation: "read_file", Path: "report.md"})
	if !errors.Is(err, sandbox.ErrWorkspaceConflict) {
		panic("files passed backup barrier")
	}
	report["fileRPCBlockedBeforeRestart"] = true
	must(registry.Close())
	registry, err = sandbox.OpenWorkspaceRegistry(filepath.Join(*control, "workspaces.jsonl"))
	must(err)
	run, err = sandbox.NewWorkspaceRuntime(manager, registry, *server, "sandboxd-workspace:runtime-v1.5")
	must(err)
	backup, err = sandbox.NewBackupRuntime(run, config)
	must(err)
	configureHTTP()
	_, err = run.Files(ctx, a, workspace.Request{Operation: "write_file", Path: "must-not-exist.md", Content: "forbidden during backup"})
	if !errors.Is(err, sandbox.ErrWorkspaceConflict) {
		panic("write passed reopened barrier")
	}
	report["writeRPCBlockedAfterRegistryRestart"] = true
	_, err = run.Claim(ctx, wid, a.TaskID, a.AttemptID)
	if !errors.Is(err, sandbox.ErrWorkspaceConflict) {
		panic("claim passed reopened barrier")
	}
	report["claimBlockedAfterRegistryRestart"] = true
	barrier, result, err := transport.Wait(ctx, barrier)
	report["stoppedBarrier"] = barrier
	must(err)
	report["backupResult"] = result
	pod, err := client.CoreV1().Pods(sandbox.StorageNamespace).Get(ctx, barrier.PodName, metav1.GetOptions{})
	must(err)
	report["maintenancePod"] = map[string]any{"name": pod.Name, "uid": pod.UID, "runtimeClass": pod.Spec.RuntimeClassName, "nodeName": pod.Spec.NodeName, "phase": pod.Status.Phase, "imageId": result.ImageID, "runAsUser": pod.Spec.SecurityContext.RunAsUser}
	report["actualRuntimeType"] = actualRuntime(ctx, string(pod.UID))
	_, err = run.Files(ctx, a, workspace.Request{Operation: "list_files", Path: "."})
	if !errors.Is(err, sandbox.ErrWorkspaceConflict) {
		panic("backup completion prematurely released barrier")
	}
	report["heldUntilExplicitFinish"] = true
	// This component records its backend outcome durably, not a fake Session COMMITTED.
	save(filepath.Join(*control, "backend-component-result.json"), result)
	finished, err := transport.Finish(ctx, barrier)
	must(err)
	report["finishedBarrier"] = finished
	read, err := run.Files(ctx, a, workspace.Request{Operation: "read_file", Path: "report.md"})
	must(err)
	if read.Status != 200 {
		panic("file unavailable after finish")
	}
	report["readAfterFinish"] = json.RawMessage(read.Body)
	list, err := run.Files(ctx, a, workspace.Request{Operation: "list_files", Path: "."})
	must(err)
	if list.Status != 200 {
		panic("list failed")
	}
	report["filesAfterFinish"] = json.RawMessage(list.Body)
	var verified struct {
		Content string `json:"content"`
		SHA256  string `json:"sha256"`
	}
	must(json.Unmarshal(read.Body, &verified))
	if verified.Content != "persistent backup barrier synthetic fixture\n" || verified.SHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(verified.Content))) {
		panic("file content/hash changed during backup")
	}
	var listed struct {
		Entries []struct {
			Path string `json:"path"`
		} `json:"entries"`
	}
	must(json.Unmarshal(list.Body, &listed))
	if len(listed.Entries) != 1 || listed.Entries[0].Path != "report.md" {
		panic("blocked write left unexpected file")
	}
	report["contentAndHashUnchanged"] = true
	if _, err := run.Stop(ctx, a); err != nil {
		panic(err)
	}
	report["businessOwnerStopConfirmed"] = true
	report["passed"] = true
	save(*output, report)
	fmt.Println("persistent-barrier backend component passed; files/snapshots/control retained")
}
