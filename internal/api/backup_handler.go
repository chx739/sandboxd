package api

import (
	"context"
	"net/http"
	"regexp"
	"time"

	"github.com/chx739/sandboxd/internal/backuphelper"
	"github.com/chx739/sandboxd/internal/sandbox"
)

var backupJobID = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,45}$`)

func (s *Server) backupEnabled(response http.ResponseWriter) bool {
	if s.workspaces == nil || s.backups == nil {
		writeJSON(response, http.StatusServiceUnavailable, map[string]any{"error": "trusted backup backend is not configured", "sandboxRecovery": false})
		return false
	}
	return true
}

func (s *Server) getWorkspaceBackup(response http.ResponseWriter, request *http.Request) {
	if !s.backupEnabled(response) {
		return
	}
	barrier, ok := s.workspaces.Registry().Barrier(request.PathValue("workspace"))
	if !ok || (barrier.Operation != "backup" && barrier.Operation != "restore") {
		writeJSON(response, http.StatusNotFound, map[string]string{"error": "backup not found"})
		return
	}
	writeJSON(response, http.StatusOK, barrier)
}

func (s *Server) workspaceBackup(response http.ResponseWriter, request *http.Request) {
	if !s.backupEnabled(response) {
		return
	}
	action := request.PathValue("action")
	switch action {
	case "reserve", "start", "wait", "abort", "finish":
	default:
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "unsupported fixed backup action"})
		return
	}
	var input struct {
		Binding      sandbox.WorkspaceAccess `json:"binding"`
		CheckpointID string                  `json:"checkpointId"`
		JobID        string                  `json:"jobId"`
		TimeoutMS    int                     `json:"timeoutMs"`
	}
	if !workspaceJSON(response, request, maxRequestBodyBytes, &input) || !workspaceBinding(response, request, &input.Binding) {
		return
	}
	_, err := backuphelper.FixedArgs(backuphelper.Request{Operation: "backup", CheckpointID: input.CheckpointID})
	if err != nil || !backupJobID.MatchString(input.JobID) || input.TimeoutMS < 0 || input.TimeoutMS > 90000 {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid bounded backup identity/timeout", "denyLayer": "workspace-policy"})
		return
	}
	timeout := 90 * time.Second
	if input.TimeoutMS > 0 {
		timeout = time.Duration(input.TimeoutMS) * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(request.Context(), timeout)
	defer cancel()
	// 取消/清理不随HTTP断连丢失；只操作已落盘的同job/UID，不扩大目标。
	if action == "abort" || action == "finish" {
		cancel()
		ctx, cancel = context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
	}
	if action == "reserve" || action == "start" {
		var barrier sandbox.WorkspaceBarrier
		if action == "reserve" {
			barrier, err = s.backups.Reserve(ctx, input.Binding, input.CheckpointID, input.JobID)
		} else {
			barrier, err = s.backups.Start(ctx, input.Binding, input.CheckpointID, input.JobID)
		}
		if err != nil {
			workspaceError(response, err)
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier})
		return
	}
	barrier, ok := s.workspaces.Registry().Barrier(input.Binding.WorkspaceID)
	if !ok || barrier.Access != input.Binding || barrier.CheckpointID != input.CheckpointID || barrier.JobID != input.JobID {
		workspaceError(response, sandbox.ErrWorkspaceConflict)
		return
	}
	if action == "wait" {
		var result sandbox.BackupResult
		barrier, result, err = s.backups.Wait(ctx, barrier)
		if err != nil {
			workspaceError(response, err)
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier, "result": result})
		return
	}
	if action == "abort" {
		barrier, err = s.backups.Abort(ctx, barrier)
	} else {
		barrier, err = s.backups.Finish(ctx, barrier)
	}
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier})
}
