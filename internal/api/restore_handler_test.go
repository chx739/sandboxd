package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
)

const restoreAPIWorkspace = "workspace-22222222222222222222222222222222"

func restoreAPIBody() map[string]any {
	return map[string]any{"sessionId": "session-test", "branchId": "branch-restored", "checkpointId": backupTestCheckpoint, "jobId": "restore-a", "timeoutMs": 1,
		"source": sandbox.RestoreSource{SnapshotID: strings.Repeat("1", 64), ManifestSHA256: strings.Repeat("2", 64), RepositoryCryptoID: strings.Repeat("3", 64), ImageRef: "fixed-image", ImageID: "sha256:" + strings.Repeat("4", 64)}}
}

func TestRestoreHTTPAuthenticationAndDisabledBackend(t *testing.T) {
	s := NewServer(nil, nil, nil, "target", "agent-token", "operator-token", time.Second, time.Second, nil)
	for _, token := range []string{"", "operator-token", "agent-token"} {
		want := 401
		if token == "agent-token" {
			want = 503
		}
		for _, method := range []string{http.MethodGet, http.MethodPost} {
			path := "/api/v1/workspaces/" + restoreAPIWorkspace + "/restore"
			if method == http.MethodPost {
				path += "/reserve"
			}
			if response := backupRequest(s, method, path, token, `{}`); response.Code != want {
				t.Fatal(response.Code, response.Body.String())
			}
		}
	}
}

func TestRestoreHTTPReserveScopeAndFailedTargetQuarantine(t *testing.T) {
	s, r, _ := backupAPI(t)
	if _, err := r.Register(restoreAPIWorkspace, "session-test", "branch-restored"); err != nil {
		t.Fatal(err)
	}
	r.Ready(restoreAPIWorkspace)
	body := restoreAPIBody()
	raw, _ := json.Marshal(body)
	base := "/api/v1/workspaces/" + restoreAPIWorkspace + "/restore"
	for range 2 {
		if got := backupRequest(s, "POST", base+"/reserve", "agent-token", string(raw)); got.Code != 200 {
			t.Fatal(got.Code, got.Body.String())
		}
	}
	if got := backupRequest(s, "GET", base, "agent-token", ""); got.Code != 200 {
		t.Fatal(got.Code)
	}
	if got := backupRequest(s, "POST", base+"/finish", "agent-token", string(raw)); got.Code != 409 {
		t.Fatal("finish waited on unstarted restore", got.Code)
	}
	if got := backupRequest(s, "POST", base+"/wait", "agent-token", string(raw)); got.Code != 504 {
		t.Fatal(got.Code, got.Body.String())
	}
	for _, key := range []string{"sessionId", "branchId", "jobId"} {
		copy := restoreAPIBody()
		copy[key] = "restore-other"
		wrong, _ := json.Marshal(copy)
		if got := backupRequest(s, "POST", base+"/abort", "agent-token", string(wrong)); got.Code != 409 {
			t.Fatal(key, got.Code)
		}
	}
	for _, action := range []string{"abort", "fail", "fail"} {
		if got := backupRequest(s, "POST", base+"/"+action, "agent-token", string(raw)); got.Code != 200 {
			t.Fatal(action, got.Code, got.Body.String())
		}
	}
	if got := backupRequest(s, "POST", base+"/finish", "agent-token", string(raw)); got.Code != 409 {
		t.Fatal("failed target promoted", got.Code)
	}
	if _, err := r.BeginClaim(restoreAPIWorkspace, "task-a", "attempt-a", "pod-a", "fixed-image"); err == nil {
		t.Fatal("failed target no longer quarantined")
	}
}

func TestRestoreHTTPCleanupDoesNotInheritDisconnectedRequest(t *testing.T) {
	s, r, _ := backupAPI(t)
	r.Register(restoreAPIWorkspace, "session-test", "branch-restored")
	r.Ready(restoreAPIWorkspace)
	raw, _ := json.Marshal(restoreAPIBody())
	base := "/api/v1/workspaces/" + restoreAPIWorkspace + "/restore/"
	if got := backupRequest(s, "POST", base+"reserve", "agent-token", string(raw)); got.Code != 200 {
		t.Fatal(got.Code)
	}
	for _, action := range []string{"abort", "fail"} {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		request := httptest.NewRequest("POST", base+action, strings.NewReader(string(raw))).WithContext(ctx)
		request.Header.Set("Authorization", "Bearer agent-token")
		response := httptest.NewRecorder()
		s.Handler().ServeHTTP(response, request)
		if response.Code != 200 {
			t.Fatal(action, response.Code, response.Body.String())
		}
	}
}

func TestRestoreHTTPRejectsExecutionConfigAndUnboundedFields(t *testing.T) {
	s, r, _ := backupAPI(t)
	r.Register(restoreAPIWorkspace, "session-test", "branch-restored")
	r.Ready(restoreAPIWorkspace)
	base := "/api/v1/workspaces/" + restoreAPIWorkspace + "/restore/"
	for _, key := range []string{"image", "secretName", "repository", "argv", "success"} {
		body := restoreAPIBody()
		body[key] = "caller-selected"
		raw, _ := json.Marshal(body)
		if got := backupRequest(s, "POST", base+"reserve", "agent-token", string(raw)); got.Code != 400 {
			t.Fatal(key, got.Code)
		}
	}
	for _, action := range []string{"unlock", "forget", "shell"} {
		raw, _ := json.Marshal(restoreAPIBody())
		if got := backupRequest(s, "POST", base+action, "agent-token", string(raw)); got.Code != 400 {
			t.Fatal(action, got.Code)
		}
	}
	for _, value := range []int{-1, 90001} {
		body := restoreAPIBody()
		body["timeoutMs"] = value
		raw, _ := json.Marshal(body)
		if got := backupRequest(s, "POST", base+"reserve", "agent-token", string(raw)); got.Code != 400 {
			t.Fatal(value, got.Code)
		}
	}
	body := restoreAPIBody()
	body["source"] = map[string]string{"snapshotId": "latest"}
	raw, _ := json.Marshal(body)
	if got := backupRequest(s, "POST", base+"reserve", "agent-token", string(raw)); got.Code != 400 {
		t.Fatal(got.Code)
	}
}
