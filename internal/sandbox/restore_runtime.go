package sandbox

import "context"

// 复用同一可信维护执行器的Pod归属、终止证明及清理；没有新增Agent工具。
func (b *BackupRuntime) ReserveRestore(ctx context.Context, workspaceID, checkpointID, jobID string, source RestoreSource) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	if source.ImageRef != w.image {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return w.registry.reserveRestore(workspaceID, checkpointID, jobID, b.job, source)
}

func (b *BackupRuntime) StartRestore(ctx context.Context, workspaceID, checkpointID, jobID string, source RestoreSource) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	if source.ImageRef != w.image {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	barrier, err := w.registry.reserveRestore(workspaceID, checkpointID, jobID, b.job, source)
	if err != nil {
		return barrier, err
	}
	return b.startReserved(ctx, barrier)
}

// success不是信任调用方的成功自报：true时仍重新读取维护Pod成功结果，
// 核对固定repo/snapshot/manifest后才落盘来源并放行。false只停止/清理并隔离目标。
func (b *BackupRuntime) FinishRestore(ctx context.Context, expected WorkspaceBarrier, success bool) (WorkspaceBarrier, error) {
	if expected.Operation != "restore" || !expected.RestoreSource.valid() {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	return b.finish(ctx, expected, success)
}
