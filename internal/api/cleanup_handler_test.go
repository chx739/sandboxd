package api

import (
	"encoding/json"
	"net/http"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/chx739/sandboxd/internal/backuphelper"
	"github.com/chx739/sandboxd/internal/sandbox"
)

func cleanupAPI(t *testing.T) (*Server, *sandbox.WorkspaceRegistry) {
	t.Helper()
	r, err := sandbox.OpenWorkspaceRegistry(filepath.Join(t.TempDir(), "workspaces.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	m := sandbox.NewManager(nil, nil, sandbox.Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	w, err := sandbox.NewWorkspaceRuntime(m, r, "172.23.0.3", "fixed-image")
	if err != nil {
		t.Fatal(err)
	}
	b, err := sandbox.NewBackupRuntime(w, sandbox.BackupJobConfig{ImageRef: "sandboxd-restic:0.18.1-v3", SecretName: "restic-test", EndpointIP: "172.23.0.4", Owner: backuphelper.RepositoryOwner{ControllerID: "controller-12345678123442348abc123456789abc", RepositoryCryptoID: strings.Repeat("2", 64), OwnerSnapshotID: strings.Repeat("9", 64)}})
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(m, nil, nil, "target", "agent-token", "operator-token", time.Second, time.Second, nil)
	s.EnableWorkspaces(w)
	s.EnableBackups(b)
	return s, r
}

func cleanupBody() map[string]any {
	return map[string]any{"gcId": "gc-12345678123442348abc123456789abc", "jobId": "gc-a", "operation": "forget", "snapshotId": strings.Repeat("1", 64), "timeoutMs": 1}
}

func TestCleanupHTTPOnlyOperatorAndFixedInputs(t *testing.T) {
	s, r := cleanupAPI(t)
	base := "/api/v1/repository/cleanup"
	for _, token := range []string{"", "agent-token", "wrong-token"} {
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			path := base
			if method == http.MethodPost {
				path += "/reserve"
			}
			if got := backupRequest(s, method, path, token, `{}`); got.Code != 401 {
				t.Fatal("non-operator accepted", got.Code, got.Body.String())
			}
		}
	}
	for _, mode := range []string{"command", "owner", "success", "bad-gc", "bad-snapshot", "unsupported", "timeout", "next-job"} {
		t.Run(mode, func(t *testing.T) {
			body := cleanupBody()
			switch mode {
			case "command":
				body["argv"] = []string{"forget", "--prune"}
			case "owner":
				body["owner"] = map[string]string{"controllerId": "other"}
			case "success":
				body["success"] = true
			case "bad-gc":
				body["gcId"] = "gc-invalid"
			case "bad-snapshot":
				body["snapshotId"] = "latest"
			case "unsupported":
				body["operation"] = "unlock"
			case "timeout":
				body["timeoutMs"] = 90001
			case "next-job":
				body["nextJobId"] = "gc-b"
			}
			raw, _ := json.Marshal(body)
			if got := backupRequest(s, http.MethodPost, base+"/reserve", "operator-token", string(raw)); got.Code != 400 {
				t.Fatal(got.Code, got.Body.String())
			}
		})
	}
	if len(r.Records()) != 0 {
		t.Fatal("invalid requests created workspace")
	}
}

func TestCleanupHTTPReserveAbortKeepsFreezeAndRetry(t *testing.T) {
	s, r := cleanupAPI(t)
	base := "/api/v1/repository/cleanup"
	body := cleanupBody()
	raw, _ := json.Marshal(body)
	for range 2 {
		if got := backupRequest(s, http.MethodPost, base+"/reserve", "operator-token", string(raw)); got.Code != 200 {
			t.Fatal(got.Code, got.Body.String())
		}
	}
	if got := backupRequest(s, http.MethodGet, base, "operator-token", ""); got.Code != 200 || !strings.Contains(got.Body.String(), `"phase":"reserved"`) {
		t.Fatal(got.Code, got.Body.String())
	}
	if got := backupRequest(s, http.MethodPost, base+"/wait", "operator-token", string(raw)); got.Code != 504 {
		t.Fatal("timeout released freeze", got.Code, got.Body.String())
	}
	if got := backupRequest(s, http.MethodPost, base+"/abort", "operator-token", string(raw)); got.Code != 200 || !strings.Contains(got.Body.String(), `"phase":"stopped"`) {
		t.Fatal(got.Code, got.Body.String())
	}
	if got := backupRequest(s, http.MethodPost, base+"/finish", "operator-token", string(raw)); got.Code != 409 {
		t.Fatal("abort released repository", got.Code, got.Body.String())
	}
	body["nextJobId"] = "gc-b"
	raw, _ = json.Marshal(body)
	if got := backupRequest(s, http.MethodPost, base+"/retry", "operator-token", string(raw)); got.Code != 200 || !strings.Contains(got.Body.String(), `"jobId":"gc-b"`) {
		t.Fatal(got.Code, got.Body.String())
	}
	workspace := "workspace-12345678123442348abc123456789abc"
	if got := backupRequest(s, http.MethodGet, "/api/v1/workspaces/"+workspace+"/backup", "agent-token", ""); got.Code != 404 {
		t.Fatal("Agent retrieved maintenance binding", got.Code, got.Body.String())
	}
	if _, err := r.Register(workspace, "session-maintenance", "branch-main"); err == nil {
		t.Fatal("reserved maintenance ID registered")
	}
	body["jobId"] = "gc-b"
	delete(body, "nextJobId")
	body["snapshotId"] = strings.Repeat("3", 64)
	raw, _ = json.Marshal(body)
	if got := backupRequest(s, http.MethodPost, base+"/abort", "operator-token", string(raw)); got.Code != 409 {
		t.Fatal("changed target accepted", got.Code, got.Body.String())
	}
	body["snapshotId"] = strings.Repeat("1", 64)
	raw, _ = json.Marshal(body)
	if got := backupRequest(s, http.MethodPost, base+"/cancel-reservation", "operator-token", string(raw)); got.Code != 200 || !strings.Contains(got.Body.String(), `"cleanupCancelled":true`) {
		t.Fatal(got.Code, got.Body.String())
	}
}

func TestCleanupHTTPUnownedBackendRefuses(t *testing.T) {
	s, _, _ := backupAPI(t)
	raw, _ := json.Marshal(cleanupBody())
	if got := backupRequest(s, http.MethodPost, "/api/v1/repository/cleanup/reserve", "operator-token", string(raw)); got.Code != 409 {
		t.Fatal("unowned repository refusal must return conflict", got.Code, got.Body.String())
	}
	disabled := NewServer(nil, nil, nil, "target", "agent-token", "operator-token", time.Second, time.Second, nil)
	if got := backupRequest(disabled, http.MethodPost, "/api/v1/repository/cleanup/reserve", "operator-token", string(raw)); got.Code != 503 {
		t.Fatal(got.Code, got.Body.String())
	}
}
