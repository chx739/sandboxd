package sandbox

import (
	"strings"

	"github.com/chx739/sandboxd/internal/backuphelper"
)

type CleanupTarget struct {
	GCID       string `json:"gcId"`
	SnapshotID string `json:"snapshotId,omitempty"`
}

// 只由可信Wait核验真实Pod结果后写入；调用者不能提交success字段。
type CleanupResult struct {
	Operation     string                       `json:"operation"`
	SnapshotID    string                       `json:"snapshotId,omitempty"`
	Owner         backuphelper.RepositoryOwner `json:"owner"`
	ImageID       string                       `json:"maintenanceImageId"`
	PodUID        string                       `json:"podUid"`
	AlreadyAbsent bool                         `json:"alreadyAbsent,omitempty"`
	OutputSHA256  string                       `json:"outputSha256,omitempty"`
	OutputBytes   int64                        `json:"outputBytes,omitempty"`
}

type cleanupPlan struct {
	Operation string
	Target    CleanupTarget
}

func (b WorkspaceBarrier) isCleanup() bool { return b.Operation == "forget" || b.Operation == "prune" }

func cleanupCheckpoint(gcID string) (string, error) {
	if !strings.HasPrefix(gcID, "gc-") {
		return "", ErrWorkspaceConflict
	}
	cp := "cp-" + strings.TrimPrefix(gcID, "gc-")
	_, err := backuphelper.FixedArgs(backuphelper.Request{Operation: "backup", CheckpointID: cp})
	return cp, err
}

func (r *WorkspaceRegistry) cleanupWorkspaceID() string {
	return "workspace-" + strings.TrimPrefix(r.repository.Owner.ControllerID, "controller-")
}

func (r *WorkspaceRegistry) indexCleanup(b WorkspaceBarrier) {
	if b.isCleanup() {
		r.cleanupPlans[b.CheckpointID] = cleanupPlan{b.Operation, b.Cleanup}
		r.cleanupJobs[b.JobID] = true
	}
}

func (r *WorkspaceRegistry) validateCleanupFields(b WorkspaceBarrier) error {
	if !b.isCleanup() {
		if b.Cleanup != (CleanupTarget{}) || b.CleanupResult != (CleanupResult{}) || b.CleanupCancelled {
			return ErrWorkspaceConflict
		}
		for _, other := range r.barriers {
			if other.isCleanup() && other.Phase != "released" {
				return ErrWorkspaceConflict
			}
		}
		return nil
	}
	cp, err := cleanupCheckpoint(b.Cleanup.GCID)
	if err != nil || cp != b.CheckpointID || !b.Job.Owner.Valid() || !r.repositoryMatches(b.Job) || b.WorkspaceID != r.cleanupWorkspaceID() || b.Access != (WorkspaceAccess{}) || b.RestoreSource != (RestoreSource{}) || b.RestoreVerified {
		return ErrWorkspaceConflict
	}
	if _, exists := r.records[b.WorkspaceID]; exists {
		return ErrWorkspaceConflict
	}
	if _, err := backuphelper.FixedArgs(backuphelper.Request{Operation: b.Operation, Owner: b.Job.Owner, SnapshotID: b.Cleanup.SnapshotID}); err != nil {
		return err
	}
	if b.Operation == "forget" && b.Cleanup.SnapshotID == b.Job.Owner.OwnerSnapshotID {
		return ErrWorkspaceConflict
	}
	if prior, exists := r.cleanupPlans[b.CheckpointID]; exists && prior != (cleanupPlan{b.Operation, b.Cleanup}) {
		return ErrWorkspaceConflict
	}
	previous, exists := r.barriers[b.WorkspaceID]
	if _, known := r.cleanupPlans[b.CheckpointID]; known && (!exists || previous.CheckpointID != b.CheckpointID) {
		return ErrWorkspaceConflict
	}
	if (!exists || previous.JobID != b.JobID) && r.cleanupJobs[b.JobID] {
		return ErrWorkspaceConflict
	}
	for id, other := range r.barriers {
		if id != b.WorkspaceID && other.Phase != "released" {
			return ErrWorkspaceConflict
		}
	}
	result := b.CleanupResult
	if b.CleanupCancelled && (b.Phase != "released" || b.StopReason != "never-submitted" || result != (CleanupResult{})) {
		return ErrWorkspaceConflict
	}
	if result != (CleanupResult{}) {
		if (b.Phase != "stopped" && b.Phase != "released") || b.StopReason != "containers-terminated" || result.Operation != b.Operation || result.Owner != b.Job.Owner || result.SnapshotID != b.Cleanup.SnapshotID || result.PodUID != b.PodUID || result.ImageID == "" || len(result.ImageID) > 512 || result.OutputBytes < 0 || result.OutputBytes > backuphelper.MaxOutput {
			return ErrWorkspaceConflict
		}
		if b.Operation == "forget" && (result.OutputSHA256 != "" || result.OutputBytes != 0) {
			return ErrWorkspaceConflict
		}
		if b.Operation == "prune" && (!backupHash.MatchString(result.OutputSHA256) || result.AlreadyAbsent) {
			return ErrWorkspaceConflict
		}
	}
	if b.Phase == "released" && result == (CleanupResult{}) && !b.CleanupCancelled {
		return ErrWorkspaceConflict
	}
	return nil
}

