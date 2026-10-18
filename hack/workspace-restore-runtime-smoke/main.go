// Trusted restore component probe; no Session branch restoration claim.
package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
	"github.com/chx739/sandboxd/internal/workspace"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

func check(err error) {
	if err != nil {
		panic("component operation failed; stage recorded")
	}
}
func id(prefix string) string {
	var raw [16]byte
	_, err := rand.Read(raw[:])
	check(err)
	raw[6] = (raw[6] & 15) | 64
	raw[8] = (raw[8] & 63) | 128
	return prefix + "-" + hex.EncodeToString(raw[:])
}
func actualRuntime(ctx context.Context, uid string) string {
	raw, err := exec.CommandContext(ctx, "docker", "exec", "sandboxd-control-plane", "crictl", "pods", "-o", "json").Output()
	check(err)
	var pods struct {
		Items []struct {
			ID       string `json:"id"`
			Metadata struct {
				UID string `json:"uid"`
			} `json:"metadata"`
		} `json:"items"`
	}
	check(json.Unmarshal(raw, &pods))
	for _, pod := range pods.Items {
		if pod.Metadata.UID == uid {
			// 原始inspectp可能包含Secret环境，只发布白名单runtimeType。
			raw, err = exec.CommandContext(ctx, "docker", "exec", "sandboxd-control-plane", "crictl", "inspectp", pod.ID).Output()
			check(err)
			var info struct {
				Info struct {
					RuntimeType string `json:"runtimeType"`
				} `json:"info"`
			}
			check(json.Unmarshal(raw, &info))
			if info.Info.RuntimeType != "io.containerd.runsc.v1" {
				panic("actual maintenance runtime is not runsc")
			}
			return info.Info.RuntimeType
		}
	}
	panic("maintenance sandbox not observed before cleanup")
}
func save(path string, value any) {
	raw, err := json.MarshalIndent(value, "", "  ")
	check(err)
	check(os.MkdirAll(filepath.Dir(path), 0700))
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	check(err)
	_, err = file.Write(append(raw, '\n'))
	check(err)
	check(file.Sync())
	check(file.Close())
}

