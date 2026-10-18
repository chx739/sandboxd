package api

import (
	"context"
	"net/http"
	"regexp"
	"time"

	"github.com/chx739/sandboxd/internal/sandbox"
)

var cleanupGCID = regexp.MustCompile(`^gc-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$`)
var cleanupSnapshotID = regexp.MustCompile(`^[a-f0-9]{64}$`)

func (s *Server) getRepositoryCleanup(response http.ResponseWriter, request *http.Request) {
	if !s.backupEnabled(response) {
		return
	}
	barrier, ok := s.backups.CleanupBarrier()
	if !ok {
		writeJSON(response, http.StatusNotFound, map[string]string{"error": "cleanup not found"})
		return
	}
	writeJSON(response, http.StatusOK, barrier)
}

// 该固定入口只接受Operator身份；Agent服务不持有此Token。
// 输入没有仓库URL、Secret、镜像、自由argv或调用方success自报。
func (s *Server) repositoryCleanup(response http.ResponseWriter, request *http.Request) {
	if !s.backupEnabled(response) {
		return
	}
	action := request.PathValue("action")
	switch action {
	case "reserve", "start", "wait", "abort", "retry", "finish", "cancel-reservation":
	default:
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "unsupported fixed cleanup action"})
		return
	}
	var input struct {
		GCID       string `json:"gcId"`
		JobID      string `json:"jobId"`
		Operation  string `json:"operation"`
		SnapshotID string `json:"snapshotId"`
		NextJobID  string `json:"nextJobId"`
		TimeoutMS  int    `json:"timeoutMs"`
	}
	if !workspaceJSON(response, request, maxRequestBodyBytes, &input) {
		return
	}
	if !cleanupGCID.MatchString(input.GCID) || !backupJobID.MatchString(input.JobID) || input.TimeoutMS < 0 || input.TimeoutMS > 90000 || (input.Operation != "forget" && input.Operation != "prune") || (input.Operation == "forget" && !cleanupSnapshotID.MatchString(input.SnapshotID)) || (input.Operation == "prune" && input.SnapshotID != "") || (action == "retry" && (!backupJobID.MatchString(input.NextJobID) || input.NextJobID == input.JobID)) || (action != "retry" && input.NextJobID != "") {
		writeJSON(response, http.StatusBadRequest, map[string]string{"error": "invalid fixed cleanup identity/target/timeout", "denyLayer": "workspace-policy"})
		return
	}
	timeout := 90 * time.Second
	if input.TimeoutMS > 0 {
		timeout = time.Duration(input.TimeoutMS) * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(request.Context(), timeout)
	defer cancel()
	if action == "abort" || action == "retry" || action == "finish" || action == "cancel-reservation" {
		cancel()
		ctx, cancel = context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
	}
	var barrier sandbox.WorkspaceBarrier
	var err error
	if action == "reserve" {
		barrier, err = s.backups.ReserveCleanup(ctx, input.GCID, input.JobID, input.Operation, input.SnapshotID)
	} else {
		var ok bool
		barrier, ok = s.backups.CleanupBarrier()
		if !ok || barrier.Cleanup.GCID != input.GCID || barrier.JobID != input.JobID || barrier.Operation != input.Operation || barrier.Cleanup.SnapshotID != input.SnapshotID {
			workspaceError(response, sandbox.ErrWorkspaceConflict)
			return
		}
		switch action {
		case "start":
			barrier, err = s.backups.StartCleanup(ctx, barrier)
		case "wait":
			var result sandbox.CleanupResult
			barrier, result, err = s.backups.WaitCleanup(ctx, barrier)
			if err != nil {
				workspaceError(response, err)
				return
			}
			writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier, "result": result})
			return
		case "abort":
			barrier, err = s.backups.Abort(ctx, barrier)
		case "retry":
			barrier, err = s.backups.RetryCleanup(ctx, barrier, input.NextJobID)
		case "finish":
			barrier, err = s.backups.FinishCleanup(ctx, barrier)
		case "cancel-reservation":
			barrier, err = s.backups.CancelCleanupReservation(ctx, barrier)
		}
	}
	if err != nil {
		workspaceError(response, err)
		return
	}
	writeJSON(response, http.StatusOK, map[string]any{"barrier": barrier})
}
