package sandbox

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/chx739/sandboxd/internal/backuphelper"
	corev1 "k8s.io/api/core/v1"
)

func (b *BackupRuntime) CleanupBarrier() (WorkspaceBarrier, bool) {
	r := b.workspace.registry
	r.mu.Lock()
	defer r.mu.Unlock()
	if !b.job.Owner.Valid() || !r.repositoryMatches(b.job) {
		return WorkspaceBarrier{}, false
	}
	barrier, ok := r.barriers[r.cleanupWorkspaceID()]
	return barrier, ok
}

// 只供Operator协调器使用；冻结仓库后上层先fsync PREPARED，再Start。
// 维护关联ID不创建NFS目录，也不能由业务claim获取。
func (b *BackupRuntime) ReserveCleanup(ctx context.Context, gcID, jobID, operation, snapshotID string) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	return w.registry.reserveCleanup(gcID, jobID, operation, snapshotID, b.job)
}

func (b *BackupRuntime) StartCleanup(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	if !expected.isCleanup() {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	barrier, err := b.current(expected)
	if err != nil {
		return barrier, err
	}
	return b.startReserved(ctx, barrier)
}

// Wait核验真实固定Pod输出后先fsync结果。即使删Pod后崩溃，重启仍能
// 返回相同证明；超时/非零/坏日志均不释放屏障或宣称后端未执行。
func (b *BackupRuntime) WaitCleanup(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, CleanupResult, error) {
	if !expected.isCleanup() {
		return WorkspaceBarrier{}, CleanupResult{}, ErrWorkspaceConflict
	}
	for {
		barrier, err := b.observe(ctx, expected)
		if err != nil {
			return barrier, CleanupResult{}, err
		}
		if barrier.CleanupResult != (CleanupResult{}) {
			return barrier, barrier.CleanupResult, nil
		}
		if barrier.Phase == "stopped" {
			if barrier.StopReason != "containers-terminated" {
				return barrier, CleanupResult{}, ErrWorkspaceUnavailable
			}
			pod, err := b.ownedPod(ctx, barrier)
			if err != nil {
				return barrier, CleanupResult{}, err
			}
			if containersStopped(pod) && pod.Status.ContainerStatuses[0].State.Terminated.ExitCode == 0 && (pod.Status.Phase == corev1.PodRunning || pod.Status.Phase == corev1.PodPending) {
				if err := pollWorkspace(ctx); err != nil {
					return barrier, CleanupResult{}, err
				}
				continue
			}
			if !containersStopped(pod) || pod.Status.Phase != corev1.PodSucceeded || len(pod.Status.ContainerStatuses) != 1 || pod.Status.ContainerStatuses[0].State.Terminated.ExitCode != 0 || pod.Status.ContainerStatuses[0].ImageID == "" {
				return barrier, CleanupResult{}, ErrWorkspaceUnavailable
			}
			raw, err := b.readLogs(ctx, pod)
			if err != nil || len(raw) > backuphelper.MaxOutput {
				return barrier, CleanupResult{}, ErrWorkspaceUnavailable
			}
			var output struct {
				OK            bool                         `json:"ok"`
				Operation     string                       `json:"operation"`
				Owner         backuphelper.RepositoryOwner `json:"owner"`
				SnapshotID    string                       `json:"snapshotId"`
				Forgotten     bool                         `json:"forgotten"`
				Pruned        bool                         `json:"pruned"`
				AlreadyAbsent bool                         `json:"alreadyAbsent"`
				OutputSHA256  string                       `json:"outputSha256"`
				OutputBytes   int64                        `json:"outputBytes"`
				ResticVersion string                       `json:"resticVersion"`
			}
			if json.Unmarshal(raw, &output) != nil || !output.OK || output.Operation != barrier.Operation || output.Owner != b.job.Owner || output.SnapshotID != barrier.Cleanup.SnapshotID || output.ResticVersion != "0.18.1" || (barrier.Operation == "forget" && (!output.Forgotten || output.Pruned)) || (barrier.Operation == "prune" && (!output.Pruned || output.Forgotten)) {
				return barrier, CleanupResult{}, fmt.Errorf("%w: cleanup result contract", ErrWorkspaceUnavailable)
			}
			result := CleanupResult{Operation: output.Operation, SnapshotID: output.SnapshotID, Owner: output.Owner, ImageID: pod.Status.ContainerStatuses[0].ImageID, PodUID: string(pod.UID), AlreadyAbsent: output.AlreadyAbsent, OutputSHA256: output.OutputSHA256, OutputBytes: output.OutputBytes}
			barrier, err = b.workspace.registry.verifyCleanup(barrier, result)
			return barrier, result, err
		}
		if err := pollWorkspace(ctx); err != nil {
			return barrier, CleanupResult{}, err
		}
	}
}

// 失败任务只有确认停止并清理同UID后才允许显式新attempt，目标不变。
// 整个过程保留仓库屏障；不自动unlock/重试，不重新分配付费调用预算。
func (b *BackupRuntime) RetryCleanup(ctx context.Context, expected WorkspaceBarrier, jobID string) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	barrier, err := b.current(expected)
	if err != nil || !barrier.isCleanup() || barrier.Phase != "stopped" || barrier.CleanupResult != (CleanupResult{}) {
		return barrier, ErrWorkspaceConflict
	}
	if barrier.StopReason == "containers-terminated" {
		if err := b.cleanupStoppedPod(ctx, barrier); err != nil {
			return barrier, err
		}
	}
	return w.registry.retryCleanup(barrier, jobID)
}

