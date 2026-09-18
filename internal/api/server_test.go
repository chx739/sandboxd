package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestReadyzReflectsReadiness(t *testing.T) {
	server := NewServer(
		nil,
		nil,
		nil,
		"sandboxd-target",
		"agent-token",
		"operator-token",
		time.Second,
		time.Second,
		func() bool { return false },
	)

	notReady := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	response := httptest.NewRecorder()
	server.Handler().ServeHTTP(response, notReady)
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("informer 未同步时 readyz 应为 503，得到 %d（body=%s）", response.Code, response.Body.String())
	}

	// healthz 只说明进程活着，即使依赖未同步也应保持 200。
	health := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	response = httptest.NewRecorder()
	server.Handler().ServeHTTP(response, health)
	if response.Code != http.StatusOK {
		t.Fatalf("healthz 不依赖 informer，应为 200，得到 %d", response.Code)
	}

	server.ready = func() bool { return true }
	response = httptest.NewRecorder()
	server.Handler().ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("informer 同步后 readyz 应为 200，得到 %d", response.Code)
	}
}
