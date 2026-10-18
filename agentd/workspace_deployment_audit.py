"""统一owned V2入口的独立只读审计；不启动任务、不执行forget/prune。"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess

from workspace_gc_joint_audit import journal


def call(args, *, env=None):
    result = subprocess.run(args, env=env, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("fixed read-only audit command failed")
    return result.stdout


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(source, output):
    assert not output.exists()
    report = json.loads(source.read_text())
    assert report["passed"] and report["mode"] == "real-owned-v2-unified-entry-sigkill-replay" and report["externalModelCalls"] == 0
    assert report["operatorEnvironmentRemoved"] and report["storageCredentialsEnvironmentRemoved"]
    root = Path(report["controlRoot"])
    assert root == root.resolve() and root.parent == Path.home()/".local/share/sandboxd/deployments"
    state_file = json.loads((root/"deployment.json").read_text())
    state = state_file["state"]
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(raw).hexdigest() == state_file["sha256"]
    assert sha(root/"deployment.json") == report["deploymentStateSha256"]
    assert state["phase"] == "READY" and state["owner"] == report["owner"]
    assert state["prefix"].startswith("owned-deploy/") and state["proof"]["podUid"] == state["podUid"]
    assert not any("persistentVolumeClaim" in v for v in state["podTemplate"]["spec"]["volumes"])
    assert state["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"
    go = journal(root/"go-control/workspaces.jsonl", go=True)
    cp = journal(root/"agent-control/checkpoints/checkpoints.jsonl")
    task_rows = journal(root/"agent-control/tasks.jsonl")
    task = task_rows[-1]["data"]
    assert task == report["stateAfterRestart"] and task["status"] == "succeeded"
    for key in ("taskId", "sessionId", "branchId", "workspaceId", "limits", "deadline", "timing", "provider"):
        assert task[key] == report["stateBeforeKill"][key], key
    assert len(task["attempts"]) == 2 and all(a["ownerStopped"] for a in task["attempts"])
    assert sum(c["kind"] == "model" for c in task["calls"].values()) == 4
    assert sum(c.get("operation") == "write_file" for c in task["calls"].values()) == 1
    assert report["oldPod"]["podUid"] != report["newPod"]["podUid"]
    for pod in (report["oldPod"], report["newPod"]):
        assert pod["runtimeClass"] == "gvisor" and pod["nfsMount"]["fstype"] == "nfs4"
    binding = next(r["data"] for r in cp if r["type"] == "repository.binding")
    assert binding["owner"] == next(r["data"] for r in go if r["type"] == "repository.binding")["owner"] == state["owner"]
    catalogue = Path.home()/".local/share/sandboxd/repository-controllers"/(state["owner"]["repositoryCryptoId"]+".json")
    assert json.loads(catalogue.read_text()) == binding
    checkpoint_dir = root/"agent-control/checkpoints"
    assert binding["directory"] == str(checkpoint_dir) and binding["device"] == checkpoint_dir.stat().st_dev and binding["inode"] == checkpoint_dir.stat().st_ino
    committed = [r["data"] for r in cp if r["type"] == "checkpoint.state" and r["data"]["status"] == "COMMITTED"]
    assert committed == report["jointCheckpoints"] and len(committed) >= 3
    assert [r["revision"] for r in committed] == list(range(1, len(committed)+1))
    previous = None
    for record in committed:
        assert record["parentCheckpointId"] == previous
        previous = record["checkpointId"]
        events = [r["data"]["status"] for r in cp if r["type"] == "checkpoint.state" and r["data"]["checkpointId"] == previous]
        assert events == ["PREPARING", "DATA_READY", "COMMITTED"]
        identity = record["identity"]; ref = identity["sessionRef"]
        assert identity["taskId"] == task["taskId"] and identity["workspaceId"] == task["workspaceId"]
        assert record["data"]["repositoryCryptoId"] == state["owner"]["repositoryCryptoId"]
        assert ref["path"] == "session-refs/"+ref["sha256"]+".json"
        path = checkpoint_dir/ref["path"]
        assert sha(path) == ref["sha256"] and path.stat().st_size == ref["bytes"]
        prefix = json.loads(path.read_text())
        parent = None
        for node in prefix["nodes"]:
            assert node["parentId"] == parent
            parent = node["nodeId"]
        assert parent == prefix["nodeId"] == identity["sessionNodeId"]
    expected = hashlib.sha256(b"synthetic unified-entry recovery\n").hexdigest()
    nfs_hash = call(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum",
        "/export/workspaces/"+task["workspaceId"]+"/entry.md"]).split()[0].decode()
    assert nfs_hash == expected == report["serverSha256"]
    # 凭据只用于独立读仓库，不进入公开报告/异常链。
    credentials = json.loads((root/"repository-credentials.json").read_text())
    binary = Path.home()/".local/share/sandboxd-tools/restic-0.18.1/restic"
    assert sha(binary) == "01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6"
    expected_repo = "s3:http://"+state["minioIp"]+":9000/sandboxd-workspace/"+state["prefix"]
    assert credentials["repository"] == expected_repo
    env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "RESTIC_PASSWORD": credentials["password"],
           "AWS_ACCESS_KEY_ID": credentials["access-key"], "AWS_SECRET_ACCESS_KEY": credentials["secret-key"],
           "RESTIC_REPOSITORY": "s3:http://127.0.0.1:9002/sandboxd-workspace/"+state["prefix"], "AWS_DEFAULT_REGION": "us-east-1"}
    command = [str(binary), "--no-cache", "-o", "s3.connections=2", "-o", "s3.bucket-lookup=path"]
    assert json.loads(call(command+["cat", "config"], env=env))["id"] == state["owner"]["repositoryCryptoId"]
    snapshots = json.loads(call(command+["snapshots", "--json"], env=env))
    expected_ids = {state["owner"]["ownerSnapshotId"]} | {r["data"]["snapshotId"] for r in committed}
    assert len(snapshots) == len(expected_ids) and {s["id"] for s in snapshots} == expected_ids
    assert all("controller:"+state["controllerId"] in s["tags"] for s in snapshots)
    call(command+["check", "--read-data"], env=env)
    for ns in ("sandboxd-demo", "sandboxd-storage"):
        assert not json.loads(call(["kubectl", "--context", "kind-sandboxd", "-n", ns, "get", "pods", "-o", "json"]))["items"]
    files = [source, root/"deployment.json", root/"go-control/workspaces.jsonl", root/"agent-control/tasks.jsonl", checkpoint_dir/"checkpoints.jsonl", catalogue]
    for path in (source, root/"deployment.json"):
        contents = path.read_bytes()
        for key in ("password", "access-key", "secret-key"):
            value = credentials[key].encode()
            assert value not in contents and base64.b64encode(value) not in contents
    result = {"passed": True, "sourceSha256": sha(source), "mode": "independent-owned-v2-unified-entry-audit",
              "scope": "Actual init/reuse/Go+Agent startup and same-Task SIGKILL recovery, original budgets and files, owned joint commits and native repository read-data; not bootstrap crash injection or full V2 matrix",
              "journalRecords": {"go": len(go), "checkpoints": len(cp), "tasks": len(task_rows)}, "owner": state["owner"],
              "snapshots": sorted(expected_ids), "resticReadDataCheckPassed": True, "externalModelCalls": 0,
              "files": {str(path): sha(path) for path in files}, "formalV2AcceptanceComplete": False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source, args.output)
    print(json.dumps({"passed": result["passed"], "journalRecords": result["journalRecords"]}))
