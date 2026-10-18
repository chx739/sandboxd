"""本轮实际GC证据独立核验；固定新仓库只读清单/正常read-data检查，不执行删除。"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def run_command(args, *, env=None):
    result = subprocess.run(args, env=env, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError("fixed audit command failed, exit=" + str(result.returncode))
    return result.stdout


def journal(path: Path, *, go=False):
    rows = []
    for line in path.read_bytes().splitlines():
        row = json.loads(line)
        if go:
            offset = line.rfind(b',"sha256":')
            assert offset > 0
            raw = line[:offset] + b"}"
        else:
            raw = json.dumps({k: v for k, v in row.items() if k != "sha256"}, ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert row["seq"] == len(rows) + 1 and hashlib.sha256(raw).hexdigest() == row["sha256"]
        rows.append(row)
    return rows


def stamp(value: str):
    # 系统Python与项目venv版本不同；显式兼容Go RFC3339Nano的Z和9位小数。
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00"))


def audit(source: Path, output: Path):
    repo = Path(__file__).resolve().parents[1]
    report = json.loads(source.read_text())
    assert report["passed"] and report["mode"] == "actual-owned-agent-checkpoints-operator-gc-joint"
    root = Path(report["controlRoot"])
    assert root.is_absolute() and root == root.resolve() and root.parent == Path.home() / ".local/share/sandboxd/workspace-gc-joint"
    assert re.fullmatch(r"[a-f0-9]{32}", root.name) and not output.exists()
    owner = report["owner"]; cps = report["checkpoints"]
    assert len(cps) == 5 and [cp["revision"] for cp in cps] == [1, 2, 3, 4, 5]
    assert set(report["forgottenCheckpointIds"]) == {cps[0]["checkpointId"], cps[2]["checkpointId"]}
    py = journal(root / "agent-control/checkpoints/checkpoints.jsonl")
    go = journal(root / "go-control/workspaces.jsonl", go=True)
    tasks = journal(root / "agent-control/tasks.jsonl")
    task = tasks[-1]["data"]
    assert task["taskId"] == report["taskId"] and task["status"] == "succeeded"
    assert sum(v["kind"] == "model" for v in task["calls"].values()) == report["modelReservations"] == 5
    assert sum(v["kind"] == "tool" for v in task["calls"].values()) == report["toolReservations"] == 4
    for path, expected in report["originalHashes"].items():
        assert path in {"agent-control/tasks.jsonl", "agent-control/sessions/" + task["sessionId"] + ".jsonl"}
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == expected
    for cp in cps:
        ref = cp["identity"]["sessionRef"]
        assert ref["path"] == "session-refs/" + ref["sha256"] + ".json" and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])
        raw = (root / "agent-control/checkpoints" / ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"] and len(raw) == ref["bytes"]
        prefix = json.loads(raw); assert prefix["nodeId"] == cp["identity"]["sessionNodeId"]
        parent = None
        for node in prefix["nodes"]:
            assert node["parentId"] == parent; parent = node["nodeId"]
        assert parent == prefix["nodeId"]
    assert next(r["data"] for r in go if r["type"] == "repository.binding")["owner"] == owner
    binding = next(r["data"] for r in py if r["type"] == "repository.binding")
    catalogue = json.loads((Path.home() / ".local/share/sandboxd/repository-controllers" / (owner["repositoryCryptoId"] + ".json")).read_text())
    assert catalogue == binding and binding["owner"] == owner
    assert binding["directory"] == str(root / "agent-control/checkpoints")
    assert binding["device"] == (root / "agent-control/checkpoints").stat().st_dev and binding["inode"] == (root / "agent-control/checkpoints").stat().st_ino
    forgets = [r for r in py if r["type"] == "checkpoint.forget"]
    assert len(forgets) == 4
    gc_ids = sorted({r["data"]["gcId"] for r in forgets})
    protocol = []
    for gc_id in gc_ids:
        events = [r for r in forgets if r["data"]["gcId"] == gc_id]
        assert [r["data"]["status"] for r in events] == ["PREPARED", "FORGOTTEN"]
        states = [r for r in go if r["type"] == "workspace.barrier" and r["data"].get("cleanup", {}).get("gcId") == gc_id]
        assert states[-1]["data"]["phase"] == "released" and states[-1]["data"]["cleanupResult"]["owner"] == owner
        prepared, forgotten = [stamp(r["timestamp"]) for r in events]
        submitted = next(stamp(r["timestamp"]) for r in states if r["data"]["phase"] == "submitted")
        proof = next(stamp(r["timestamp"]) for r in states if r["data"]["phase"] == "stopped" and r["data"].get("cleanupResult"))
        released = stamp(states[-1]["timestamp"])
        assert prepared <= submitted <= proof <= forgotten <= released
        executions = [r["data"] for r in py if r["type"] == "cleanup.execution" and r["data"]["gcId"] == gc_id]
        assert executions[0]["status"] == "PENDING" and executions[-1]["status"] == "DONE"
        protocol.append({"gcId": gc_id, "attempts": executions[-1]["attempt"], "jobIds": sorted({r["data"]["jobId"] for r in states}),
                         "snapshotId": events[0]["data"]["snapshotId"], "preparedBeforeSubmitAndForgottenBeforeRelease": True})
    cut = next(c for c in report["cases"] if c["case"] == "actual-admin-and-go-sigkill-after-verified-wait")
    assert cut["adminExitCode"] == cut["goExitCode"] == -9
    assert len(next(p for p in protocol if p["gcId"] == cut["gcId"])["jobIds"]) == 1
    assert {r["barrier"]["podUid"] for r in report["operatorCalls"] if r["gcId"] == cut["gcId"] and "barrier" in r} == {cut["samePodUid"]}
    lock = next(c for c in report["cases"] if c["case"] == "actual-exclusive-lock-failure-fixed-target-retry")
    assert lock["forceUnlockCalls"] == 0 and lock["blockedBackupStatus"] == 409 and lock["attempt"] == 2
    assert len(next(p for p in protocol if p["gcId"] == lock["gcId"])["jobIds"]) == 2
    lock_rows = [r for r in report["operatorCalls"] if r["gcId"] == lock["gcId"] and r["action"] == "wait"]
    assert [r["statusCode"] for r in lock_rows] == [503, 200] and [r["exitCode"] for r in lock_rows] == [1, 0]

    kube = ["kubectl", "--context", "kind-sandboxd"]
    assert re.fullmatch(r"restic-[a-f0-9]{16}", report["secretName"])
    secret = json.loads(run_command([*kube, "-n", "sandboxd-storage", "get", "secret", report["secretName"], "-o", "json"]))
    assert secret["metadata"]["labels"]["sandbox.io/managed-by"] == "sandboxd-backup"
    values = {k: base64.b64decode(secret["data"][k]).decode() for k in ("repository", "password", "access-key", "secret-key")}
    server = json.loads(run_command(["docker", "inspect", "sandboxd-workspace-backup-minio-1"]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace-backup"
    endpoint = server["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    assert values["repository"] == "s3:http://" + endpoint + ":9000/sandboxd-workspace/owned-joint/" + root.name
    assert {"HostIp": "127.0.0.1", "HostPort": "9002"} in server["NetworkSettings"]["Ports"]["9000/tcp"]
    binary = repo / ".cache/restic-image-v3/restic"
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == "01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6"
    env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "AWS_ACCESS_KEY_ID": values["access-key"],
           "AWS_SECRET_ACCESS_KEY": values["secret-key"], "AWS_DEFAULT_REGION": "us-east-1",
           "RESTIC_PASSWORD": values["password"], "RESTIC_REPOSITORY": "s3:http://127.0.0.1:9002/sandboxd-workspace/owned-joint/" + root.name}
    command = [str(binary), "--no-cache", "-o", "s3.connections=2", "-o", "s3.bucket-lookup=path"]
    assert json.loads(run_command([*command, "cat", "config"], env=env))["id"] == owner["repositoryCryptoId"]
    snapshots = json.loads(run_command([*command, "snapshots", "--json"], env=env))
    expected = {owner["ownerSnapshotId"], *(cps[i]["data"]["snapshotId"] for i in (1, 3, 4))}
    assert {s["id"] for s in snapshots} == expected and len(snapshots) == 4
    for snapshot in snapshots:
        assert [t for t in snapshot["tags"] if t.startswith("controller:")] == ["controller:" + owner["controllerId"]]
    marker = json.loads(run_command([*command, "dump", owner["ownerSnapshotId"], "/tmp/sandboxd-owner.json"], env=env))
    assert marker == {"schemaVersion": 1, "controllerId": owner["controllerId"]}
    assert not run_command([*command, "list", "locks"], env=env).strip()
    integrity = run_command([*command, "check", "--read-data"], env=env)
    assert not run_command([*command, "list", "locks"], env=env).strip()
    data = {"schemaVersion": 1, "passed": True, "mode": "independent-owned-joint-evidence-and-live-inventory-audit",
            "sourceReport": str(source), "sourceReportSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "fixtureSha256": hashlib.sha256((repo / "agentd/workspace_gc_joint_demo.py").read_bytes()).hexdigest(),
            "auditSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "journalRecords": {"pythonCheckpoints": len(py), "goControl": len(go), "tasks": len(tasks)},
            "protocol": protocol, "nativeRepositoryCryptoIdMatches": True, "freshDedicatedPrefixVerified": True,
            "actualRetainedSnapshotIds": sorted(expected), "readDataCheck": {"exitCode": 0, "outputBytes": len(integrity), "outputSha256": hashlib.sha256(integrity).hexdigest()},
            "destructiveCalls": 0, "externalModelCalls": 0, "scope": "Audit of the same actual joint experiment plus native inventory/read-data check; not a second recovery experiment or performance benchmark."}
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print("joint journals, protocol order, dedicated repository inventory and read-data check passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); audit(args.source, args.output)
