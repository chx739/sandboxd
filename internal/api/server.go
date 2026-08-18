package api

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const maxRequestBodyBytes = 16 << 10

type Server struct {
	manager       *sandbox.Manager
	pool          *sandbox.Pool
	token         string
	createTimeout time.Duration
	execTimeout   time.Duration
	mux           *http.ServeMux
}

func NewServer(
	manager *sandbox.Manager,
	pool *sandbox.Pool,
	token string,
	createTimeout time.Duration,
	execTimeout time.Duration,
) *Server {
	server := &Server{
		manager:       manager,
		pool:          pool,
		token:         token,
		createTimeout: createTimeout,
		execTimeout:   execTimeout,
		mux:           http.NewServeMux(),
	}
	server.routes()
	return server
}

func (s *Server) Handler() http.Handler {
	return s.mux
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /healthz", textOK)
	s.mux.HandleFunc("GET /readyz", textOK)
	s.mux.Handle("GET /metrics", promhttp.Handler())
	s.mux.Handle("POST /api/v1/sandboxes", s.authenticate(http.HandlerFunc(s.createSandbox)))
	s.mux.Handle("GET /api/v1/sandboxes", s.authenticate(http.HandlerFunc(s.listSandboxes)))
	s.mux.Handle("DELETE /api/v1/sandboxes/{id}", s.authenticate(http.HandlerFunc(s.deleteSandbox)))
	s.mux.Handle("POST /api/v1/sandboxes/{id}/exec", s.authenticate(http.HandlerFunc(s.execSandbox)))
}

func (s *Server) authenticate(next http.Handler) http.Handler {
	expected := []byte("Bearer " + s.token)
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		actual := []byte(request.Header.Get("Authorization"))
		if len(actual) != len(expected) || subtle.ConstantTimeCompare(actual, expected) != 1 {
			writeJSON(response, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		next.ServeHTTP(response, request)
	})
}

func textOK(response http.ResponseWriter, _ *http.Request) {
	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(http.StatusOK)
	_, _ = response.Write([]byte("ok\n"))
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json; charset=utf-8")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}
