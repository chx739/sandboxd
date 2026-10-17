package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
	"github.com/chx739/sandboxd/internal/workspace"
)

func (s *Server) workspaceEnabled(response http.ResponseWriter) bool {
	if s.workspaces == nil {
		writeJSON(response, http.StatusServiceUnavailable, map[string]any{"error": "persistent workspace backend is not configured", "sandboxRecovery": false})
		return false
	}
	return true
}

func workspaceJSON(response http.ResponseWriter, request *http.Request, limit int64, value any) bool {
	request.Body = http.MaxBytesReader(response, request.Body, limit)
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	if decoder.Decode(value) != nil || decoder.Decode(new(any)) != io.EOF {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid bounded workspace request", "denyLayer": "workspace-policy"})
		return false
	}
	return true
}

func workspaceError(response http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	recoverable := false
	if errors.Is(err, sandbox.ErrWorkspaceConflict) {
		status = http.StatusConflict
	}
	if errors.Is(err, sandbox.ErrWorkspaceUnavailable) {
		status = http.StatusServiceUnavailable
		recoverable = true
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		status = http.StatusGatewayTimeout
		recoverable = true
	}
	log.Printf("workspace operation failed: %v", err)
	body := map[string]any{"error": "workspace operation did not complete", "sandboxRecovery": recoverable}
	if status == http.StatusConflict {
		body["denyLayer"] = "workspace-ownership"
	} else {
		body["errorType"] = "workspace-execution"
	}
	writeJSON(response, status, body)
}

func (s *Server) prepareWorkspace(response http.ResponseWriter, request *http.Request) {
	if !s.workspaceEnabled(response) {
		return
	}
	var input struct {
		WorkspaceID string `json:"workspaceId"`
		SessionID   string `json:"sessionId"`
		BranchID    string `json:"branchId"`
	}
	if !workspaceJSON(response, request, maxRequestBodyBytes, &input) {
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), s.createTimeout)
	defer cancel()
	result, err := s.workspaces.Prepare(ctx, input.WorkspaceID, input.SessionID, input.BranchID)
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, http.StatusCreated, result)
}

func (s *Server) getWorkspace(response http.ResponseWriter, request *http.Request) {
	if !s.workspaceEnabled(response) {
		return
	}
	result, err := s.workspaces.Registry().Get(request.PathValue("workspace"))
	if err != nil {
		writeJSON(response, http.StatusNotFound, map[string]string{"error": "workspace not found"})
		return
	}
	writeJSON(response, http.StatusOK, result)
}

func (s *Server) claimWorkspace(response http.ResponseWriter, request *http.Request) {
	if !s.workspaceEnabled(response) {
		return
	}
	var input struct {
		TaskID    string `json:"taskId"`
		AttemptID string `json:"attemptId"`
	}
	if !workspaceJSON(response, request, maxRequestBodyBytes, &input) {
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), s.createTimeout)
	defer cancel()
	result, err := s.workspaces.Claim(ctx, request.PathValue("workspace"), input.TaskID, input.AttemptID)
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, http.StatusCreated, result)
}

func workspaceBinding(response http.ResponseWriter, request *http.Request, access *sandbox.WorkspaceAccess) bool {
	if access.WorkspaceID == "" {
		access.WorkspaceID = request.PathValue("workspace")
	}
	if access.WorkspaceID != request.PathValue("workspace") || access.Validate() != nil {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid workspace binding", "denyLayer": "workspace-policy"})
		return false
	}
	return true
}

func (s *Server) releaseWorkspace(response http.ResponseWriter, request *http.Request) {
	if !s.workspaceEnabled(response) {
		return
	}
	var input sandbox.WorkspaceAccess
	if !workspaceJSON(response, request, maxRequestBodyBytes, &input) || !workspaceBinding(response, request, &input) {
		return
	}
	// 清理不跟随调用者断连。只停止本次UID/代次，不能宽泛删除同名新资源。
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	result, err := s.workspaces.Stop(ctx, input)
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, result)
}

func (s *Server) workspaceFiles(response http.ResponseWriter, request *http.Request) {
	if !s.workspaceEnabled(response) {
		return
	}
	var input struct {
		Binding sandbox.WorkspaceAccess `json:"binding"`
		Request json.RawMessage         `json:"request"`
	}
	if !workspaceJSON(response, request, workspace.MaxRequestBytes+4096, &input) || !workspaceBinding(response, request, &input.Binding) {
		return
	}
	operation, err := workspace.Decode(bytes.NewReader(input.Request))
	if err != nil {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid file operation", "denyLayer": "workspace-policy"})
		return
	}
	ctx, cancel := context.WithTimeout(request.Context(), s.execTimeout)
	defer cancel()
	result, err := s.workspaces.Files(ctx, input.Binding, operation)
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, result.Status, result.Body)
}