// 上层先落盘FORGOTTEN再Finish。无成功证明的abort/失败不能解除冻结。
func (b *BackupRuntime) FinishCleanup(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	if !expected.isCleanup() {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	if old, ok := b.CleanupBarrier(); ok && old.Phase == "released" && old.JobID == expected.JobID && old.CheckpointID == expected.CheckpointID && old.Operation == expected.Operation && old.Cleanup == expected.Cleanup && old.Job == b.job && old.CleanupResult != (CleanupResult{}) {
		return old, nil
	}
	barrier, err := b.current(expected)
	if err != nil || !barrier.isCleanup() || barrier.Phase != "stopped" || barrier.CleanupResult == (CleanupResult{}) {
		return barrier, ErrWorkspaceConflict
	}
	if err := b.cleanupStoppedPod(ctx, barrier); err != nil {
		return barrier, err
	}
	return w.registry.releaseBarrier(barrier)
}

// 管理协调器在取得屏障后、尚未持久化PREPARED时，可能发现计划过期。
// 只有确定从未提交Pod的预留可撤销；此操作不能撤销已写入的遗忘意图。
func (b *BackupRuntime) CancelCleanupReservation(ctx context.Context, expected WorkspaceBarrier) (WorkspaceBarrier, error) {
	w := b.workspace
	if err := w.enter(ctx); err != nil {
		return WorkspaceBarrier{}, err
	}
	defer func() { <-w.gate }()
	if !expected.isCleanup() {
		return WorkspaceBarrier{}, ErrWorkspaceConflict
	}
	if old, ok := b.CleanupBarrier(); ok && old.CleanupCancelled && old.JobID == expected.JobID && old.Cleanup == expected.Cleanup && old.Operation == expected.Operation && old.Job == b.job {
		return old, nil
	}
	barrier, err := b.current(expected)
	if err != nil {
		return barrier, err
	}
	if barrier.Phase == "reserved" {
		barrier, err = w.registry.stopBarrier(barrier, "never-submitted")
		if err != nil {
			return barrier, err
		}
	}
	if barrier.Phase != "stopped" || barrier.StopReason != "never-submitted" || barrier.CleanupResult != (CleanupResult{}) {
		return barrier, ErrWorkspaceConflict
	}
	return w.registry.updateBarrier(barrier, func(next *WorkspaceBarrier) { next.Phase = "released"; next.CleanupCancelled = true })
}
