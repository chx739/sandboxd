// workspace-runtime-smoke exercises real Go lifecycle control. It is an
// administrative fixture, not an Agent or automatic recovery acceptance score.
package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"time"

	"github.com/chx739/sandboxd/internal/api"
	"github.com/chx739/sandboxd/internal/sandbox"
	"github.com/chx739/sandboxd/internal/workspace"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

func id(prefix string) string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		fail(err)
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return prefix + "-" + hex.EncodeToString(b[:])
}
func fail(err error) { panic(err) }
func access(r sandbox.WorkspaceRecord) sandbox.WorkspaceAccess {
	return sandbox.WorkspaceAccess{WorkspaceID: r.WorkspaceID, TaskID: r.Owner.TaskID, AttemptID: r.Owner.AttemptID, SandboxID: r.Owner.SandboxID, Generation: r.Generation}
}

func main() {
	defer func() {
		if value := recover(); value != nil {
			fmt.Fprintln(os.Stderr, value)
			os.Exit(1)
		}
	}()
	server := flag.String("nfs-server", "", "trusted local project NFS IPv4")
	image := flag.String("image", "", "loaded project image with both fixed helpers")
	control := flag.String("control-dir", "", "private native Linux persistent control directory")
	output := flag.String("output", "", "report path")
	kubeconfig := flag.String("kubeconfig", clientcmd.RecommendedHomeFile, "local kind config")
	httpMode := flag.Bool("http", false, "exercise actual authenticated HTTP handlers on a local fixture server")
	flag.Parse()
	if *control == "" || *output == "" {
		fail(fmt.Errorf("control-dir and output are required"))
	}
	raw, err := clientcmd.LoadFromFile(*kubeconfig)
	if err != nil || raw.CurrentContext != "kind-sandboxd" {
		fail(fmt.Errorf("requires local kind-sandboxd context"))
	}
	rest, err := clientcmd.BuildConfigFromFlags("", *kubeconfig)
	if err != nil {
		fail(err)
	}
	client, err := kubernetes.NewForConfig(rest)
	if err != nil {
		fail(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	nodes, err := client.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	if err != nil || len(nodes.Items) != 1 || nodes.Items[0].Name != "sandboxd-control-plane" {
		fail(fmt.Errorf("requires the project single-node cluster"))
	}
	registry, err := sandbox.OpenWorkspaceRegistry(filepath.Join(*control, "workspaces.jsonl"))
	if err != nil {
		fail(err)
	}
	defer func() { registry.Close() }()
	manager := sandbox.NewManager(client, rest, sandbox.Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	runtime, err := sandbox.NewWorkspaceRuntime(manager, registry, *server, *image)
	if err != nil {
		fail(err)
	}
	prepare, claim, files, stop := runtime.Prepare, runtime.Claim, runtime.Files, runtime.Stop
	var fixtureServer *httptest.Server
	defer func() {
		if fixtureServer != nil {
			fixtureServer.Close()
		}
	}()
	configureHTTP := func() {
		if !*httpMode {
			prepare, claim, files, stop = runtime.Prepare, runtime.Claim, runtime.Files, runtime.Stop
			return
		}
		if fixtureServer != nil {
			fixtureServer.Close()
		}
		handler := api.NewServer(manager, nil, nil, "sandboxd-target", "synthetic-fixture-agent", "synthetic-fixture-operator", 90*time.Second, 30*time.Second, func() bool { return true })
		handler.EnableWorkspaces(runtime)
		fixtureServer = httptest.NewServer(handler.Handler())
		invoke := func(ctx context.Context, path string, payload any) (int, []byte, error) {
			body, err := json.Marshal(payload)
			if err != nil {
				return 0, nil, err
			}
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, fixtureServer.URL+path, bytes.NewReader(body))
			if err != nil {
				return 0, nil, err
			}
			req.Header.Set("Authorization", "Bearer synthetic-fixture-agent")
			req.Header.Set("Content-Type", "application/json")
			resp, err := fixtureServer.Client().Do(req)
			if err != nil {
				return 0, nil, err
			}
			defer resp.Body.Close()
			data, err := io.ReadAll(io.LimitReader(resp.Body, workspace.MaxResponseBytes+1))
			if err != nil {
				return resp.StatusCode, nil, err
			}
			if resp.StatusCode >= 400 {
				return resp.StatusCode, data, fmt.Errorf("workspace HTTP status %d", resp.StatusCode)
			}
			return resp.StatusCode, data, nil
		}
		prepare = func(ctx context.Context, w, s, b string) (sandbox.WorkspaceRecord, error) {
			var r sandbox.WorkspaceRecord
			_, data, err := invoke(ctx, "/api/v1/workspaces", map[string]string{"workspaceId": w, "sessionId": s, "branchId": b})
			if err == nil {
				err = json.Unmarshal(data, &r)
			}
			return r, err
		}
		claim = func(ctx context.Context, w, t, a string) (sandbox.WorkspaceRecord, error) {
			var r sandbox.WorkspaceRecord
			_, data, err := invoke(ctx, "/api/v1/workspaces/"+w+"/claim", map[string]string{"taskId": t, "attemptId": a})
			if err == nil {
				err = json.Unmarshal(data, &r)
			}
			return r, err
		}
		files = func(ctx context.Context, a sandbox.WorkspaceAccess, req workspace.Request) (sandbox.WorkspaceFileResult, error) {
			status, data, err := invoke(ctx, "/api/v1/workspaces/"+a.WorkspaceID+"/files", map[string]any{"binding": a, "request": req})
			return sandbox.WorkspaceFileResult{Status: status, Body: data}, err
		}
		stop = func(ctx context.Context, a sandbox.WorkspaceAccess) (sandbox.WorkspaceRecord, error) {
			var r sandbox.WorkspaceRecord
			_, data, err := invoke(ctx, "/api/v1/workspaces/"+a.WorkspaceID+"/release", a)
			if err == nil {
				err = json.Unmarshal(data, &r)
			}
			return r, err
		}
	}
	configureHTTP()
	workspaceID, sessionID, branchID, taskID := id("workspace"), id("session"), id("branch"), id("task")
	started := time.Now()
	if _, err := prepare(ctx, workspaceID, sessionID, branchID); err != nil {
		fail(err)
	}
	first, err := claim(ctx, workspaceID, taskID, id("attempt"))
	if err != nil {
		fail(err)
	}
	// Keep enough information to stop the current fixture even on a later failed assertion.
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		current, err := registry.Get(workspaceID)
		if err != nil {
			fmt.Fprintln(os.Stderr, "fixture record unavailable:", err)
			return
		}
		if _, err := runtime.Stop(cleanup, access(current)); err != nil {
			fmt.Fprintln(os.Stderr, "fixture cleanup incomplete:", err)
		}
	}()
	write, err := files(ctx, access(first), workspace.Request{Operation: "write_file", Path: "report.md", Content: "real Go NFS lifecycle synthetic fixture\n"})
	if err != nil || write.Status != 200 {
		fail(fmt.Errorf("write failed: %v", err))
	}
	if _, err := stop(ctx, access(first)); err != nil {
		fail(err)
	}
	// Reopen Go's durable journal: the next binding must not rely on memory.
	if fixtureServer != nil {
		fixtureServer.Close()
	}
	registry.Close()
	registry, err = sandbox.OpenWorkspaceRegistry(filepath.Join(*control, "workspaces.jsonl"))
	if err != nil {
		fail(err)
	}
	runtime, err = sandbox.NewWorkspaceRuntime(manager, registry, *server, *image)
	if err != nil {
		fail(err)
	}
	configureHTTP()
	second, err := claim(ctx, workspaceID, taskID, id("attempt"))
	if err != nil {
		fail(err)
	}
	read, err := files(ctx, access(second), workspace.Request{Operation: "read_file", Path: "report.md"})
	if err != nil || read.Status != 200 {
		fail(fmt.Errorf("read after replacement failed: %v", err))
	}
	var readBody struct {
		Content string `json:"content"`
		SHA256  string `json:"sha256"`
	}
	var writeBody struct {
		SHA256 string `json:"newSha256"`
	}
	if json.Unmarshal(read.Body, &readBody) != nil || json.Unmarshal(write.Body, &writeBody) != nil || readBody.SHA256 != writeBody.SHA256 || readBody.Content != "real Go NFS lifecycle synthetic fixture\n" {
		fail(fmt.Errorf("restored fixture does not match"))
	}
	_, staleErr := files(ctx, access(first), workspace.Request{Operation: "read_file", Path: "report.md"})
	if staleErr == nil {
		fail(fmt.Errorf("old attempt still authorized"))
	}
	if first.Owner.PodUID == second.Owner.PodUID || first.WorkspaceID != second.WorkspaceID || second.Generation != first.Generation+1 {
		fail(fmt.Errorf("replacement identities incorrect"))
	}
	last, err := stop(ctx, access(second))
	if err != nil {
		fail(err)
	}
	report := map[string]any{"mode": "real-go-lifecycle-component", "externalModelCalls": 0, "scope": "Go Prepare/Claim/Files/Stop and journal reopen; not Python automatic Task continuation", "first": first, "second": second, "released": last, "write": json.RawMessage(write.Body), "read": json.RawMessage(read.Body), "oldAttemptDenied": true, "elapsedMs": time.Since(started).Milliseconds(), "passed": true}
	if *httpMode {
		report["mode"] = "real-go-http-lifecycle-component"
		report["scope"] = "authenticated Go HTTP Prepare/Claim/Files/Stop, real gVisor/NFS and journal reopen; not Python automatic Task continuation"
	}
	data, _ := json.MarshalIndent(report, "", "  ")
	if err := os.WriteFile(*output, append(data, '\n'), 0600); err != nil {
		fail(err)
	}
	fmt.Printf("real Go lifecycle component passed; report=%s\n", *output)
}
