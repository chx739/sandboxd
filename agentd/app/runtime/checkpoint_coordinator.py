"""顺序协调Session/Task与可信Go备份；不注册模型工具，不发起模型调用。"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from ..clients import SandboxdClient, HTTPResult
from ..tools.remote_files import WorkspaceBinding, WorkspaceExecutionError
from .checkpoints import CheckpointLedger, CheckpointConflict
from .execution import RecoveryAttempt
from .recovery import RecoveryConflict, new_id
from .session import SessionJournal
from .session_refs import SessionReferences


class CheckpointCoordinator:
    def __init__(self, client: SandboxdClient, ledger: CheckpointLedger,
                 refs: SessionReferences, repository_id: str):
        self.client, self.ledger, self.refs = client, ledger, refs
        if not isinstance(repository_id,str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}",repository_id):
            raise ValueError("checkpoint repository needs a logical ID")
        self.repository_id = repository_id

    @staticmethod
    def _ok(response: HTTPResult) -> dict[str, Any]:
        if response.status_code != 200 or not isinstance(response.body, dict):
            raise WorkspaceExecutionError("trusted checkpoint request did not complete; reconcile original job",
                                          recoverable=False, outcome_uncertain=True)
        return response.body

    @staticmethod
    def _barrier(value: dict, binding: dict, cp: str, job: str) -> dict:
        if (value.get("access") != binding or value.get("checkpointId") != cp
                or value.get("jobId") != job or value.get("operation") != "backup"
                or value.get("phase") not in {"reserved", "submitted", "identified", "stopped", "released"}):
            raise RecoveryConflict("checkpoint executor returned another scope/job")
        return value

    async def preflight(self, recovery: RecoveryAttempt) -> None:
        recovery.remaining()
        response = await self.client.get_workspace_backup(recovery.snapshot()["workspaceId"])
        if response.status_code != 404:
            self._ok(response)

    async def _call(self, action: str, binding: dict, cp: str, job: str,
                    recovery: RecoveryAttempt, *, cleanup: bool = False) -> dict:
        timeout = 30.0 if cleanup else min(90.0, recovery.remaining())
        result = self._ok(await self.client.workspace_backup(action, binding, cp, job, timeout=timeout))
        self._barrier(result.get("barrier", {}), binding, cp, job)
        return result

    async def save(self, recovery: RecoveryAttempt, journal: SessionJournal,
                   binding: WorkspaceBinding, node_id: str) -> dict:
        state = recovery.snapshot()
        recovery.remaining()  # 原截止时间已过，不发起新的存储请求。
        if (state["sessionId"] != journal.session_id or state["completeNodeId"] != node_id
                or binding.task_id != recovery.task_id or binding.attempt_id != recovery.attempt_id):
            raise RecoveryConflict("checkpoint does not match the current complete boundary")
        old = self.ledger.boundary(recovery.task_id, recovery.attempt_id, node_id)
        if old is not None and old["status"] == "FAILED":
            raise CheckpointConflict("previous save at this boundary failed; do not allocate another request")
        cp = old["checkpointId"] if old else new_id("cp")
        job = "backup-" + cp[3:]
        access = binding.as_request()
        if old is None:
            previous = await self.client.get_workspace_backup(binding.workspace_id)
            if previous.status_code != 404:
                barrier = self._ok(previous)
                if barrier.get("phase") != "released":
                    cp, job = barrier.get("checkpointId"), barrier.get("jobId")
                    self._barrier(barrier, access, cp, job)
                    if barrier["phase"] != "reserved" or not isinstance(cp,str) or job != "backup-" + cp[3:]:
                        raise RecoveryConflict("cannot infer Session binding for an already started orphan job")
        # 启动接续先cleanup旧attempt。当前save只处理当前边界，不猜旧任务的节点。
        if old is not None and old["status"] == "COMMITTED":
            committed = self.ledger.restore_point(cp)[0]
            await self._call("finish", access, cp, job, recovery)
            return committed
        await self._call("reserve", access, cp, job, recovery)
        reference = await self.refs.capture(journal, node_id)
        owner = self._ok(await self.client.get_workspace(binding.workspace_id))
        if WorkspaceBinding.from_record(owner) != binding:
            raise RecoveryConflict("checkpoint workspace owner changed")
        identity = {"sessionId": state["sessionId"], "branchId": state["branchId"],
                    "sessionNodeId": node_id, "workspaceId": binding.workspace_id,
                    "taskId": recovery.task_id, "attemptId": recovery.attempt_id,
                    "sandboxId": binding.sandbox_id, "podUid": owner["owner"]["podUid"],
                    "generation": binding.generation, "imageRef": owner["imageRef"],
                    "imageId": owner["imageId"], "sessionRef": reference}
        head = self.ledger.head(state["sessionId"], state["branchId"])
        parent = old["parentCheckpointId"] if old else head["checkpointId"] if head else None
        prepared = self.ledger.prepare(cp, identity, parent)
        if prepared["status"] == "PREPARING":
            await self._call("start", access, cp, job, recovery)
            result = (await self._call("wait", access, cp, job, recovery)).get("result", {})
            self.ledger.data_ready(cp, {"repositoryId": self.repository_id,
                                      "repositoryCryptoId": result.get("repositoryCryptoId"),
                                      "snapshotId": result.get("snapshotId"),
                                      "manifestSha256": result.get("manifestSha256")})
        barrier = self._barrier(self._ok(await self.client.get_workspace_backup(binding.workspace_id)), access, cp, job)
        if barrier["phase"] != "stopped" or barrier.get("stopReason") != "containers-terminated":
            raise RecoveryConflict("checkpoint executor is not positively stopped/frozen")
        owner = self._ok(await self.client.get_workspace(binding.workspace_id))
        # 此后无网络await；cancel与COMMITTED在RecoveryLedger同一短锁下排序。
        committed = recovery.ledger.commit_checkpoint(self.ledger, cp, owner, datetime.now(timezone.utc))
        await self._call("finish", access, cp, job, recovery)
        return committed

    async def cleanup(self, recovery: RecoveryAttempt) -> None:
        """Runner现有独立清理窗口内调用；旧job未证明停止就不允许换attempt。"""
        state = recovery.snapshot()
        pending = [r for r in self.ledger.pending() if r["identity"]["taskId"] == recovery.task_id
                   and r["identity"]["attemptId"] == recovery.attempt_id]
        response = await self.client.get_workspace_backup(state["workspaceId"])
        if response.status_code == 404:
            if pending:
                raise RecoveryConflict("pending checkpoint has no durable executor record")
            return
        barrier = self._ok(response)
        access = barrier.get("access", {})
        if access.get("taskId") != recovery.task_id or access.get("attemptId") != recovery.attempt_id:
            if barrier.get("phase") == "released" and not pending:
                return
            raise RecoveryConflict("cannot reconcile another checkpoint owner")
        binding = WorkspaceBinding(access["workspaceId"], access["taskId"], access["attemptId"],
                                   access["sandboxId"], access["generation"])
        cp, job = barrier.get("checkpointId"), barrier.get("jobId")
        self._barrier(barrier, binding.as_request(), cp, job)
        if barrier["phase"] != "released":
            await self._call("abort", binding.as_request(), cp, job, recovery, cleanup=True)
        # 保留COMMITTED；未提交数据保留为失败/孤立快照，不静默推进head。
        try:
            record = self.ledger.get(cp)
        except CheckpointConflict:
            record = None  # 崩溃发生在Reserve之后、PREPARING之前。
        if record is not None:
            if record["identity"]["taskId"] != recovery.task_id or record["identity"]["attemptId"] != recovery.attempt_id:
                raise RecoveryConflict("checkpoint metadata belongs to another owner")
            if record["status"] in {"PREPARING", "DATA_READY"}:
                self.ledger.fail(cp, "cancelled" if recovery.snapshot()["cancelRequested"] else "backup_failed")
        if barrier["phase"] != "released":
            await self._call("finish", binding.as_request(), cp, job, recovery, cleanup=True)

    def close(self) -> None:
        self.ledger.close()
