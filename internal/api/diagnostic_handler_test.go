package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestKubernetesDiagnosticRejectsBeforeExec(t *testing.T) {
	server := NewServer(
		nil,
		nil,
		nil,
		"sandboxd-target",
		"agent-token",
		"operator-token",
		time.Second,
		time.Second,
	)

	tests := []struct {
		name       string
		token      string
		body       string
		wantStatus int
		wantBody   string
	}{
		{
			name:       "requires agent authentication",
			body:       `{"operation":"delete_namespace","namespace":"sandboxd-target"}`,
			wantStatus: http.StatusUnauthorized,
			wantBody:   "unauthorized",
		},
		{
			name:       "write operation is policy denied",
			token:      "agent-token",
			body:       `{"operation":"delete_namespace","namespace":"sandboxd-target"}`,
			wantStatus: http.StatusForbidden,
			wantBody:   `"denyLayer":"tool-policy"`,
		},
		{
			name:       "other namespace is policy denied",
			token:      "agent-token",
			body:       `{"operation":"list_pods","namespace":"kube-system"}`,
			wantStatus: http.StatusForbidden,
			wantBody:   `"denyLayer":"tool-policy"`,
		},
		{
			name:       "unknown JSON field is rejected",
			token:      "agent-token",
			body:       `{"operation":"list_pods","namespace":"sandboxd-target","url":"https://example.com"}`,
			wantStatus: http.StatusBadRequest,
			wantBody:   `"denyLayer":"tool-policy"`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request := httptest.NewRequest(
				http.MethodPost,
				"/api/v1/sandboxes/demo/diagnostics/kubernetes",
				strings.NewReader(test.body),
			)
			if test.token != "" {
				request.Header.Set("Authorization", "Bearer "+test.token)
			}
			response := httptest.NewRecorder()

			server.Handler().ServeHTTP(response, request)

			if response.Code != test.wantStatus {
				t.Fatalf("status = %d, want %d; body=%s", response.Code, test.wantStatus, response.Body.String())
			}
			if !strings.Contains(response.Body.String(), test.wantBody) {
				t.Fatalf("body = %q, want substring %q", response.Body.String(), test.wantBody)
			}
		})
	}
}
