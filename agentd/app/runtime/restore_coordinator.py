"""历史文件恢复协调：复用可信Go执行器；不调用模型、不注册工具。"""
from __future__ import annotations

import asyncio
from typing import Any

from .checkpoint_coordinator import CheckpointCoordinator
from .execution import RecoveryAttempt
from .recovery import RecoveryConflict
from ..tools.remote_files import WorkspaceExecutionError


class RestoreCoordinator:
    def __init__(self, checkpoints: CheckpointCoordinator):
        self.checkpoints = checkpoints
        self.client, self.ledger = checkpoints.client, checkpoints.ledger

    def for_task(self, task_id: str) -> dict | None:
        return next((r for r in self.ledger.restore_requests() if r["taskId"] == task_id), None)

    def source(self, request: dict) -> dict:
        cp = self.ledger.get(request["sourceCheckpointId"])
        if cp["status"] != "COMMITTED":
            raise RecoveryConflict("restore source is not committed")
        if cp["data"]["repositoryId"] != self.checkpoints.repository_id:
            raise RecoveryConflict("restore source belongs to another configured repository")
        return {"snapshotId": cp["data"]["snapshotId"], "manifestSha256": cp["data"]["manifestSha256"],
                "repositoryCryptoId": cp["data"]["repositoryCryptoId"],
                "imageRef": cp["identity"]["imageRef"], "imageId": cp["identity"]["imageId"]}

    @staticmethod
    def target(request: dict) -> dict:
        return {key: request[key] for key in ("workspaceId", "sessionId", "branchId")}

    def barrier(self, value: dict, request: dict, source: dict) -> dict:
        if (not isinstance(value, dict) or value.get("workspaceId") != request["workspaceId"]
                or value.get("checkpointId") != request["sourceCheckpointId"]
                or value.get("jobId") != request["restoreId"] or value.get("operation") != "restore"
                or value.get("restoreSource") != source
                or value.get("phase") not in {"reserved", "submitted", "identified", "stopped", "released"}):
            raise RecoveryConflict("restore executor returned another source/target/job")
        return value

    async def _call(self, action: str, request: dict, source: dict, recovery: RecoveryAttempt,
                    *, cleanup: bool = False) -> dict:
        timeout = 30.0 if cleanup else min(90.0, recovery.remaining())
        body = self.checkpoints._ok(await self.client.workspace_restore(
            action, self.target(request), request["sourceCheckpointId"], request["restoreId"], source, timeout=timeout))
        self.barrier(body.get("barrier"), request, source)
        return body

    async def _verified_target(self, request: dict, source: dict) -> None:
        response = await self.client.get_workspace(request["workspaceId"])
        record = self.checkpoints._ok(response)
        if (any(record.get(k) != v for k, v in self.target(request).items())
                or record.get("restoredFrom") != source or record.get("phase") != "ready"
                or record.get("generation") != 0):
            raise RecoveryConflict("restored target is not verified, new and unclaimed")

    async def ensure_files(self, request: dict, recovery: RecoveryAttempt) -> dict:
        self.ledger.restore_point(request["sourceCheckpointId"])
        state = recovery.snapshot()
        if any(state[key] != request[key] for key in ("taskId", "sessionId", "branchId", "workspaceId")):
            raise RecoveryConflict("restore does not match the active Task")
        if request["status"] not in {"RESERVED", "FILES_READY"}:
            raise RecoveryConflict("restore is not pending")
        source = self.source(request)
        # 覆盖整段准备/恢复，而不只限制每次HTTP；重启仍受原Task截止时间约束。
        async with asyncio.timeout(min(90.0, recovery.remaining())):
            prepared = await self.client.prepare_workspace(request["workspaceId"], request["sessionId"], request["branchId"])
            if prepared.status_code not in {200, 201} or any(prepared.body.get(k) != v for k, v in self.target(request).items()):
                raise RecoveryConflict("restore target preparation failed")
            existing = await self.client.get_workspace_restore(request["workspaceId"])
            if existing.status_code == 404:
                if request["status"] == "FILES_READY":
                    raise RecoveryConflict("verified restore lost its executor record")
                barrier = (await self._call("reserve", request, source, recovery))["barrier"]
            else:
                barrier = self.barrier(self.checkpoints._ok(existing), request, source)
            if barrier["phase"] == "released":
                if barrier.get("restoreVerified") is not True:
                    raise WorkspaceExecutionError("restore target remains quarantined", recoverable=False)
            else:
                if request["status"] == "FILES_READY":
                    raise RecoveryConflict("verified restore is unexpectedly still frozen")
                if barrier["phase"] == "reserved":
                    await self._call("start", request, source, recovery)
                # 已核验但清理中断时只重试finish；不能要求已清理Pod重新提供结果。
                if not barrier.get("restoreVerified"):
                    waited = await self._call("wait", request, source, recovery)
                    result = waited.get("result", {})
                    if any(result.get(k) != source[k] for k in ("snapshotId", "manifestSha256", "repositoryCryptoId")):
                        raise RecoveryConflict("restore result identity mismatch")
                recovery.remaining()
                finished = (await self._call("finish", request, source, recovery))["barrier"]
                if finished.get("restoreVerified") is not True or finished["phase"] != "released":
                    raise RecoveryConflict("restore was not durably verified/released")
            await self._verified_target(request, source)
            recovery.remaining()
            return self.ledger.advance_restore(request["restoreId"], "FILES_READY")

    async def cleanup(self, request: dict, recovery: RecoveryAttempt) -> None:
        """先确认维护执行者停止，才允许Task的never-bound停止证明成立。"""
        if request["status"] in {"ATTACHED", "FAILED"}:
            return
        source = self.source(request)
        async with asyncio.timeout(30):
            response = await self.client.get_workspace_restore(request["workspaceId"])
            if response.status_code == 404:
                # 可信GET不存在屏障，只有本恢复协议尚未接受Reserve时可停止。
                if request["status"] != "RESERVED":
                    raise RecoveryConflict("cannot reconcile missing verified restore")
            else:
                barrier = self.barrier(self.checkpoints._ok(response), request, source)
                if barrier["phase"] != "released":
                    barrier = (await self._call("abort", request, source, recovery, cleanup=True))["barrier"]
                    action = "finish" if barrier.get("restoreVerified") else "fail"
                    released = (await self._call(action, request, source, recovery, cleanup=True))["barrier"]
                    if released["phase"] != "released":
                        raise RecoveryConflict("restore cleanup has not released executor")
            reason = "cancelled" if recovery.snapshot()["cancelRequested"] else "backup_failed"
            self.ledger.advance_restore(request["restoreId"], "FAILED", reason)
