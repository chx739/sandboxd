"""独立核对本轮拒绝/幂等原始证据；不运行任务或删除数据。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from workspace_gc_joint_audit import journal


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call(args, env=None):
    result = subprocess.run(args, env=env, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("fixed acceptance audit command failed")
    return result.stdout


def prefix_matches(path, expected):
    running = hashlib.sha256()
    for index, line in enumerate(path.read_bytes().splitlines(keepends=True), 1):
        running.update(line)
        if running.hexdigest() == expected:
            return index
    raise AssertionError("recorded intermediate journal prefix missing")


def audit(source, output):
    assert not output.exists()
    report = json.loads(source.read_text())
    assert report["passed"] and report["mode"] == "actual-v2-save-idempotence-and-restore-refusals" and report["externalModelCalls"] == 0
    root = Path(report["controlRoot"])
    assert root.parent == Path.home()/".local/share/sandboxd/deployments" and root == root.resolve() and root.name.startswith("acceptance-")
    state = json.loads((root/"deployment.json").read_text())["state"]
    assert state["owner"] == report["owner"] and state["phase"] == "READY" and state["prefix"].startswith("owned-deploy/")
    go = journal(root/"go-control/workspaces.jsonl", go=True)
    cp = journal(root/"agent-control/checkpoints/checkpoints.jsonl")
    tasks = journal(root/"agent-control/tasks.jsonl")
    task = tasks[-1]["data"]
    assert task == report["sourceTask"] and task["status"] == "succeeded"
    assert sum(c["kind"] == "model" for c in task["calls"].values()) == 2
    assert sum(c.get("operation") == "write_file" for c in task["calls"].values()) == 1
    first, head = report["sourceCheckpoints"]
    committed = [r["data"] for r in cp if r["type"] == "checkpoint.state" and r["data"]["status"] == "COMMITTED"]
    assert committed == [first, head] and first["revision"] == 1 and head["revision"] == 2 and head["parentCheckpointId"] == first["checkpointId"]
    for checkpoint in committed:
        states = [r["data"]["status"] for r in cp if r["type"] == "checkpoint.state" and r["data"]["checkpointId"] == checkpoint["checkpointId"]]
        assert states == ["PREPARING", "DATA_READY", "COMMITTED"]
        reference = checkpoint["identity"]["sessionRef"]
        assert sha(root/"agent-control/checkpoints"/reference["path"]) == reference["sha256"]
        prefix = json.loads((root/"agent-control/checkpoints"/reference["path"]).read_text())
        parent = None
        for node in prefix["nodes"]:
            assert node["parentId"] == parent; parent = node["nodeId"]
        assert parent == prefix["nodeId"] == checkpoint["identity"]["sessionNodeId"]
    for path, expected in report["originalControlHashes"].items():
        assert path in {"agent-control/tasks.jsonl", "agent-control/sessions/"+task["sessionId"]+".jsonl"}
        assert sha(root/path) == expected
    prefixes = {}
    for recorded in (report["saveRetry"]["journalsUnchanged"], report["activeSourceRefusal"]["journalsUnchanged"]):
        for path, expected in recorded.items():
            path = Path(path)
            if not path.is_absolute(): path = root/path
            assert path.is_relative_to(root)
            prefixes[str(path)] = prefix_matches(path, expected)
    assert report["saveRetry"]["sameResponse"] and report["saveRetry"]["checkpointId"] == first["checkpointId"]
    assert report["saveRetry"]["repeatCalls"] == [{"action": "finish", "checkpointId": first["checkpointId"], "jobId": "backup-"+first["checkpointId"][3:], "statusCode": 200}]
    assert report["activeSourceRefusal"]["agentStatus"] == report["activeSourceRefusal"]["goStatus"] == 409
    assert report["sessionReferenceRefusal"]["statusCode"] == 409 and report["sessionReferenceRefusal"]["originalRestored"]
    assert sha(root/report["sessionReferenceRefusal"]["reference"]) == report["sessionReferenceRefusal"]["sha256"]
    assert sha(root/"synthetic-reference-original.json") == head["identity"]["sessionRef"]["sha256"]
    assert {c["kind"] for c in report["cases"]} == {"snapshot-missing", "manifest-mismatch", "image-id-mismatch"}
    for case in report["cases"]:
        assert case["passed"] and case["maintenance"]["actualRuntimeType"] == "io.containerd.runsc.v1"
        workspace = case["target"]["workspaceId"]
        history = [r["data"] for r in go if r["type"] == "workspace.barrier" and r["data"]["workspaceId"] == workspace]
        assert history and history[-1]["phase"] == "released" and history[-1]["restoreSource"] == case["source"]
        assert {r["jobId"] for r in history} == {case["jobId"]}
        assert {r["podUid"] for r in history if r.get("podUid")} == {case["maintenance"]["podUid"]}
        assert case["claimStatus"] == 409
        if case["kind"] != "image-id-mismatch":
            assert case["waitStatus"] == 503 and case["finishStatus"] == 409 and not history[-1].get("restoreVerified", False)
            assert not call(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "ls", "-A", "/export/workspaces/"+workspace]).strip()
        else:
            assert case["waitStatus"] == 200 and case["fileToolStatus"] == 409 and history[-1]["restoreVerified"]
            assert case["actualImageId"] == head["identity"]["imageId"] != case["expectedImageId"] == case["source"]["imageId"]
            records = [r["data"] for r in go if r["type"] == "workspace.state" and r["data"]["workspaceId"] == workspace]
            assert records and all(r["phase"] != "bound" for r in records)
            assert records[-1]["owner"]["stopConfirmed"]
    forgotten = report["forgotten"]
    assert forgotten["checkpointId"] == first["checkpointId"] and forgotten["snapshotId"] == first["data"]["snapshotId"]
    assert forgotten["restoreStatus"] == 409 and forgotten["historicalMessagesStatus"] == 200 and forgotten["headCheckpointId"] == head["checkpointId"]
    events = [r["data"] for r in cp if r["type"] == "checkpoint.forget"]
    assert [r["status"] for r in events] == ["PREPARED", "FORGOTTEN"]
    assert all(r["snapshotId"] == forgotten["snapshotId"] for r in events)
    before = set(report["inventoryBefore"]); after = set(report["inventoryAfter"])
    assert before-after == {forgotten["snapshotId"]} and not after-before
    assert after == {state["owner"]["ownerSnapshotId"], head["data"]["snapshotId"]}
    values = json.loads((root/"repository-credentials.json").read_text())
    binary = Path.home()/".local/share/sandboxd-tools/restic-0.18.1/restic"
    assert sha(binary) == "01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6"
    env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "RESTIC_PASSWORD": values["password"],
           "AWS_ACCESS_KEY_ID": values["access-key"], "AWS_SECRET_ACCESS_KEY": values["secret-key"],
           "RESTIC_REPOSITORY": "s3:http://127.0.0.1:9002/sandboxd-workspace/"+state["prefix"], "AWS_DEFAULT_REGION": "us-east-1"}
    command = [str(binary), "--no-cache", "-o", "s3.connections=2", "-o", "s3.bucket-lookup=path"]
    assert json.loads(call(command+["cat", "config"], env))["id"] == state["owner"]["repositoryCryptoId"]
    snapshots = json.loads(call(command+["snapshots", "--json"], env))
    assert {s["id"] for s in snapshots} == after and all("controller:"+state["controllerId"] in s["tags"] for s in snapshots)
    call(command+["check", "--read-data"], env)
    expected = hashlib.sha256(b"synthetic checkpoint refusal acceptance\n").hexdigest()
    actual = call(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "sha256sum", "/export/workspaces/"+task["workspaceId"]+"/report.md"]).split()[0].decode()
    assert actual == expected == report["sourceFileSha256"]
    for ns in ("sandboxd-demo", "sandboxd-storage"):
        assert not json.loads(call(["kubectl", "--context", "kind-sandboxd", "-n", ns, "get", "pods", "-o", "json"]))["items"]
    result = {"passed": True, "scope": "Independent journal/hash/live inventory/read-data audit of same actual acceptance experiment; no new service scenario, task or deletion",
              "sourceSha256": sha(source), "journalRecords": {"go": len(go), "checkpoints": len(cp), "tasks": len(tasks)},
              "journalPrefixRecords": prefixes, "nativeRetainedSnapshotIds": sorted(after), "forgottenSnapshotId": forgotten["snapshotId"],
              "sourceFileSha256": actual, "externalModelCalls": 0, "formalV2AcceptanceComplete": False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source, args.output)
    print(json.dumps({"passed": result["passed"], "journalRecords": result["journalRecords"]}))
