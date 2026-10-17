"""持久恢复账本。此模块只管理状态，不自行认领 Pod、重放工具或调用模型。"""
from __future__ import annotations

import copy
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..redaction import redact_text
from ..models import AgentTrace
from .durable import DurableJournal, JournalCorrupt

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "limit_exceeded"})
_IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,95}\Z")
_RESOURCE_UID = re.compile(r"[a-z0-9][a-z0-9-]{0,127}\Z")
_SANDBOX_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class RecoveryConflict(RuntimeError):
    pass


class RecoveryLimit(RecoveryConflict):
    pass


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid4().hex


def _identity(value: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise ValueError("invalid recovery identity")
    return value


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("recovery time needs an explicit timezone")
    return value.astimezone(timezone.utc)


def _resource_uid(value: str) -> str:
    # Kubernetes UID 是资源身份，不是以字母开头的本项目逻辑 ID。
    if not isinstance(value, str) or not _RESOURCE_UID.fullmatch(value):
        raise ValueError("invalid Kubernetes resource UID")
    return value


def _sandbox_id(value: str) -> str:
    if not isinstance(value, str) or not _SANDBOX_ID.fullmatch(value):
        raise ValueError("invalid sandbox runtime ID")
    return value


class RecoveryLedger:
    """每次转移先 fsync，再发布内存投影；无费用、无外部副作用。

    单写入者由 DurableJournal 的进程锁保证。调用者只能在 Go 已确认旧
    执行者停止后调用 owner_stopped；这个账本本身不是分布式 fencing。
    TaskStore/Runner已接入；本模块单测仍不能代替真实故障验收。
    """

    def __init__(self, path: Path) -> None:
        self._journal = DurableJournal(path)
        self._mutex = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._traces: dict[str, dict[str, Any]] = {}
        self._created_at: dict[str, str] = {}
        try:
            for event in self._journal.entries():
                if event["type"] == "attempt.trace":
                    data = event["data"]
                    self._check_trace(data)
                    if data["attemptId"] in self._traces:
                        raise JournalCorrupt("duplicate attempt trace")
                    self._traces[data["attemptId"]] = data
                    continue
                if event["type"] != "task.state":
                    raise JournalCorrupt("unsupported recovery ledger event")
                state = event["data"]
                self._check_projection(state)
                self._tasks[state["taskId"]] = state
                self._created_at.setdefault(state["taskId"], event["timestamp"])
        except BaseException:
            self._journal.close()
            raise

    def _check_projection(self, state: dict[str, Any]) -> None:
        # A malformed complete state is not silently skipped or reset to zero.
        try:
            fields = {
                "schemaVersion", "taskId", "sessionId", "branchId", "workspaceId",
                "alert", "status", "cancelRequested", "deadline", "limits",
                "maxRecoveries", "attempts", "calls", "commands", "provider", "completeNodeId",
            }
            version = state["schemaVersion"]
            if type(version) is not int or version not in {1, 2}:
                raise ValueError
            if version == 2:
                fields.add("timing")
            if set(state) != fields:
                raise ValueError
            task_id = _identity(state["taskId"])
            for key in ("sessionId", "branchId", "workspaceId"):
                _identity(state[key])
            if state["status"] not in TERMINAL | {"queued", "running", "interrupted", "cancelling"}:
                raise ValueError
            if version == 1:
                _utc(datetime.fromisoformat(state["deadline"]))
            else:
                self._check_timing(state)
            if type(state["cancelRequested"]) is not bool:
                raise ValueError
            if type(state["maxRecoveries"]) is not int or not 0 <= state["maxRecoveries"] <= 10:
                raise ValueError
            if not isinstance(state["alert"], dict) or not isinstance(state["attempts"], list):
                raise ValueError
            if not isinstance(state["commands"], list) or not isinstance(state["calls"], dict):
                raise ValueError
            if len(state["commands"]) > 64 or len(state["attempts"]) > state["maxRecoveries"] + 1:
                raise ValueError
            attempt_ids = set()
            for attempt in state["attempts"]:
                _identity(attempt["attemptId"])
                _utc(datetime.fromisoformat(attempt["startedAt"]))
                if attempt["attemptId"] in attempt_ids or type(attempt["ownerStopped"]) is not bool:
                    raise ValueError
                if "claimRequested" in attempt and type(attempt["claimRequested"]) is not bool:
                    raise ValueError
                if (attempt["sandboxId"] is None) != (attempt["podUid"] is None):
                    raise ValueError
                if attempt["sandboxId"] is not None:
                    _sandbox_id(attempt["sandboxId"])
                    _resource_uid(attempt["podUid"])
                attempt_ids.add(attempt["attemptId"])
            if set(state["limits"]) != {"model", "tool", "router"}:
                raise ValueError
            for kind, limit in state["limits"].items():
                if type(limit) is not int or not 0 <= limit <= 100:
                    raise ValueError
                if sum(c["kind"] == kind for c in state["calls"].values()) > limit:
                    raise ValueError
            for call_id, call in state["calls"].items():
                _identity(call_id)
                if call["kind"] not in state["limits"] or call["state"] not in {"reserved", "finished", "uncertain"}:
                    raise ValueError
                if call["attemptId"] not in attempt_ids:
                    raise ValueError
            previous = self._tasks.get(task_id)
            if previous:
                for key in ("schemaVersion", "sessionId", "branchId", "workspaceId", "limits", "maxRecoveries", "alert"):
                    if previous[key] != state[key]:
                        raise ValueError
                if version == 1 and previous["deadline"] != state["deadline"]:
                    raise ValueError
                if version == 2:
                    for key, old in previous["timing"].items():
                        # 时间锚点只能首次设置；重启/新 attempt 不能把停机时间抹掉。
                        if old is not None and state["timing"][key] != old:
                            raise ValueError
                if previous["cancelRequested"] and not state["cancelRequested"]:
                    raise ValueError
                if previous["status"] in TERMINAL and state != previous:
                    raise ValueError
                if not set(previous["calls"]) <= set(state["calls"]):
                    raise ValueError
                if len(state["attempts"]) < len(previous["attempts"]):
                    raise ValueError
                for index, old in enumerate(previous["attempts"]):
                    current = state["attempts"][index]
                    for key in ("attemptId", "startedAt"):
                        if old[key] != current[key]:
                            raise ValueError
                    if old["ownerStopped"] and current != old:
                        raise ValueError
                    if old.get("claimRequested") is True and current.get("claimRequested") is not True:
                        raise ValueError
                    if old["sandboxId"] is not None:
                        if any(current[key] != old[key] for key in ("sandboxId", "podUid")):
                            raise ValueError
                if previous["provider"] is not None and state["provider"] != previous["provider"]:
                    raise ValueError
                for call_id, old in previous["calls"].items():
                    current = state["calls"][call_id]
                    if {k: v for k, v in old.items() if k != "state"} != {k: v for k, v in current.items() if k != "state"}:
                        raise ValueError
                    if old["state"] != "reserved" and old["state"] != current["state"]:
                        raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            raise JournalCorrupt("invalid recovery state or nonmonotonic history") from None

    @staticmethod
    def _check_timing(state: dict[str, Any]) -> None:
        timing = state["timing"]
        if set(timing) != {"claimSeconds", "loopSeconds", "claimStartedAt", "loopStartedAt"}:
            raise ValueError("invalid phase timing")
        for key in ("claimSeconds", "loopSeconds"):
            if type(timing[key]) is not int or not 1 <= timing[key] <= 600:
                raise ValueError("invalid phase budget")
        claim = timing["claimStartedAt"]
        loop = timing["loopStartedAt"]
        if claim is None:
            if loop is not None or state["attempts"] or state["deadline"] is not None:
                raise ValueError("unstarted task has phase state")
            return
        claim_time = _utc(datetime.fromisoformat(claim))
        if not state["attempts"] or state["attempts"][0]["startedAt"] != claim:
            raise ValueError("claim clock must start with the first attempt")
        start, seconds = claim_time, timing["claimSeconds"]
        if loop is not None:
            start = _utc(datetime.fromisoformat(loop))
            if start < claim_time or start >= claim_time + timedelta(seconds=seconds):
                raise ValueError("loop started outside original claim window")
            seconds = timing["loopSeconds"]
        if state["deadline"] != (start + timedelta(seconds=seconds)).isoformat():
            raise ValueError("deadline differs from persisted phase clock")

    def _save(self, state: dict[str, Any]) -> dict[str, Any]:
        self._check_projection(state)
        event = self._journal.append("task.state", state)
        self._tasks[state["taskId"]] = event["data"]
        self._created_at.setdefault(state["taskId"], event["timestamp"])
        return copy.deepcopy(event["data"])

    def get(self, task_id: str) -> dict[str, Any]:
        with self._mutex:
            if task_id not in self._tasks:
                raise LookupError("task not found")
            return copy.deepcopy(self._tasks[task_id])

    def records(self) -> list[dict[str, Any]]:
        """启动对账包括超时/取消记录，不能只扫描仍可发起模型调用的候选。"""
        with self._mutex:
            return copy.deepcopy(list(self._tasks.values()))

    def created_at(self, task_id: str) -> str:
        with self._mutex:
            return self._created_at[task_id]

    def _check_trace(self, data: dict[str, Any]) -> None:
        try:
            trace = AgentTrace.model_validate(data)
            state = self._tasks[trace.task_id]
            if not any(a["attemptId"] == trace.attempt_id for a in state["attempts"]):
                raise ValueError("trace has no attempt")
            if trace.workspace_id != state["workspaceId"]:
                raise ValueError("trace has another workspace")
        except (ValueError, KeyError, TypeError):
            raise JournalCorrupt("invalid attempt trace") from None

    def record_trace(self, trace: AgentTrace) -> None:
        with self._mutex:
            data = trace.model_dump(mode="json", by_alias=True)
            self._check_trace(data)
            previous = self._traces.get(trace.attempt_id)
            if previous is not None:
                if previous != data:
                    raise RecoveryConflict("attempt trace cannot be overwritten")
                return
            event = self._journal.append("attempt.trace", data)
            self._traces[trace.attempt_id] = event["data"]

    def traces(self, task_id: str) -> list[dict[str, Any]]:
        with self._mutex:
            state = self.get(task_id)
            return [copy.deepcopy(self._traces[a["attemptId"]]) for a in state["attempts"]
                    if a["attemptId"] in self._traces]

    def create(
        self, *, task_id: str, session_id: str, branch_id: str,
        workspace_id: str, alert: dict[str, Any], deadline: datetime | None = None,
        model_limit: int = 6, tool_limit: int = 8, max_recoveries: int = 2,
        claim_seconds: int = 190, loop_seconds: int = 120,
    ) -> dict[str, Any]:
        with self._mutex:
            for value in (task_id, session_id, branch_id, workspace_id):
                _identity(value)
            if task_id in self._tasks:
                raise RecoveryConflict("task already registered")
            if any(t["workspaceId"] == workspace_id and t["status"] not in TERMINAL
                   for t in self._tasks.values()):
                raise RecoveryConflict("workspace already belongs to an unfinished task")
            # Whitelist alert fields and redact strings; no HTTP headers or runtime
            # credentials are accepted by the control ledger schema.
            safe_alert: dict[str, Any] = {}
            for key in ("status", "startsAt", "fingerprint"):
                if key in alert and alert[key] is not None:
                    safe_alert[key] = redact_text(str(alert[key]), 256)[0]
            for key in ("labels", "annotations"):
                values = alert.get(key, {})
                if not isinstance(values, dict) or len(values) > 64:
                    raise ValueError("invalid alert fields")
                safe_alert[key] = {redact_text(str(k), 128)[0]: redact_text(str(v), 2048)[0]
                                   for k, v in values.items()}
            state = {
                "schemaVersion": 1 if deadline is not None else 2,
                "taskId": task_id, "sessionId": session_id,
                "branchId": branch_id, "workspaceId": workspace_id,
                "alert": safe_alert, "status": "queued", "cancelRequested": False,
                "deadline": _utc(deadline).isoformat() if deadline is not None else None,
                "limits": {"model": model_limit, "tool": tool_limit, "router": 1},
                "maxRecoveries": max_recoveries, "attempts": [], "calls": {},
                "commands": [], "provider": None, "completeNodeId": None,
            }
            if deadline is None:
                state["timing"] = {
                    "claimSeconds": claim_seconds, "loopSeconds": loop_seconds,
                    "claimStartedAt": None, "loopStartedAt": None,
                }
            return self._save(state)

    def _eligible(self, state: dict[str, Any], now: datetime) -> None:
        if state["cancelRequested"] or state["status"] in TERMINAL:
            raise RecoveryConflict("task is cancelled or terminal")
        now = _utc(now)
        if state["attempts"] and now < datetime.fromisoformat(state["attempts"][-1]["startedAt"]):
            raise RecoveryConflict("wall clock precedes the persisted attempt start: observed=" + now.isoformat()
                                   + ", started=" + state["attempts"][-1]["startedAt"])
        if state["deadline"] is not None and now >= datetime.fromisoformat(state["deadline"]):
            raise RecoveryLimit("original task deadline has expired")

    def candidates(self, now: datetime) -> list[dict[str, Any]]:
        with self._mutex:
            candidates = []
            for task_id in self._tasks:
                state = self.get(task_id)
                try:
                    self._eligible(state, now)
                except RecoveryConflict:
                    continue
                if len(state["attempts"]) <= state["maxRecoveries"]:
                    candidates.append(state)
            return candidates

    def begin_attempt(self, task_id: str, now: datetime) -> dict[str, Any]:
        with self._mutex:
            state = self.get(task_id)
            self._eligible(state, now)
            attempts = state["attempts"]
            if attempts and not attempts[-1]["ownerStopped"]:
                raise RecoveryConflict("previous owner has not been confirmed stopped")
            if len(attempts) > state["maxRecoveries"]:
                raise RecoveryLimit("automatic recovery limit reached")
            attempts.append({
                "attemptId": new_id("attempt"), "startedAt": _utc(now).isoformat(),
                "ownerStopped": False, "sandboxId": None, "podUid": None,
            })
            if state["schemaVersion"] == 2:
                attempts[-1]["claimRequested"] = False
            if state["schemaVersion"] == 2 and state["timing"]["claimStartedAt"] is None:
                state["timing"]["claimStartedAt"] = _utc(now).isoformat()
                state["deadline"] = (_utc(now) + timedelta(seconds=state["timing"]["claimSeconds"])).isoformat()
            state["status"] = "running"
            return self._save(state)

    def begin_loop(self, task_id: str, attempt_id: str, now: datetime) -> dict[str, Any]:
        """首次认领成功后开启独立 loop 时钟；恢复只能复用这个截止时间。"""
        with self._mutex:
            state = self.get(task_id)
            self._eligible(state, now)
            attempt = self._active(state, attempt_id)
            if state["schemaVersion"] != 2:
                # 旧账本只有硬截止时间，保守继承，不为迁移补发 120 秒。
                return state
            if attempt["sandboxId"] is None:
                raise RecoveryConflict("loop requires a bound attempt")
            if state["timing"]["loopStartedAt"] is None:
                state["timing"]["loopStartedAt"] = _utc(now).isoformat()
                state["deadline"] = (_utc(now) + timedelta(seconds=state["timing"]["loopSeconds"])).isoformat()
                return self._save(state)
            return state

    def remaining_seconds(self, task_id: str, now: datetime) -> float:
        """给 wait_for 的剩余额度。排队未启动时没有计时窗口。"""
        with self._mutex:
            state = self.get(task_id)
            self._eligible(state, now)
            if state["deadline"] is None:
                raise RecoveryConflict("task has not started its claim clock")
            return (datetime.fromisoformat(state["deadline"]) - _utc(now)).total_seconds()

    def bind(self, task_id: str, attempt_id: str, sandbox_id: str, pod_uid: str) -> dict[str, Any]:
        with self._mutex:
            state = self.get(task_id)
            attempt = self._active(state, attempt_id)
            if state["cancelRequested"]:
                raise RecoveryConflict("task cancellation takes precedence")
            if attempt["sandboxId"] is not None:
                raise RecoveryConflict("attempt already bound")
            attempt["sandboxId"], attempt["podUid"] = _sandbox_id(sandbox_id), _resource_uid(pod_uid)
            return self._save(state)

    def claim_requested(self, task_id: str, attempt_id: str) -> dict[str, Any]:
        """先记录可能发送认领，再发 HTTP；没有响应不代表 Go 没有收到。"""
        with self._mutex:
            state = self.get(task_id)
            attempt = self._active(state, attempt_id)
            if state["cancelRequested"]:
                raise RecoveryConflict("task cancellation takes precedence")
            attempt["claimRequested"] = True
            return self._save(state)

    def observed_owner(self, task_id: str, attempt_id: str, sandbox_id: str, pod_uid: str) -> dict[str, Any]:
        """仅供可信清理核对 Go 元数据；取消后仍记录实际资源，不授权继续执行。"""
        with self._mutex:
            state = self.get(task_id)
            attempt = self._active(state, attempt_id)
            sandbox_id, pod_uid = _sandbox_id(sandbox_id), _resource_uid(pod_uid)
            if attempt["sandboxId"] is not None:
                if (attempt["sandboxId"], attempt["podUid"]) != (sandbox_id, pod_uid):
                    raise RecoveryConflict("observed owner differs from the saved binding")
                return state
            attempt["sandboxId"], attempt["podUid"] = sandbox_id, pod_uid
            return self._save(state)

    @staticmethod
    def _active(state: dict[str, Any], attempt_id: str) -> dict[str, Any]:
        if state["status"] in TERMINAL or not state["attempts"]:
            raise RecoveryConflict("no active attempt")
        attempt = state["attempts"][-1]
        if attempt["attemptId"] != attempt_id or attempt["ownerStopped"]:
            raise RecoveryConflict("stale attempt")
        return attempt

    def owner_stopped(self, task_id: str, attempt_id: str, reason: str) -> dict[str, Any]:
        """Trusted Go reconciliation must establish this fact before recording it.

        Pod object deletion or lease expiry alone are not an accepted reason.
        """
        if reason not in {"containers-terminated", "never-bound"}:
            raise ValueError("unsupported owner stop evidence")
        with self._mutex:
            state = self.get(task_id)
            attempt = self._active(state, attempt_id)
            if reason == "never-bound" and attempt["sandboxId"] is not None:
                raise RecoveryConflict("bound owner requires termination evidence")
            attempt["ownerStopped"] = True
            attempt["stopReason"] = reason
            for call in state["calls"].values():
                if call["attemptId"] == attempt_id and call["state"] == "reserved":
                    call["state"] = "uncertain"
            state["status"] = "cancelled" if state["cancelRequested"] else "interrupted"
            return self._save(state)

    def reserve(
        self, task_id: str, attempt_id: str, kind: str, now: datetime, *,
        operation: str = "", path: str = "", expected_new_sha256: str | None = None,
    ) -> str:
        with self._mutex:
            state = self.get(task_id)
            self._eligible(state, now)
            attempt = self._active(state, attempt_id)
            if kind not in state["limits"]:
                raise ValueError("invalid call kind")
            if (state["schemaVersion"] == 2 and kind in {"model", "tool"}
                    and (state["timing"]["loopStartedAt"] is None or attempt["sandboxId"] is None)):
                raise RecoveryConflict("model and tool calls require a bound attempt and the original loop clock")
            if sum(c["kind"] == kind for c in state["calls"].values()) >= state["limits"][kind]:
                raise RecoveryLimit("call budget exhausted, including uncertain reservations")
            if len(operation) > 64 or len(path.encode()) > 512:
                raise ValueError("invalid call metadata")
            if expected_new_sha256 is not None and not _HASH.fullmatch(expected_new_sha256):
                raise ValueError("invalid expected write hash")
            call_id = new_id("call")
            state["calls"][call_id] = {
                "attemptId": attempt_id, "kind": kind, "state": "reserved",
                "operation": redact_text(operation, 64)[0],
                "path": redact_text(path, 512)[0],
                "expectedNewSha256": expected_new_sha256,
            }
            self._save(state)
            return call_id

    def finish_call(self, task_id: str, attempt_id: str, call_id: str) -> dict[str, Any]:
        with self._mutex:
            state = self.get(task_id)
            self._active(state, attempt_id)
            call = state["calls"].get(call_id)
            if call is None or call["attemptId"] != attempt_id or call["state"] != "reserved":
                raise RecoveryConflict("stale or completed call")
            call["state"] = "finished"
            return self._save(state)

    def select_provider(
        self, task_id: str, provider: str, model: str, *, fingerprint: str | None = None,
        tier: str | None = None,
    ) -> dict[str, Any]:
        with self._mutex:
            state = self.get(task_id)
            if state["status"] in TERMINAL or state["cancelRequested"]:
                raise RecoveryConflict("task not accepting a provider")
            selected = {"provider": redact_text(provider, 64)[0], "model": redact_text(model, 128)[0]}
            if fingerprint is not None:
                if not _HASH.fullmatch(fingerprint):
                    raise ValueError("invalid provider configuration fingerprint")
                selected["fingerprint"] = fingerprint
            if tier is not None:
                if tier not in {"economy", "strong"}:
                    raise ValueError("invalid provider tier")
                selected["tier"] = tier
            if state["provider"] is not None and state["provider"] != selected:
                raise RecoveryConflict("automatic recovery cannot reroute the task")
            state["provider"] = selected
            return self._save(state)

    def queue_command(self, task_id: str, kind: str, content: str) -> str:
        with self._mutex:
            state = self.get(task_id)
            if state["status"] in TERMINAL or state["cancelRequested"]:
                raise RecoveryConflict("task not accepting commands")
            if kind not in {"steer", "follow-up"} or not content or len(content)>4096:
                raise ValueError("invalid control command")
            if len(state["commands"]) >= 64:
                raise RecoveryLimit("control command limit reached")
            command_id = new_id("command")
            state["commands"].append({"commandId": command_id, "kind": kind,
                                      "content": redact_text(content)[0], "appliedNodeId": None})
            self._save(state)
            return command_id

    def complete_boundary(self, task_id: str, attempt_id: str, node_id: str, command_ids: list[str]) -> dict[str, Any]:
        """Call only after Session fsync. Session must also store command IDs so
        recovery can reconcile a crash between its commit and this ledger write.
        """
        with self._mutex:
            state = self.get(task_id)
            self._active(state, attempt_id)
            _identity(node_id)
            commands = {c["commandId"]: c for c in state["commands"]}
            if len(set(command_ids)) != len(command_ids) or any(cid not in commands for cid in command_ids):
                raise RecoveryConflict("unknown or duplicate control command")
            for cid in command_ids:
                if commands[cid]["appliedNodeId"] not in {None, node_id}:
                    raise RecoveryConflict("command already consumed at another boundary")
                commands[cid]["appliedNodeId"] = node_id
            state["completeNodeId"] = node_id
            return self._save(state)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._mutex:
            state = self.get(task_id)
            if state["status"] in TERMINAL:
                return state
            state["cancelRequested"] = True
            active = state["attempts"] and not state["attempts"][-1]["ownerStopped"]
            state["status"] = "cancelling" if active else "cancelled"
            return self._save(state)

    def finish(self, task_id: str, status: str) -> dict[str, Any]:
        if status not in TERMINAL:
            raise ValueError("expected terminal status")
        with self._mutex:
            state = self.get(task_id)
            if state["status"] in TERMINAL:
                if state["status"] != status:
                    raise RecoveryConflict("terminal task cannot be revived or changed")
                return state
            if state["cancelRequested"]:
                status = "cancelled"
            if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                raise RecoveryConflict("terminal commit requires resource reconciliation")
            state["status"] = status
            return self._save(state)

    def close(self) -> None:
        self._journal.close()

    def __enter__(self) -> RecoveryLedger:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
