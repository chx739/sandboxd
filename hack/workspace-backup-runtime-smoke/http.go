package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"time"

	"github.com/chx739/sandboxd/internal/api"
	"github.com/chx739/sandboxd/internal/sandbox"
)

type backupTransport interface {
	Reserve(context.Context, sandbox.WorkspaceAccess, string, string) (sandbox.WorkspaceBarrier, error)
	Start(context.Context, sandbox.WorkspaceAccess, string, string) (sandbox.WorkspaceBarrier, error)
	Wait(context.Context, sandbox.WorkspaceBarrier) (sandbox.WorkspaceBarrier, sandbox.BackupResult, error)
	Finish(context.Context, sandbox.WorkspaceBarrier) (sandbox.WorkspaceBarrier, error)
}

type httpBackup struct{ server *httptest.Server }
type backupResponse struct {
	Barrier sandbox.WorkspaceBarrier `json:"barrier"`
	Result  sandbox.BackupResult     `json:"result"`
}

func newHTTPBackup(w *sandbox.WorkspaceRuntime, b *sandbox.BackupRuntime) (backupTransport, func()) {
	s := api.NewServer(nil, nil, nil, "sandboxd-target", "synthetic-backup-agent", "synthetic-backup-operator", time.Minute, 30*time.Second, func() bool { return true })
	s.EnableWorkspaces(w)
	s.EnableBackups(b)
	server := httptest.NewServer(s.Handler())
	for _, token := range []string{"", "synthetic-backup-operator"} {
		req, _ := http.NewRequest(http.MethodGet, server.URL+"/api/v1/workspaces/unused/backup", nil)
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		resp, err := server.Client().Do(req)
		must(err)
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized {
			server.Close()
			panic("backup HTTP accepted unauthorized identity")
		}
	}
	return &httpBackup{server}, server.Close
}

func (h *httpBackup) call(ctx context.Context, action string, a sandbox.WorkspaceAccess, checkpointID, jobID string) (backupResponse, error) {
	var out backupResponse
	raw, err := json.Marshal(map[string]any{"binding": a, "checkpointId": checkpointID, "jobId": jobID, "timeoutMs": 90000})
	if err != nil {
		return out, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.server.URL+"/api/v1/workspaces/"+a.WorkspaceID+"/backup/"+action, bytes.NewReader(raw))
	if err != nil {
		return out, err
	}
	req.Header.Set("Authorization", "Bearer synthetic-backup-agent")
	req.Header.Set("Content-Type", "application/json")
	resp, err := h.server.Client().Do(req)
	if err != nil {
		return out, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return out, fmt.Errorf("trusted backup HTTP status %d", resp.StatusCode)
	}
	raw, err = io.ReadAll(io.LimitReader(resp.Body, (16<<10)+1))
	if err != nil || len(raw) > 16<<10 {
		return out, fmt.Errorf("bounded backup HTTP output failed")
	}
	err = json.Unmarshal(raw, &out)
	return out, err
}

func (h *httpBackup) Reserve(ctx context.Context, a sandbox.WorkspaceAccess, cp, job string) (sandbox.WorkspaceBarrier, error) {
	v, e := h.call(ctx, "reserve", a, cp, job)
	return v.Barrier, e
}
func (h *httpBackup) Start(ctx context.Context, a sandbox.WorkspaceAccess, cp, job string) (sandbox.WorkspaceBarrier, error) {
	v, e := h.call(ctx, "start", a, cp, job)
	return v.Barrier, e
}
func (h *httpBackup) Wait(ctx context.Context, b sandbox.WorkspaceBarrier) (sandbox.WorkspaceBarrier, sandbox.BackupResult, error) {
	v, e := h.call(ctx, "wait", b.Access, b.CheckpointID, b.JobID)
	return v.Barrier, v.Result, e
}
func (h *httpBackup) Finish(ctx context.Context, b sandbox.WorkspaceBarrier) (sandbox.WorkspaceBarrier, error) {
	v, e := h.call(ctx, "finish", b.Access, b.CheckpointID, b.JobID)
	return v.Barrier, e
}
