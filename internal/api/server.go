package api

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"
	"time"

	"github.com/chx739/sandboxd/internal/approval"
	"github.com/chx739/sandboxd/internal/sandbox"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const maxRequestBodyBytes = 16 << 10

type Server struct {
	manager       *sandbox.Manager
	pool          *sandbox.Pool
	plans         *approval.Service
	agentToken    string
	operatorToken string
	createTimeout time.Duration
	execTimeout   time.Duration
	mux           *http.ServeMux
}

func NewServer(
	manager *sandbox.Manager,
	pool *sandbox.Pool,
	plans *approval.Service,
	agentToken string,
	operatorToken string,
	createTimeout time.Duration,
	execTimeout time.Duration,
) *Server {
	server := &Server{
		manager:       manager,
		pool:          pool,
		plans:         plans,
		agentToken:    agentToken,
		operatorToken: operatorToken,
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

	// Agent 可以使用沙箱并提交计划，但不能越过 Operator 执行集群写操作。
	s.mux.Handle("POST /api/v1/sandboxes", s.requireAgent(http.HandlerFunc(s.createSandbox)))
	s.mux.Handle("GET /api/v1/sandboxes", s.requireAgent(http.HandlerFunc(s.listSandboxes)))
	s.mux.Handle("DELETE /api/v1/sandboxes/{id}", s.requireAgent(http.HandlerFunc(s.deleteSandbox)))
	s.mux.Handle("POST /api/v1/sandboxes/{id}/exec", s.requireAgent(http.HandlerFunc(s.execSandbox)))
	s.mux.Handle("POST /api/v1/plans", s.requireAgent(http.HandlerFunc(s.proposePlan)))

	// Operator 需要先读取 Plan 再决策，所以 list 同时允许两种角色。
	s.mux.Handle("GET /api/v1/plans", s.requireEither(http.HandlerFunc(s.listPlans)))
	s.mux.Handle("POST /api/v1/plans/{id}/approve", s.requireOperator(http.HandlerFunc(s.approvePlan)))
	s.mux.Handle("POST /api/v1/plans/{id}/reject", s.requireOperator(http.HandlerFunc(s.rejectPlan)))
}

func (s *Server) requireAgent(next http.Handler) http.Handler {
	return authenticate([]string{s.agentToken}, next)
}

func (s *Server) requireOperator(next http.Handler) http.Handler {
	return authenticate([]string{s.operatorToken}, next)
}

func (s *Server) requireEither(next http.Handler) http.Handler {
	return authenticate([]string{s.agentToken, s.operatorToken}, next)
}

func authenticate(tokens []string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(response http.ResponseWriter, request *http.Request) {
		authorized := 0
		actual := []byte(request.Header.Get("Authorization"))
		for _, token := range tokens {
			expected := []byte("Bearer " + token)
			if len(actual) == len(expected) {
				authorized |= subtle.ConstantTimeCompare(actual, expected)
			}
		}
		if authorized != 1 {
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
