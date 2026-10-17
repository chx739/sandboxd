#!/usr/bin/env python3
"""Real local-service component probe; does not claim Agent auto-recovery.

Prerequisite: workspace-fixture.go's dedicated synthetic Pod/PV/PVC and the
project NFS container are running. No model, generic shell or paid API is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

KUBECTL = ["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo"]
POD = "sandbox-workspace-probe"
WORKSPACE = "workspace-00000000000000000000000000000001"
NFS = "sandboxd-workspace-nfs-1"
CONTENT = "synthetic NFS gVisor component probe\n"
FILE = "probe/component-report.md"


def run(argv: list[str], data: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, input=data, capture_output=True, text=True, timeout=35)


def checked(argv: list[str]) -> str:
    result = run(argv)
    if result.returncode:
        raise RuntimeError(result.stderr[:512])
    return result.stdout


def helper(request: dict) -> tuple[int, dict, float]:
    started = time.perf_counter()
    result = run([*KUBECTL, "exec", "-i", POD, "--", "/usr/local/bin/sandbox-filetool"], json.dumps(request))
    return result.returncode, json.loads(result.stdout), (time.perf_counter()-started)*1000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pod = json.loads(checked([*KUBECTL, "get", "pod", POD, "-o", "json"]))
    assert pod["metadata"]["labels"]["sandbox.io/workspace"] == WORKSPACE
    assert pod["metadata"]["labels"]["sandbox.io/task"] == "task-workspace-probe"
    assert pod["spec"]["runtimeClassName"] == "gvisor"
    assert pod["spec"]["securityContext"]["runAsUser"] == 65532
    assert re.fullmatch(r"[a-f0-9-]{36}", pod["metadata"]["uid"])
    identity = checked([*KUBECTL, "exec", POD, "--", "id"])
    assert "uid=65532" in identity
    dmesg = checked([*KUBECTL, "exec", POD, "--", "dmesg"])
    assert "gVisor" in dmesg

    observations = []
    code, old, elapsed = helper({"operation": "read_file", "path": FILE})
    request = {"operation": "write_file", "path": FILE, "content": CONTENT}
    if code == 0:
        # Re-run only over the exact known synthetic fixture, never overwrite an
        # unexpectedly modified file and hide evidence of an earlier failure.
        assert old["content"] == CONTENT
        request["expectedSha256"] = old["sha256"]
    code, written, elapsed = helper(request)
    assert code == 0, written
    expected = hashlib.sha256(CONTENT.encode()).hexdigest()
    assert written["newSha256"] == expected
    observations.append({"operation": "write_file", "elapsedMs": elapsed, "result": written})
    code, read, elapsed = helper({"operation": "read_file", "path": FILE})
    assert code == 0 and read["content"] == CONTENT and read["sha256"] == expected
    observations.append({"operation": "read_file", "elapsedMs": elapsed, "result": read})
    for request in [
        {"operation": "write_file", "path": "../escape", "content": "denied"},
        {"operation": "write_file", "path": FILE, "content": "denied", "expectedSha256": "0"*64},
    ]:
        code, result, elapsed = helper(request)
        assert code != 0 and result["denyLayer"] == "workspace-policy"
        observations.append({"operation": request["operation"], "negativeCase": request["path"], "elapsedMs": elapsed, "result": result})

    server = json.loads(checked(["docker", "inspect", NFS]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert not server["HostConfig"]["Privileged"]
    assert "CAP_DAC_READ_SEARCH" in server["HostConfig"]["CapAdd"] or "DAC_READ_SEARCH" in server["HostConfig"]["CapAdd"]
    server_hash = checked(["docker", "exec", "--user", "65532:65532", NFS, "sha256sum", "/export/workspaces/"+WORKSPACE+"/"+FILE]).split()[0]
    assert server_hash == expected
    node = pod["spec"]["nodeName"]
    assert node == "sandboxd-control-plane"
    mount_path = "/var/lib/kubelet/pods/"+pod["metadata"]["uid"]+"/volumes/kubernetes.io~nfs/sandboxd-"+WORKSPACE
    mount = checked(["docker", "exec", node, "findmnt", "--json", "--target", mount_path, "--output", "SOURCE,FSTYPE,OPTIONS"])
    filesystem = json.loads(mount)["filesystems"][0]
    assert filesystem["fstype"] == "nfs4" and "vers=4.1" in filesystem["options"]
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "real-local-service-component", "externalModelCalls": 0,
        "scope": "gVisor + dedicated NFSv4.1 PVC + fixed file helper; not Agent RPC or automatic recovery",
        "workspaceId": WORKSPACE, "podUid": pod["metadata"]["uid"],
        "runtimeClass": "gvisor", "identity": identity.strip(),
        "gvisorStartup": next(line for line in dmesg.splitlines() if "gVisor" in line),
        "imageRef": pod["spec"]["containers"][0]["image"],
        "imageId": pod["status"]["containerStatuses"][0]["imageID"],
        "nfsImageId": server["Image"], "nfsMount": filesystem,
        "nfsConfigSha256": hashlib.sha256((Path(__file__).resolve().parents[1] / "deploy/workspace/ganesha.conf").read_bytes()).hexdigest(),
        "serverFileSha256": server_hash, "observations": observations,
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"passed": True, "output": str(args.output), "mode": report["mode"]}))


if __name__ == "__main__":
    main()
