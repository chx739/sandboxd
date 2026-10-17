package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
)

const backupTestWorkspace = "workspace-123456789abcdef0123456789abcdef0"
const backupTestCheckpoint = "cp-123456789abc4def8123456789abcdef"

func backupAPI(t *testing.T) (*Server, *sandbox.WorkspaceRegistry, sandbox.WorkspaceAccess) {
	t.Helper()
	r, err := sandbox.OpenWorkspaceRegistry(filepath.Join(t.TempDir(), "workspaces.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { r.Close() })
	if _, err = r.Register(backupTestWorkspace, "session-test", "branch-main"); err != nil {
		t.Fatal(err)
	}
	r.Ready(backupTestWorkspace)
	record, err := r.BeginClaim(backupTestWorkspace, "task-a", "attempt-a", "sandbox-a", "fixed-image")
	if err != nil {
		t.Fatal(err)
	}
	a := sandbox.WorkspaceAccess{WorkspaceID: backupTestWorkspace, TaskID: "task-a", AttemptID: "attempt-a", SandboxID: "sandbox-a", Generation: record.Generation}
	r.CreationSubmitted(a)
	if _, err = r.Bind(a, "uid-a", "node-a", "sha256:"+strings.Repeat("1", 64)); err != nil {
		t.Fatal(err)
	}
	m := sandbox.NewManager(nil, nil, sandbox.Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	w, err := sandbox.NewWorkspaceRuntime(m, r, "172.23.0.3", "fixed-image")
	if err != nil {
		t.Fatal(err)
	}
	b, err := sandbox.NewBackupRuntime(w, sandbox.BackupJobConfig{ImageRef: "sandboxd-restic:0.18.1-v1", SecretName: "restic-test", EndpointIP: "172.23.0.4"})
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(m, nil, nil, "sandboxd-target", "agent-token", "operator-token", time.Second, time.Second, func() bool { return true })
	s.EnableWorkspaces(w)
	s.EnableBackups(b)
	return s, r, a
}

func backupRequest(s *Server, method, path, token, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp := httptest.NewRecorder()
	s.Handler().ServeHTTP(resp, req)
	return resp
}

func TestBackupHTTPAuthAndDisabledBackend(t *testing.T) {
	s := NewServer(nil, nil, nil, "target", "agent-token", "operator-token", time.Second, time.Second, nil)
	for _, token := range []string{"", "operator-token", "agent-token"} {
		expected := http.StatusUnauthorized
		if token == "agent-token" {
			expected = http.StatusServiceUnavailable
		}
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			path := "/api/v1/workspaces/" + backupTestWorkspace + "/backup"
			if method == http.MethodPost {
				path += "/reserve"
			}
			if got := backupRequest(s, method, path, token, `{}`); got.Code != expected {
				t.Fatal(got.Code, got.Body.String())
			}
		}
	}
}

func TestBackupHTTPReserveAbortFinishAndStrictScope(t *testing.T) {
	s, r, a := backupAPI(t)
	raw, _ := json.Marshal(map[string]any{"binding": a, "checkpointId": backupTestCheckpoint, "jobId": "backup-a", "timeoutMs": 1})
	base := "/api/v1/workspaces/" + backupTestWorkspace + "/backup"
	if got := backupRequest(s, http.MethodPost, base+"/reserve", "agent-token", string(raw)); got.Code != 200 {
		t.Fatal(got.Code, got.Body.String())
	}
	if got := backupRequest(s, http.MethodGet, base, "agent-token", ""); got.Code != 200 || !strings.Contains(got.Body.String(), `"phase":"reserved"`) {
		t.Fatal(got.Code, got.Body.String())
	}
	if got := backupRequest(s, http.MethodPost, base+"/wait", "agent-token", string(raw)); got.Code != http.StatusGatewayTimeout {
		t.Fatal(got.Code, got.Body.String())
	}
	barrier, _ := r.Barrier(a.WorkspaceID)
	if barrier.Phase != "reserved" {
		t.Fatal("timeout unfroze barrier")
	}
	for _, action := range []string{"finish", "wait", "abort"} {
		wrong := strings.Replace(string(raw), `"jobId":"backup-a"`, `"jobId":"backup-b"`, 1)
		if got := backupRequest(s, http.MethodPost, base+"/"+action, "agent-token", wrong); got.Code != 409 {
			t.Fatal(got.Code, got.Body.String())
		}
	}
	for _, action := range []string{"abort", "finish", "finish"} {
		if got := backupRequest(s, http.MethodPost, base+"/"+action, "agent-token", string(raw)); got.Code != 200 {
			t.Fatal(got.Code, got.Body.String())
		}
	}
	barrier, _ = r.Barrier(a.WorkspaceID)
	if barrier.Phase != "released" {
		t.Fatal(barrier)
	}
}

func TestBackupHTTPRejectsCallerChosenExecutionConfig(t *testing.T) {
	s, _, a := backupAPI(t)
	body := map[string]any{"binding": a, "checkpointId": backupTestCheckpoint, "jobId": "backup-a"}
	base := "/api/v1/workspaces/" + backupTestWorkspace + "/backup/"
	for _, key := range []string{"image", "secretName", "repository", "argv"} {
		copy := map[string]any{}
		for k, v := range body {
			copy[k] = v
		}
		copy[key] = "caller-controlled"
		raw, _ := json.Marshal(copy)
		if got := backupRequest(s, http.MethodPost, base+"reserve", "agent-token", string(raw)); got.Code != 400 {
			t.Fatal(key, got.Code)
		}
	}
	for _, action := range []string{"shell", "unlock", "forget", "restore"} {
		raw, _ := json.Marshal(body)
		if got := backupRequest(s, http.MethodPost, base+action, "agent-token", string(raw)); got.Code != 400 {
			t.Fatal(action, got.Code)
		}
	}
	body["timeoutMs"] = 90001
	raw, _ := json.Marshal(body)
	if got := backupRequest(s, http.MethodPost, base+"reserve", "agent-token", string(raw)); got.Code != 400 {
		t.Fatal(got.Code)
	}
}
