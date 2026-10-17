package api

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/chx739/sandboxd/internal/backuphelper"
	"github.com/chx739/sandboxd/internal/sandbox"
)

func (s *Server) getWorkspaceRestore(response http.ResponseWriter, request *http.Request) {
	if !s.backupEnabled(response) {
		return
	}
	barrier, ok := s.workspaces.Registry().Barrier(request.PathValue("workspace"))
	if !ok || barrier.Operation != "restore" {
		writeJSON(response, http.StatusNotFound, map[string]string{"error": "restore not found"})
		return
	}
	writeJSON(response, http.StatusOK, barrier)
}

func (s *Server) workspaceRestore(response http.ResponseWriter, request *http.Request) {
	if !s.backupEnabled(response) {
		return
	}
	action := request.PathValue("action")
	switch action {
	case "reserve", "start", "wait", "abort", "finish", "fail":
	default:
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "unsupported fixed restore action"})
		return
	}
	var input struct {
		SessionID    string                `json:"sessionId"`
		BranchID     string                `json:"branchId"`
		CheckpointID string                `json:"checkpointId"`
		JobID        string                `json:"jobId"`
		Source       sandbox.RestoreSource `json:"source"`
		TimeoutMS    int                   `json:"timeoutMs"`
	}
	if !workspaceJSON(response, request, maxRequestBodyBytes, &input) {
		return
	}
	_, err := backuphelper.FixedArgs(backuphelper.Request{Operation: "backup", CheckpointID: input.CheckpointID})
	if err != nil || !backupJobID.MatchString(input.JobID) || !strings.HasPrefix(input.JobID, "restore-") || input.Source.Validate() != nil || input.TimeoutMS < 0 || input.TimeoutMS > 90000 {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid bounded restore identity/source/timeout", "denyLayer": "workspace-policy"})
		return
	}
	workspace := request.PathValue("workspace")
	record, err := s.workspaces.Registry().Get(workspace)
	if err != nil || record.SessionID != input.SessionID || record.BranchID != input.BranchID {
		workspaceError(response, sandbox.ErrWorkspaceConflict)
		return
	}
	timeout := 90 * time.Second
	if input.TimeoutMS > 0 {
		timeout = time.Duration(input.TimeoutMS) * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(request.Context(), timeout)
	defer cancel()
	if action == "abort" || action == "finish" || action == "fail" {
		cancel()
		ctx, cancel = context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
	}
	if action == "reserve" || action == "start" {
		var barrier sandbox.WorkspaceBarrier
		if action == "reserve" {
			barrier, err = s.backups.ReserveRestore(ctx, workspace, input.CheckpointID, input.JobID, input.Source)
		} else {
			barrier, err = s.backups.StartRestore(ctx, workspace, input.CheckpointID, input.JobID, input.Source)
		}
		if err != nil {
			workspaceError(response, err)
			return
		}
		writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier})
		return
	}
	barrier, ok := s.workspaces.Registry().Barrier(workspace)
	if !ok || barrier.Operation != "restore" || barrier.CheckpointID != input.CheckpointID || barrier.JobID != input.JobID || barrier.RestoreSource != input.Source {
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
		barrier, err = s.backups.FinishRestore(ctx, barrier, action == "finish")
	}
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier})
}
