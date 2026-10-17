package api

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
)

func TestWorkspaceRoutesRequireTokenAndConfiguredBackend(t *testing.T) {
	server := NewServer(nil, nil, nil, "sandboxd-target", "agent-token", "operator-token", time.Second, time.Second, func() bool { return true })
	for _, token := range []string{"", "operator-token", "agent-token"} {
		req := httptest.NewRequest(http.MethodPost, "/api/v1/workspaces", strings.NewReader(`{}`))
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		resp := httptest.NewRecorder()
		server.Handler().ServeHTTP(resp, req)
		expected := http.StatusUnauthorized
		if token == "agent-token" {
			expected = http.StatusServiceUnavailable
		}
		if resp.Code != expected {
			t.Fatal(resp.Code, resp.Body.String())
		}
	}
}

func TestWorkspaceFilesRejectBypassingPythonPolicy(t *testing.T) {
	registry, err := sandbox.OpenWorkspaceRegistry(filepath.Join(t.TempDir(), "workspaces.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer registry.Close()
	manager := sandbox.NewManager(nil, nil, sandbox.Config{Namespace: "sandboxd-demo", RuntimeClass: "gvisor"}, nil)
	runtime, err := sandbox.NewWorkspaceRuntime(manager, registry, "172.18.0.3", "fixed-image")
	if err != nil {
		t.Fatal(err)
	}
	server := NewServer(manager, nil, nil, "sandboxd-target", "agent-token", "operator-token", time.Second, time.Second, func() bool { return true })
	server.EnableWorkspaces(runtime)
	path := "/api/v1/workspaces/workspace-0123456789abcdef0123456789abcdef/files"
	binding := `"binding":{"workspaceId":"workspace-0123456789abcdef0123456789abcdef","taskId":"task-a","attemptId":"attempt-a","sandboxId":"a","generation":1}`
	for _, body := range []string{
		`{` + binding + `,"request":{"operation":"write_file","path":"../escape","content":"x"}}`,
		`{` + binding + `,"request":{"operation":"read_file","path":"a","argv":["sh"]}}`,
		`{` + binding + `,"request":{"operation":"shell","path":"a"}}`,
		`{` + binding + `,"request":{"operation":"read_file","path":"a"},"extra":true}`,
		`{` + binding + `,"request":{"operation":"read_file","path":"a"}} {}`,
		`{"binding":{"workspaceId":"workspace-other"},"request":{}}`,
	} {
		req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer agent-token")
		resp := httptest.NewRecorder()
		server.Handler().ServeHTTP(resp, req)
		if resp.Code != http.StatusBadRequest {
			t.Fatal(resp.Code, resp.Body.String())
		}
	}
	// A syntactically valid request for an unregistered owner still cannot execute.
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{`+binding+`,"request":{"operation":"read_file","path":"a"}}`))
	req.Header.Set("Authorization", "Bearer agent-token")
	resp := httptest.NewRecorder()
	server.Handler().ServeHTTP(resp, req)
	if resp.Code != http.StatusConflict {
		t.Fatal(resp.Code, resp.Body.String())
	}
}
