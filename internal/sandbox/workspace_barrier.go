package sandbox

import (
	"errors"
	"github.com/chx739/sandboxd/internal/backuphelper"
)

// WorkspaceBarrier lives in Go's fsynced control journal, outside the workspace.
// A process crash, HTTP timeout, or vanished Pod must not silently unfreeze it.
// Only trusted maintenance reconciliation advances the Pod stop evidence.
type WorkspaceBarrier struct {
	Version      int             `json:"schemaVersion"`
	WorkspaceID  string          `json:"workspaceId"`
	CheckpointID string          `json:"checkpointId"`
	Operation    string          `json:"operation"`
	Access       WorkspaceAccess `json:"access"`
	JobID        string          `json:"jobId"`
	PodName      string          `json:"podName"`
	PodUID       string          `json:"podUid,omitempty"`
	Phase        string          `json:"phase"`
	StopReason   string          `json:"stopReason,omitempty"`
}

func (r *WorkspaceRegistry) barrierActive(workspaceID string) bool {
	b, ok := r.barriers[workspaceID]
	return ok && b.Phase != "released"
}

func (r *WorkspaceRegistry) Barrier(workspaceID string) (WorkspaceBarrier, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	b, ok := r.barriers[workspaceID]
	return b, ok
}

func (r *WorkspaceRegistry) validateBarrier(b WorkspaceBarrier) error {
	if b.Version != 1 || !workspaceIDPattern.MatchString(b.WorkspaceID) || !runtimeIDPattern.MatchString(b.JobID) || len(b.JobID) > 46 || b.PodName != "workspace-backup-"+b.JobID {
		return errors.New("invalid workspace barrier identity")
	}
	if _, err := backuphelper.FixedArgs(backuphelper.Request{Operation: "backup", CheckpointID: b.CheckpointID}); err != nil {
		return err
	}
	record, ok := r.records[b.WorkspaceID]
	if !ok {
		return ErrWorkspaceConflict
	}
	switch b.Operation {
	case "backup":
		if b.Access.Validate() != nil || !matches(record, b.Access) {
			return ErrWorkspaceConflict
		}
	case "restore":
		// 目标只能是从未认领的新目录；已释放的旧目录也不能作为恢复目标。
		if record.Phase != "ready" || record.Generation != 0 || b.Access != (WorkspaceAccess{}) {
			return ErrWorkspaceConflict
		}
	default:
		return errors.New("invalid workspace barrier operation")
	}
	switch b.Phase {
	case "reserved", "submitted":
		if b.PodUID != "" || b.StopReason != "" {
			return ErrWorkspaceConflict
		}
	case "identified":
		if !runtimeIDPattern.MatchString(b.PodUID) || b.StopReason != "" {
			return ErrWorkspaceConflict
		}
	case "stopped", "released":
		switch b.StopReason {
		case "never-submitted":
			if b.PodUID != "" {
				return ErrWorkspaceConflict
			}
		case "containers-terminated":
			if !runtimeIDPattern.MatchString(b.PodUID) {
				return ErrWorkspaceConflict
			}
		default:
			return ErrWorkspaceConflict
		}
	default:
		return ErrWorkspaceConflict
	}
	previous, exists := r.barriers[b.WorkspaceID]
	if !exists || previous.JobID != b.JobID {
		if exists && (previous.Phase != "released" || previous.CheckpointID == b.CheckpointID) {
			return ErrWorkspaceConflict
		}
		if b.Phase != "reserved" || (b.Operation == "backup" && (record.Phase != "bound" || record.Owner.StopConfirmed)) {
			return ErrWorkspaceConflict
		}
		return nil
	}
	if previous.CheckpointID != b.CheckpointID || previous.Access != b.Access || previous.Operation != b.Operation || previous.PodName != b.PodName || (previous.PodUID != "" && b.PodUID != previous.PodUID) || (previous.StopReason != "" && b.StopReason != previous.StopReason) {
		return ErrWorkspaceConflict
	}
	if b == previous {
		return nil
	}
	allowed := map[string]map[string]bool{
		"reserved":   {"submitted": true, "stopped": true},
		"submitted":  {"identified": true},
		"identified": {"stopped": true},
		"stopped":    {"released": true},
	}
	if !allowed[previous.Phase][b.Phase] || (previous.Phase == "reserved" && b.Phase == "stopped" && b.StopReason != "never-submitted") || (previous.Phase == "identified" && b.Phase == "stopped" && b.StopReason != "containers-terminated") {
		return ErrWorkspaceConflict
	}
	return nil
}

func (r *WorkspaceRegistry) saveBarrier(b WorkspaceBarrier) (WorkspaceBarrier, error) {
	if err := r.validateBarrier(b); err != nil {
		return WorkspaceBarrier{}, err
	}
	if previous, ok := r.barriers[b.WorkspaceID]; ok && b == previous {
		return previous, nil
	}
	if _, err := r.journal.Append("workspace.barrier", b); err != nil {
		return WorkspaceBarrier{}, err
	}
	r.barriers[b.WorkspaceID] = b
	return b, nil
}

// reserveBarrier shares the file RPC mutex only during the fsynced transition.
// This drains an in-flight file RPC; the long backup does not hold that mutex.
func (r *WorkspaceRegistry) reserveBarrier(workspaceID, checkpointID, jobID, operation string, access WorkspaceAccess) (WorkspaceBarrier, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	b := WorkspaceBarrier{Version: 1, WorkspaceID: workspaceID, CheckpointID: checkpointID, Operation: operation, Access: access, JobID: jobID, PodName: "workspace-backup-" + jobID, Phase: "reserved"}
	if old, ok := r.barriers[workspaceID]; ok && old.Phase != "released" && old.CheckpointID == checkpointID && old.JobID == jobID && old.Access == access && old.Operation == operation {
		return old, nil
	}
	return r.saveBarrier(b)
}

func (r *WorkspaceRegistry) updateBarrier(expected WorkspaceBarrier, fn func(*WorkspaceBarrier)) (WorkspaceBarrier, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	b, ok := r.barriers[expected.WorkspaceID]
	// Exact CAS prevents stale cleanup from unfreezing a later maintenance job.
	if !ok || b != expected {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	fn(&b)
	return r.saveBarrier(b)
}

func (r *WorkspaceRegistry) submitBarrier(b WorkspaceBarrier) (WorkspaceBarrier, error) {
	return r.updateBarrier(b, func(next *WorkspaceBarrier) { next.Phase = "submitted" })
}

func (r *WorkspaceRegistry) identifyBarrier(b WorkspaceBarrier, podUID string) (WorkspaceBarrier, error) {
	return r.updateBarrier(b, func(next *WorkspaceBarrier) { next.Phase = "identified"; next.PodUID = podUID })
}

func (r *WorkspaceRegistry) stopBarrier(b WorkspaceBarrier, reason string) (WorkspaceBarrier, error) {
	return r.updateBarrier(b, func(next *WorkspaceBarrier) { next.Phase = "stopped"; next.StopReason = reason })
}

func (r *WorkspaceRegistry) releaseBarrier(b WorkspaceBarrier) (WorkspaceBarrier, error) {
	return r.updateBarrier(b, func(next *WorkspaceBarrier) { next.Phase = "released" })
}
