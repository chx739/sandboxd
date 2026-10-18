"""独立管理员进程；本模块不由agentd服务、插件或模型工具加载。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .app.clients import HTTPResult, _bounded_request
from .app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from .app.runtime.durable import JournalError
from .app.runtime.maintenance import _directory, _GC, _HASH, _JOB
from .app.runtime.session_refs import SessionReferences


class CleanupClient:
    def __init__(self, base_url: str, operator_token: str):
        url = urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or url.hostname not in {"127.0.0.1", "::1", "localhost"}
                or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}
                or not operator_token):
            raise ValueError("cleanup requires an explicit loopback backend and Operator credential")
        self._url = base_url.rstrip("/") + "/api/v1/repository/cleanup"
        self._headers = {"Authorization": "Bearer " + operator_token}
        self._http = httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False)

    async def get(self) -> HTTPResult:
        return await _bounded_request(self._http, "GET", self._url, headers=self._headers)

    async def request(self, action: str, record: dict, *, job_id: str | None = None, next_job_id: str | None = None) -> HTTPResult:
        if action not in {"reserve", "start", "wait", "abort", "retry", "finish", "cancel-reservation"}:
            raise ValueError("unsupported fixed cleanup action")
        body = {"gcId": record["gcId"], "jobId": job_id or record["jobId"], "operation": record["operation"],
                "snapshotId": record["snapshotId"], "timeoutMs": 90000}
        if (not _GC.fullmatch(body["gcId"]) or not _JOB.fullmatch(body["jobId"])
                or body["operation"] not in {"forget", "prune"}
                or (body["operation"] == "forget" and not _HASH.fullmatch(body["snapshotId"]))
                or (body["operation"] == "prune" and body["snapshotId"] != "")):
            raise ValueError("invalid fixed cleanup request")
        if action == "retry":
            if not isinstance(next_job_id, str) or not _JOB.fullmatch(next_job_id) or next_job_id == body["jobId"]:
                raise ValueError("retry needs a distinct fixed job ID")
            body["nextJobId"] = next_job_id
        elif next_job_id is not None:
            raise ValueError("unexpected next job ID")
        return await _bounded_request(self._http, "POST", self._url + "/" + action, headers=self._headers,
                                      json=body, timeout=35 if action in {"abort", "retry", "finish", "cancel-reservation"} else 92)

    async def close(self):
        await self._http.aclose()


class CleanupCoordinator:
    """调用者全程持有CheckpointLedger单写入锁；未知结果不自动重试/解冻。"""
    def __init__(self, ledger: CheckpointLedger, client):
        self.ledger, self.client = ledger, client
        self.owner = ledger.maintenance.binding()["owner"]

    @staticmethod
    def _ok(response: HTTPResult) -> dict:
        if response.status_code != 200 or not isinstance(response.body, dict):
            raise JournalError("trusted cleanup request incomplete; reconcile original GC/job before retry")
        return response.body

    def _barrier(self, barrier: dict, record: dict, *, previous: bool = False) -> dict:
        cleanup = {"gcId": record["gcId"]}
        if record["snapshotId"]:
            cleanup["snapshotId"] = record["snapshotId"]
        if (not isinstance(barrier, dict) or barrier.get("checkpointId") != "cp-" + record["gcId"][3:]
                or barrier.get("jobId") != (record["previousJobId"] if previous else record["jobId"])
                or barrier.get("operation") != record["operation"] or barrier.get("cleanup") != cleanup
                or barrier.get("job", {}).get("owner") != self.owner
                or barrier.get("workspaceId") != "workspace-" + self.owner["controllerId"][11:]
                or barrier.get("phase") not in {"reserved", "submitted", "identified", "stopped", "released"}):
            raise JournalError("cleanup executor returned another owner/target/attempt")
        return barrier

    async def _call(self, action: str, record: dict, **kwargs) -> dict:
        reply = self._ok(await self.client.request(action, record, **kwargs))
        self._barrier(reply.get("barrier"), record)
        return reply

    def _forget(self, record: dict) -> dict | None:
        return next((r for r in self.ledger.forget_requests() if r["gcId"] == record["gcId"]), None)

    def _proof(self, barrier: dict, record: dict, result: dict | None = None) -> dict:
        proof = barrier.get("cleanupResult")
        if (barrier.get("phase") not in {"stopped", "released"} or barrier.get("stopReason") != "containers-terminated"
                or barrier.get("cleanupCancelled") or not isinstance(proof, dict) or (result is not None and result != proof)
                or proof.get("owner") != self.owner or proof.get("operation") != record["operation"]
                or proof.get("snapshotId", "") != record["snapshotId"] or not proof.get("maintenanceImageId")
                or proof.get("podUid") != barrier.get("podUid") or not proof.get("podUid")):
            raise JournalError("missing durable verified cleanup result")
        if record["operation"] == "prune" and not _HASH.fullmatch(proof.get("outputSha256", "")):
            raise JournalError("invalid prune output identity")
        return proof

    async def run(self, gc_id: str) -> dict:
        record = self.ledger.maintenance.get(gc_id)
        if record is None:
            raise JournalError("unknown persisted cleanup request")
        if record["status"] != "PENDING":
            return record
        response = await self.client.get()
        forget = self._forget(record)
        if response.status_code == 404:
            if record["attempt"] != 1 or forget is not None:
                raise JournalError("durable cleanup has lost backend control; do not recreate executor")
            barrier = (await self._call("reserve", record))["barrier"]
        else:
            barrier = self._ok(response)
            if barrier.get("cleanup", {}).get("gcId") != gc_id:
                if barrier.get("phase") != "released" or record["attempt"] != 1 or forget is not None:
                    raise JournalError("another cleanup request owns the repository")
                barrier = (await self._call("reserve", record))["barrier"]
            elif barrier.get("jobId") == record["previousJobId"]:
                self._barrier(barrier, record, previous=True)
                if barrier["phase"] != "stopped" or barrier.get("cleanupResult"):
                    raise JournalError("retry source is not positively stopped/unverified")
                barrier = (await self._call("retry", record, job_id=record["previousJobId"], next_job_id=record["jobId"]))["barrier"]
        self._barrier(barrier, record)
        if forget is not None and forget["status"] == "FORGOTTEN" and not barrier.get("cleanupResult"):
            raise JournalError("forgotten metadata has lost the verified executor result")
        if barrier.get("cleanupCancelled"):
            if forget is not None:
                raise JournalError("executor cancelled a durable forget intent; manual investigation required")
            return self.ledger.maintenance.complete(gc_id, cancelled=True)
        if barrier["phase"] == "reserved":
            try:
                if record["operation"] == "forget":
                    self.ledger.prepare_forget(gc_id, self.owner["repositoryCryptoId"], record["snapshotId"], record["planSha256"])
                elif self.ledger.cleanup_preview()["planSha256"] != record["planSha256"]:
                    raise CheckpointConflict("prune reference preview changed")
            except CheckpointConflict:
                # 只捕获确定没有写遗忘意图的引用冲突；fsync错误/未知结果不能撤销。
                if record["attempt"] == 1 and self._forget(record) is None:
                    await self._call("cancel-reservation", record)
                    self.ledger.maintenance.complete(gc_id, cancelled=True)
                raise
            barrier = (await self._call("start", record))["barrier"]
        if record["operation"] == "forget" and self._forget(record) is None:
            raise JournalError("started cleanup has no prior durable reference intent")
        if barrier["phase"] != "released":
            reply = await self._call("wait", record)
            barrier = reply["barrier"]
            self._proof(barrier, record, reply.get("result"))
        else:
            self._proof(barrier, record)
        if record["operation"] == "forget":
            self.ledger.complete_forget(gc_id)
        if barrier["phase"] != "released":
            barrier = (await self._call("finish", record))["barrier"]
        if barrier["phase"] != "released":
            raise JournalError("cleanup metadata completed but executor remains frozen")
        self._proof(barrier, record)
        return self.ledger.maintenance.complete(gc_id)

    async def retry(self, gc_id: str) -> dict:
        record = self.ledger.maintenance.get(gc_id)
        if record is None or record["status"] != "PENDING":
            raise JournalError("only pending cleanup can be explicitly retried")
        barrier = self._ok(await self.client.get())
        if record["previousJobId"] and barrier.get("jobId") == record["previousJobId"]:
            return await self.run(gc_id)  # 前次retry已落盘但HTTP结果未知，沿原nextJob对账。
        self._barrier(barrier, record)
        if barrier["phase"] != "stopped" or barrier.get("cleanupResult"):
            raise JournalError("retry requires positively stopped unverified job; use reconcile for success")
        self.ledger.maintenance.retry(gc_id)  # 在远端retry之前先fsync新attempt身份。
        return await self.run(gc_id)

    async def abort(self, gc_id: str) -> dict:
        record = self.ledger.maintenance.get(gc_id)
        if record is None or record["status"] != "PENDING":
            raise JournalError("only pending cleanup may be stopped")
        barrier = self._ok(await self.client.get()); self._barrier(barrier, record)
        return (await self._call("abort", record))["barrier"]


async def execute(directory: Path, command: str, *, owner: dict | None = None, base_url: str = "http://127.0.0.1:10880",
                  gc_id: str | None = None, operation: str = "forget", snapshot_id: str = "", plan_sha256: str = "") -> dict:
    _directory(directory)
    if command != "bind-owner" and (not (directory / "checkpoints.jsonl").is_file() or not (directory / "session-refs").is_dir()):
        raise ValueError("requires existing checkpoint control directory")
    ledger = CheckpointLedger(directory / "checkpoints.jsonl", SessionReferences(directory))
    client = None
    try:
        if command == "bind-owner":
            return ledger.maintenance.bind(owner)
        if command not in {"apply", "reconcile", "retry", "abort"}:
            raise ValueError("unsupported fixed cleanup management command")
        token = os.getenv("SANDBOXD_OPERATOR_TOKEN", "")
        client = CleanupClient(base_url, token)
        coordinator = CleanupCoordinator(ledger, client)
        if command == "apply":
            ledger.maintenance.plan(gc_id, operation, snapshot_id, plan_sha256)
        if command == "retry":
            return await coordinator.retry(gc_id)
        if command == "abort":
            return await coordinator.abort(gc_id)
        return await coordinator.run(gc_id)
    finally:
        try:
            if client is not None:
                await client.close()
        finally:
            ledger.close()


def main():
    parser = argparse.ArgumentParser(description="独立可信管理员；停止同控制目录agentd后使用，不注册Agent工具")
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:10880")
    actions = parser.add_subparsers(dest="command", required=True)
    bind = actions.add_parser("bind-owner")
    for field in ("controller-id", "repository-crypto-id", "owner-snapshot-id"):
        bind.add_argument("--" + field, required=True)
    apply = actions.add_parser("apply")
    apply.add_argument("--gc-id", required=True); apply.add_argument("--operation", choices=("forget", "prune"), required=True)
    apply.add_argument("--snapshot-id", default=""); apply.add_argument("--plan-sha256", required=True)
    for name in ("reconcile", "retry", "abort"):
        actions.add_parser(name).add_argument("--gc-id", required=True)
    args = parser.parse_args()
    owner = ({"controllerId": args.controller_id, "repositoryCryptoId": args.repository_crypto_id,
              "ownerSnapshotId": args.owner_snapshot_id} if args.command == "bind-owner" else None)
    try:
        result = asyncio.run(execute(args.checkpoint_dir, args.command, owner=owner, base_url=args.base_url,
            gc_id=getattr(args, "gc_id", None), operation=getattr(args, "operation", "forget"),
            snapshot_id=getattr(args, "snapshot_id", ""), plan_sha256=getattr(args, "plan_sha256", "")))
    except Exception:
        # 异常链可能含HTTP URL/Header；正文不打印凭据或任意后端响应。
        parser.exit(1, "cleanup did not complete; inspect persisted local request and reconcile original GC/job\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