func main() {
	server := flag.String("nfs-server", "", "verified project NFS IPv4")
	endpoint := flag.String("minio-ip", "", "verified dedicated MinIO IPv4")
	secret := flag.String("secret-name", "", "dedicated Secret name only")
	control := flag.String("control-dir", "", "new private native fixture directory")
	output := flag.String("output", "", "new result path")
	sourceReport := flag.String("source-report", "docs/evidence/phase9-v2-agent-cut-committed.json", "existing public synthetic committed checkpoint evidence")
	reconcile := flag.Bool("reconcile", false, "only reconcile resources in this fixture's retained control directory")
	flag.Parse()
	home, err := os.UserHomeDir()
	check(err)
	if filepath.Dir(filepath.Clean(*control)) != filepath.Join(home, ".local/share/sandboxd/workspace-restore-runtime-smoke") || !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(filepath.Base(*control)) {
		panic("requires new private fixture control directory")
	}
	if _, err = os.Stat(*control); !*reconcile && !os.IsNotExist(err) {
		panic("preserve prior control directory")
	}
	if *reconcile {
		if _, err = os.Stat(filepath.Join(*control, "workspaces.jsonl")); err != nil {
			panic("reconcile requires existing fixture journal")
		}
	}
	if _, err = os.Stat(*output); !os.IsNotExist(err) {
		panic("preserve prior output")
	}
	report := map[string]any{"passed": false, "mode": "real-go-gvisor-nfs-restic-minio-trusted-restore-component", "externalModelCalls": 0, "controlRoot": *control, "scope": "trusted restore executor only; no Session branch/API/GC acceptance"}
	stage := "source validation"
	defer func() {
		if recovered := recover(); recovered != nil {
			report["errorStage"] = stage
			report["failed"] = true
			save(*output, report)
			fmt.Fprintln(os.Stderr, "restore component failed; inspect stage and retained control state")
			os.Exit(1)
		}
		if stage == "complete" && report["passed"] != true {
			os.Exit(1)
		}
	}()
	raw, err := os.ReadFile(*sourceReport)
	check(err)
	if len(raw) > 1<<20 {
		panic("source evidence too large")
	}
	var evidence struct {
		Passed     bool   `json:"passed"`
		FileSHA256 string `json:"fileSha256"`
		Checkpoint struct {
			Status       string `json:"status"`
			CheckpointID string `json:"checkpointId"`
			Identity     struct {
				SessionID string `json:"sessionId"`
				ImageRef  string `json:"imageRef"`
				ImageID   string `json:"imageId"`
			} `json:"identity"`
			Data struct {
				SnapshotID string `json:"snapshotId"`
				Manifest   string `json:"manifestSha256"`
				Repo       string `json:"repositoryCryptoId"`
			} `json:"data"`
		} `json:"checkpointAtRestart"`
	}
	check(json.Unmarshal(raw, &evidence))
	if !evidence.Passed || evidence.Checkpoint.Status != "COMMITTED" || len(evidence.FileSHA256) != 64 {
		panic("requires existing synthetic COMMITTED evidence")
	}
	source := sandbox.RestoreSource{SnapshotID: evidence.Checkpoint.Data.SnapshotID, ManifestSHA256: evidence.Checkpoint.Data.Manifest, RepositoryCryptoID: evidence.Checkpoint.Data.Repo, ImageRef: evidence.Checkpoint.Identity.ImageRef, ImageID: evidence.Checkpoint.Identity.ImageID}
	report["source"] = source
	report["sourceCheckpointId"] = evidence.Checkpoint.CheckpointID
	stage = "cluster validation"
	kube, err := clientcmd.LoadFromFile(clientcmd.RecommendedHomeFile)
	check(err)
	if kube.CurrentContext != "kind-sandboxd" {
		panic("requires kind-sandboxd")
	}
	config, err := clientcmd.BuildConfigFromFlags("", clientcmd.RecommendedHomeFile)
	check(err)
	client, err := kubernetes.NewForConfig(config)
	check(err)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	nodes, err := client.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	check(err)
	if len(nodes.Items) != 1 || nodes.Items[0].Name != "sandboxd-control-plane" {
		panic("requires project single node")
	}
	registry, err := sandbox.OpenWorkspaceRegistry(filepath.Join(*control, "workspaces.jsonl"))
	check(err)
	defer registry.Close()
	manager := sandbox.NewManager(client, config, sandbox.Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	run, err := sandbox.NewWorkspaceRuntime(manager, registry, *server, "sandboxd-workspace:runtime-v1.5")
	check(err)
	backup, err := sandbox.NewBackupRuntime(run, sandbox.BackupJobConfig{ImageRef: "sandboxd-restic:0.18.1-v2", SecretName: *secret, EndpointIP: *endpoint})
	check(err)
	var targets []string
	var active *sandbox.WorkspaceAccess
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if active != nil {
			_, err := run.Stop(cleanup, *active)
			if err != nil {
				report["cleanupError"] = true
				report["passed"] = false
			}
		}
		for _, target := range targets {
			if record, err := registry.Get(target); err == nil && record.Generation > 0 && !record.Owner.StopConfirmed {
				access := sandbox.WorkspaceAccess{WorkspaceID: target, TaskID: record.Owner.TaskID, AttemptID: record.Owner.AttemptID, SandboxID: record.Owner.SandboxID, Generation: record.Generation}
				if _, err = run.Stop(cleanup, access); err != nil {
					report["cleanupError"] = true
					report["passed"] = false
				}
			}
			if b, ok := registry.Barrier(target); ok && b.Phase != "released" {
				if !b.RestoreVerified {
					var err error
					b, err = backup.Abort(cleanup, b)
					if err != nil {
						report["cleanupError"] = true
						report["passed"] = false
						continue
					}
				}
				if _, err := backup.FinishRestore(cleanup, b, b.RestoreVerified); err != nil {
					report["cleanupError"] = true
					report["passed"] = false
				}
			}
		}
		if stage == "complete" {
			save(*output, report)
		}
	}()
	if *reconcile {
		for _, record := range registry.Records() {
			barrier, ok := registry.Barrier(record.WorkspaceID)
			if !ok || barrier.Operation != "restore" || (record.Owner.TaskID != "" && record.Owner.TaskID != "task-restored-smoke") {
				panic("reconcile scope differs from this fixture")
			}
			targets = append(targets, record.WorkspaceID)
		}
		report["mode"] = "real-trusted-restore-fixture-reconciliation"
		report["targetCount"] = len(targets)
		report["passed"] = true
		stage = "complete"
		return
	}
	for _, badRepo := range []bool{false, true} {
		target := id("workspace")
		targets = append(targets, target)
		job := id("restore")
		branch := id("branch")
		stage = "prepare new restore target"
		_, err = run.Prepare(ctx, target, evidence.Checkpoint.Identity.SessionID, branch)
		check(err)
		selected := source
		if badRepo {
			selected.RepositoryCryptoID = strings.Repeat("0", 64)
			if selected.RepositoryCryptoID == source.RepositoryCryptoID {
				selected.RepositoryCryptoID = strings.Repeat("1", 64)
			}
		}
		stage = "start trusted restore"
		barrier, err := backup.StartRestore(ctx, target, evidence.Checkpoint.CheckpointID, job, selected)
		check(err)
		stage = "wait trusted restore"
		stopped, result, restoreErr := backup.Wait(ctx, barrier)
		if badRepo {
			if restoreErr == nil {
				panic("wrong repository accepted")
			}
			stopped, err = backup.Abort(ctx, barrier)
			check(err)
			runtime := actualRuntime(ctx, stopped.PodUID)
			_, err = backup.FinishRestore(ctx, stopped, false)
			check(err)
			if _, err = registry.BeginClaim(target, "task-failed-restore", "attempt-a", "pod-failed", source.ImageRef); err == nil {
				panic("failed target claimed")
			}
			contents, err := exec.CommandContext(ctx, "docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ls", "-A", "/export/workspaces/"+target).Output()
			check(err)
			if strings.TrimSpace(string(contents)) != "" {
				panic("wrong repository wrote files before rejection")
			}
			report["wrongRepository"] = map[string]any{"workspaceId": target, "rejected": true, "targetEmpty": true, "claimRejected": true, "actualRuntimeType": runtime}
			continue
		}
		check(restoreErr)
		runtime := actualRuntime(ctx, stopped.PodUID)
		stage = "verify result and finish"
		done, err := backup.FinishRestore(ctx, stopped, true)
		check(err)
		if !done.RestoreVerified {
			panic("target not verified")
		}
		stage = "claim restored business workspace"
		record, err := run.Claim(ctx, target, "task-restored-smoke", "attempt-restored-smoke")
		check(err)
		access := sandbox.WorkspaceAccess{WorkspaceID: target, TaskID: record.Owner.TaskID, AttemptID: record.Owner.AttemptID, SandboxID: record.Owner.SandboxID, Generation: record.Generation}
		active = &access
		stage = "read restored file"
		file, err := run.Files(ctx, access, workspace.Request{Operation: "read_file", Path: "report.md"})
		check(err)
		var read struct {
			Content string `json:"content"`
			SHA256  string `json:"sha256"`
		}
		check(json.Unmarshal(file.Body, &read))
		if file.Status != 200 || read.SHA256 != evidence.FileSHA256 || fmt.Sprintf("%x", sha256.Sum256([]byte(read.Content))) != read.SHA256 || record.ImageID != source.ImageID {
			panic("restored file or business image differs")
		}
		businessRuntime := actualRuntime(ctx, record.Owner.PodUID)
		serverHash, err := exec.CommandContext(ctx, "docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum", "/export/workspaces/"+target+"/report.md").Output()
		check(err)
		if len(strings.Fields(string(serverHash))) < 1 || strings.Fields(string(serverHash))[0] != read.SHA256 {
			panic("NFS server file differs")
		}
		_, err = run.Stop(ctx, access)
		check(err)
		active = nil
		report["restored"] = map[string]any{"workspaceId": target, "snapshotId": result.SnapshotID, "manifestSha256": result.ManifestSHA256, "repositoryCryptoId": result.RepositoryCryptoID, "actualMaintenanceRuntimeType": runtime, "actualBusinessRuntimeType": businessRuntime, "maintenanceImageId": result.ImageID, "businessImageId": record.ImageID, "fileSha256": read.SHA256, "restoreVerified": true}
	}
	report["passed"] = true
	stage = "complete"
}
