"""V1-10：直接请求真实Go文件接口，绕过Python Policy核验归属与文件边界。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.redaction import public_error
from .app.tools.remote_files import WorkspaceBinding
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go


def invalid_operations() -> list[tuple[str, dict]]:
    return [
        ("parent-escape", {"operation": "write_file", "path": "../escape.md", "content": "rejected"}),
        ("absolute-path", {"operation": "read_file", "path": "/etc/passwd"}),
        ("reserved-lock", {"operation": "read_file", "path": ".sandboxd-lock"}),
        ("path-depth", {"operation": "read_file", "path": "/".join(["a"] * 9)}),
        ("oversize-text", {"operation": "write_file", "path": "oversize.txt", "content": "x" * ((256 << 10) + 1)}),
        ("arbitrary-operation", {"operation": "shell", "path": "guard.md"}),
        ("extra-argv", {"operation": "read_file", "path": "guard.md", "argv": ["sh"]}),
        ("missing-content", {"operation": "write_file", "path": "guard.md"}),
        ("null-content", {"operation": "write_file", "path": "guard.md", "content": None}),
    ]


async def run(output: Path, binary: Path, nfs_server: str) -> dict:
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    nfs = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert nfs_server in [n["IPAddress"] for n in nfs["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    go, log, token = start_go(root, binary, nfs_server)
    sdk = SandboxdClient("http://127.0.0.1:10880", token)
    raw = httpx.AsyncClient(base_url="http://127.0.0.1:10880", headers={"Authorization": "Bearer " + token}, timeout=10)
    bindings, checks, observations, claims = [], [], [], []
    report = {"mode": "real-go-gvisor-nfs-boundary", "controlRoot": str(root), "externalModelCalls": 0,
              "pythonPolicyUsed": False, "checks": checks, "pods": observations, "passed": False}
    try:
        await ready(go)
        for name in ("a", "b"):
            workspace = "workspace-" + uuid4().hex
            prepared = await sdk.prepare_workspace(workspace, "session-boundary-" + name, "branch-boundary-" + name)
            assert prepared.status_code == 201
            task_id, attempt_id = "task-boundary-" + name, "attempt-" + uuid4().hex
            claims.append((workspace, task_id, attempt_id))
            claimed = await sdk.claim_workspace(workspace, task_id, attempt_id)
            assert claimed.status_code == 201
            binding = WorkspaceBinding.from_record(claimed.body).as_request()
            bindings.append(binding)
            observations.append(observe_pod(claimed.body))
            content = "synthetic guard " + name + "\n"
            written = await sdk.workspace_files(binding, {"operation": "write_file", "path": "guard.md", "content": content})
            assert written.status_code == 200 and written.body["newSha256"] == hashlib.sha256(content.encode()).hexdigest()
        a, b = bindings
        guard = "synthetic guard a\n"
        valid_cas = await sdk.workspace_files(a, {"operation": "write_file", "path": "guard.md", "content": guard,
                                                 "expectedSha256": hashlib.sha256(guard.encode()).hexdigest()})
        assert valid_cas.status_code == 200
        report["validCasControl"] = valid_cas.body

        async def denied(name, binding, operation, expected, layer=None, url_workspace=None):
            response = await raw.post("/api/v1/workspaces/" + (url_workspace or binding["workspaceId"]) + "/files",
                                      json={"binding": binding, "request": operation})
            body = response.json()
            assert response.status_code == expected, (name, response.status_code, body)
            if layer is not None:
                assert body.get("denyLayer") == layer, (name, body)
            checks.append({"name": name, "statusCode": response.status_code, "body": body})

        for name, operation in invalid_operations():
            await denied(name, a, operation, 400, "workspace-policy")
        for key, value in (("taskId", b["taskId"]), ("attemptId", b["attemptId"]),
                           ("sandboxId", b["sandboxId"]), ("generation", a["generation"] + 1),
                           ("workspaceId", b["workspaceId"])):
            await denied("wrong-" + key, dict(a, **{key: value}), {"operation": "read_file", "path": "guard.md"},
                         409, "workspace-ownership")
        await denied("url-binding-mismatch", a, {"operation": "read_file", "path": "guard.md"}, 400,
                     "workspace-policy", url_workspace=b["workspaceId"])
        # 在放入symlink前检查CAS，避免目录扫描的链接拒绝遮蔽了真正的CAS检查。
        for name, operation in (
            ("stale-cas-write", {"operation": "write_file", "path": "guard.md", "content": "rejected", "expectedSha256": "0" * 64}),
            ("stale-cas-edit", {"operation": "edit_file", "path": "guard.md", "oldText": "synthetic", "newText": "rejected", "expectedSha256": "0" * 64}),
        ):
            await denied(name, a, operation, 403)
        # 仅管理员夹具在自己的合成目录放入链接。业务工具没有创建symlink能力。
        base = "/export/workspaces/" + a["workspaceId"]
        checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ln", "-s", "guard.md", base + "/link.md"])
        checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ln", "-s", ".", base + "/dirlink"])
        for name, operation in (
            ("read-symlink", {"operation": "read_file", "path": "link.md"}),
            ("write-symlink", {"operation": "write_file", "path": "link.md", "content": "rejected"}),
            ("parent-symlink", {"operation": "read_file", "path": "dirlink/guard.md"}),
        ):
            await denied(name, a, operation, 403)
        # 所有拒绝后核对真实文件和服务端目录，不能只凭HTTP状态判断无副作用。
        hashes = []
        for name, binding in zip(("a", "b"), bindings):
            result = await sdk.workspace_files(binding, {"operation": "read_file", "path": "guard.md"})
            expected = hashlib.sha256(("synthetic guard " + name + "\n").encode()).hexdigest()
            assert result.status_code == 200 and result.body["sha256"] == expected
            server_path = "/export/workspaces/" + binding["workspaceId"]
            assert checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum", server_path + "/guard.md"]).split()[0] == expected
            names = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ls", "-A", server_path]).splitlines()
            assert set(names) == ({".sandboxd-lock", "guard.md", "link.md", "dirlink"} if name == "a" else {".sandboxd-lock", "guard.md"})
            hashes.append({"workspaceId": binding["workspaceId"], "guardSha256": expected, "directoryEntries": names})
        report.update({"filesAfterDenials": hashes, "denialCount": len(checks), "verifiedCases": ["V1-10"]})
        report["linksAfterDenials"] = {
            name: checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "readlink", base + "/" + name]).strip()
            for name in ("link.md", "dirlink")
        }
        assert report["linksAfterDenials"] == {"link.md": "guard.md", "dirlink": "."}
        report["passed"] = True
    except BaseException as exc:
        report["failure"] = {"type": type(exc).__name__, "message": public_error(exc)}
        raise
    finally:
        report["releases"] = []
        for workspace, task_id, attempt_id in reversed(claims):
            try:
                # 认领失败也可能已经创建Pod；按Go实际owner对账，不能只清理收到201的对象。
                current = await sdk.get_workspace(workspace)
                assert current.status_code == 200
                owner = current.body.get("owner") or {}
                if current.body["phase"] == "ready" and not owner.get("sandboxId"):
                    continue
                assert owner.get("taskId") == task_id and owner.get("attemptId") == attempt_id
                binding = WorkspaceBinding.from_record(current.body).as_request()
                released = await sdk.release_workspace(binding)
                assert released.status_code == 200 and released.body["owner"]["stopConfirmed"]
                report["releases"].append(released.body)
            except Exception as exc:
                report["cleanupError"] = public_error(exc)
                report["passed"] = False
        await raw.aclose()
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