func (r *WorkspaceRegistry) validCleanupRetry(old, next WorkspaceBarrier, exists bool) bool {
	return exists && old.isCleanup() && old.Phase == "stopped" && old.CleanupResult == (CleanupResult{}) && next.Phase == "reserved" && next.CleanupResult == (CleanupResult{}) && old.Cleanup == next.Cleanup && old.Operation == next.Operation && old.Job == next.Job && old.CheckpointID == next.CheckpointID
}

func (r *WorkspaceRegistry) reserveCleanup(gcID, jobID, operation, snapshotID string, job BackupJobConfig) (WorkspaceBarrier, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !job.Owner.Valid() || !r.repositoryMatches(job) {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	cp, err := cleanupCheckpoint(gcID)
	if err != nil || (operation != "forget" && operation != "prune") {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	b := WorkspaceBarrier{Version: 1, WorkspaceID: r.cleanupWorkspaceID(), CheckpointID: cp, JobID: jobID, PodName: "workspace-backup-" + jobID, Phase: "reserved", Operation: operation, Job: job, Cleanup: CleanupTarget{gcID, snapshotID}}
	if old, exists := r.barriers[b.WorkspaceID]; exists && old.JobID == b.JobID && old.CheckpointID == b.CheckpointID && old.Operation == b.Operation && old.Cleanup == b.Cleanup && old.Job == b.Job {
		return old, nil
	}
	// reserve不能借重试替换活动请求，必须先经可信Retry清理旧Pod。
	if old, exists := r.barriers[b.WorkspaceID]; exists && old.Phase != "released" {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return r.saveBarrier(b)
}

func (r *WorkspaceRegistry) verifyCleanup(b WorkspaceBarrier, result CleanupResult) (WorkspaceBarrier, error) {
	if !b.isCleanup() || b.Phase != "stopped" || b.StopReason != "containers-terminated" {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return r.updateBarrier(b, func(next *WorkspaceBarrier) { next.CleanupResult = result })
}

func (r *WorkspaceRegistry) retryCleanup(expected WorkspaceBarrier, jobID string) (WorkspaceBarrier, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	old, exists := r.barriers[expected.WorkspaceID]
	if !exists || old != expected || !old.isCleanup() || old.Phase != "stopped" || old.CleanupResult != (CleanupResult{}) || jobID == old.JobID {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	next := old
	next.JobID, next.PodName, next.Phase, next.PodUID, next.StopReason = jobID, "workspace-backup-"+jobID, "reserved", "", ""
	return r.saveBarrier(next)
}
