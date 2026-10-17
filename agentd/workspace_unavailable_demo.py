"""V1-11：短暂停止项目NFS响应，验证文件RPC有界失败及无停止证明时禁止接管。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.redaction import public_error
from .app.tools.remote_files import WorkspaceBinding
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go

CONTENT = "synthetic uncached NFS observation\n"


def watchdog_argv(container_id: str) -> list[str]:
    # 固定ID、一次性45秒看门狗：父进程若异常退出，仍恢复本次存储服务。
    if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
        raise ValueError("watchdog requires a verified full container ID")
    return [sys.executable, "-c",
            "import subprocess,time; time.sleep(45); subprocess.run(['docker','unpause'," + repr(container_id) + "],check=False)"]


async def run(output: Path, binary: Path, nfs_server: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    for namespace in ("sandboxd-demo", "sandboxd-storage"):
        assert not json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", namespace, "get", "pods", "-o", "json"]))["items"], "run fault experiment serially without other workspace Pods"
    server = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert server["State"]["Running"] and not server["State"]["Paused"]
    assert nfs_server in [n["IPAddress"] for n in server["NetworkSettings"]["Networks"].values()]
    container_id = server["Id"]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    go, log, token = start_go(root, binary, nfs_server)
    sdk = SandboxdClient("http://127.0.0.1:10880", token)
    http = httpx.AsyncClient(base_url="http://127.0.0.1:10880", headers={"Authorization": "Bearer " + token}, timeout=40)
    binding, watchdog, watchdog_log, paused, claim_requested = None, None, None, False, False
    workspace_id = "workspace-" + uuid4().hex
    report = {"mode": "real-nfs-pause-go-gvisor", "scope": "V1-11 local NFS unavailability and unconfirmed old writer rejection",
              "controlRoot": str(root), "workspaceId": workspace_id, "externalModelCalls": 0, "passed": False}
    try:
        await ready(go)
        prepared = await sdk.prepare_workspace(workspace_id, "session-nfs-unavailable", "branch-nfs-unavailable")
        assert prepared.status_code == 201
        claim_requested = True
        claimed = await sdk.claim_workspace(workspace_id, "task-nfs-unavailable", "attempt-nfs-original")
        assert claimed.status_code == 201
        binding = WorkspaceBinding.from_record(claimed.body).as_request()
        report["podBeforeFault"] = observe_pod(claimed.body)
        # 这个名字从未被Pod查询过，避免已缓存guard的读取掩盖NFS故障。
        filename = "unseen-" + uuid4().hex + ".txt"
        server_path = "/export/workspaces/" + workspace_id + "/" + filename
        subprocess.run(["docker", "exec", "-i", "--user", "65532:65532", container_id, "tee", server_path],
                       input=CONTENT, text=True, check=True, capture_output=True, timeout=10)
        watchdog_log = (root / "nfs-watchdog.log").open("w")
        (root / "nfs-watchdog.log").chmod(0o600)
        watchdog = subprocess.Popen(watchdog_argv(container_id), stdout=watchdog_log, stderr=subprocess.STDOUT,
                                    env={k: v for k, v in os.environ.items() if not k.startswith(("AGENTD_", "SANDBOXD_"))})
        checked(["docker", "pause", container_id])
        paused = True
        started = time.perf_counter()
        report["fault"] = {"containerId": container_id, "kind": "docker-pause-own-nfs-only",
                           "startedAt": datetime.now(timezone.utc).isoformat(), "watchdogSeconds": 45,
                           "execTimeoutSeconds": 30, "httpClientTimeoutSeconds": 40}
        print(json.dumps({"stage": "own-nfs-paused", "workspaceId": workspace_id, "watchdogPid": watchdog.pid}), flush=True)
        async with asyncio.timeout(38):
            response = await http.post("/api/v1/workspaces/" + workspace_id + "/files",
                                       json={"binding": binding, "request": {"operation": "read_file", "path": filename}})
        elapsed = (time.perf_counter() - started) * 1000
        body = response.json()
        assert response.status_code in {503, 504} and body.get("sandboxRecovery") is True, (response.status_code, body)
        assert elapsed < 38000
        old = await sdk.get_workspace(workspace_id)
        assert old.status_code == 200 and old.body["phase"] == "bound"
        assert old.body["generation"] == claimed.body["generation"] and not old.body["owner"]["stopConfirmed"]
        pod = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo", "get", "pod",
                                  old.body["owner"]["podName"], "-o", "json"]))
        assert pod["metadata"]["uid"] == old.body["owner"]["podUid"]
        takeover = await sdk.claim_workspace(workspace_id, "task-nfs-unavailable", "attempt-nfs-unconfirmed")
        assert takeover.status_code == 409
        unchanged = await sdk.get_workspace(workspace_id)
        assert unchanged.body == old.body
        during = json.loads(checked(["docker", "inspect", container_id]))[0]
        assert during["State"]["Paused"] and watchdog.poll() is None
        report.update({"fileRpc": {"statusCode": response.status_code, "body": body, "elapsedMs": round(elapsed, 3)},
                       "ownerWithoutStopProof": old.body, "podUidDuringFault": pod["metadata"]["uid"],
                       "podPhaseDuringFault": pod["status"]["phase"],
                       "podDeletionRequestedDuringFault": pod["metadata"].get("deletionTimestamp"),
                       "unconfirmedTakeoverStatus": takeover.status_code, "generationUnchanged": True})
        checked(["docker", "unpause", container_id])
        paused = False
        report["fault"]["pausedMs"] = round((time.perf_counter() - started) * 1000, 3)
        watchdog.terminate()
        watchdog.wait(timeout=5)
        # 既有Exec超时会请求回收Pod；恢复NFS不等于原Pod还能接着执行。
        # 必须先采集旧实例停止证明，再建立同工作区的新绑定。
        stopped = await sdk.release_workspace(binding)
        assert stopped.status_code == 200 and stopped.body["owner"]["stopConfirmed"]
        report["oldReleaseAfterUnpause"] = stopped.body
        replacement = await sdk.claim_workspace(workspace_id, "task-nfs-unavailable", "attempt-nfs-after-stop")
        assert replacement.status_code == 201
        binding = WorkspaceBinding.from_record(replacement.body).as_request()
        assert replacement.body["generation"] == claimed.body["generation"] + 1
        assert replacement.body["owner"]["podUid"] != claimed.body["owner"]["podUid"]
        report["replacementPod"] = observe_pod(replacement.body)
        recovered = await http.post("/api/v1/workspaces/" + workspace_id + "/files",
                                    json={"binding": binding, "request": {"operation": "read_file", "path": filename}})
        expected = hashlib.sha256(CONTENT.encode()).hexdigest()
        assert recovered.status_code == 200 and recovered.json()["sha256"] == expected
        assert checked(["docker", "exec", "--user", "65532:65532", container_id, "sha256sum", server_path]).split()[0] == expected
        report.update({"readAfterUnpauseSha256": expected, "passed": True, "verifiedCases": ["V1-11"]})
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": public_error(exc)}
        raise
    finally:
        # 恢复存储先于Pod清理。不能为了清理快而强删或丢弃NFS数据。
        if paused:
            try:
                checked(["docker", "unpause", container_id])
                paused = False
            except Exception as exc:
                report["unpauseError"] = public_error(exc)
                report["passed"] = False
        if watchdog is not None and watchdog.poll() is None and not paused:
            watchdog.terminate()
            watchdog.wait(timeout=5)
        if watchdog_log is not None:
            watchdog_log.close()
        if claim_requested:
            try:
                # 包括认领返回前中断的情况，按实际Go owner对账自己的固定尝试。
                current = await sdk.get_workspace(workspace_id)
                assert current.status_code == 200
                owner = current.body.get("owner", {})
                assert owner.get("taskId") == "task-nfs-unavailable"
                assert owner.get("attemptId") in {"attempt-nfs-original", "attempt-nfs-after-stop"}
                binding = WorkspaceBinding.from_record(current.body).as_request()
                released = await sdk.release_workspace(binding)
                assert released.status_code == 200 and released.body["owner"]["stopConfirmed"]
                report["release"] = released.body
            except Exception as exc:
                report["cleanupError"] = public_error(exc)
                report["passed"] = False
        await http.aclose()
        await sdk.close()
        stop_go(go, log)
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=REPO / ".cache/workspace-sandboxd")
    parser.add_argument("--nfs-server", required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.output, args.binary.resolve(), args.nfs_server))
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
