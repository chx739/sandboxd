package sandbox

import "regexp"

// RestoreSource固定联合检查点的文件/仓库/业务镜像身份。不是可执行Pod配置，
// 后续可信执行器必须验证实际恢复结果，再调用completeRestore。
type RestoreSource struct {
	SnapshotID         string `json:"snapshotId"`
	ManifestSHA256     string `json:"manifestSha256"`
	RepositoryCryptoID string `json:"repositoryCryptoId"`
	ImageRef           string `json:"imageRef"`
	ImageID            string `json:"imageId"`
}

var restoreImageRef = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._:/@+-]{0,511}$`)
var restoreImageID = regexp.MustCompile(`^(sha256:[a-f0-9]{64}|[a-zA-Z0-9][a-zA-Z0-9._:/@+-]{0,430}@sha256:[a-f0-9]{64})$`)

func (s RestoreSource) valid() bool {
	return backupHash.MatchString(s.SnapshotID) && backupHash.MatchString(s.ManifestSHA256) && backupHash.MatchString(s.RepositoryCryptoID) && restoreImageRef.MatchString(s.ImageRef) && restoreImageID.MatchString(s.ImageID)
}

func (r *WorkspaceRegistry) reserveRestore(workspaceID, checkpointID, jobID string, job BackupJobConfig, source RestoreSource) (WorkspaceBarrier, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !source.valid() || job == (BackupJobConfig{}) {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	for id, old := range r.barriers {
		if id != workspaceID && old.Phase != "released" {
			return WorkspaceBarrier{}, ErrWorkspaceConflict
		}
	}
	if old, ok := r.barriers[workspaceID]; ok {
		if old.Operation == "restore" && old.Phase != "released" && old.CheckpointID == checkpointID && old.JobID == jobID && old.Job == job && old.RestoreSource == source {
			return old, nil
		}
		// 不复用失败/已经恢复过的目录，避免把残留文件当另一版本。
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return r.saveBarrier(WorkspaceBarrier{Version: 1, WorkspaceID: workspaceID, CheckpointID: checkpointID, Operation: "restore", JobID: jobID, PodName: "workspace-backup-" + jobID, Phase: "reserved", Job: job, RestoreSource: source})
}

// 仅供可信恢复执行器调用：它必须先读取真实成功结果，核对repo/snapshot/manifest。
// 先fsync镜像来源，再解除屏障；中间崩溃仍冻结，重试不更换来源。
func (r *WorkspaceRegistry) completeRestore(expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	b, ok := r.barriers[expected.WorkspaceID]
	if !ok || b != expected || b.Operation != "restore" || b.Phase != "stopped" || b.StopReason != "containers-terminated" || !b.RestoreSource.valid() {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	record := r.records[b.WorkspaceID]
	if record.RestoredFrom != (RestoreSource{}) && record.RestoredFrom != b.RestoreSource {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	if record.RestoredFrom == (RestoreSource{}) {
		record.RestoredFrom = b.RestoreSource
		if _, err := r.save(record); err != nil {
			return WorkspaceBarrier{}, err
		}
	}
	b.Phase, b.RestoreVerified = "released", true
	return r.saveBarrier(b)
}
