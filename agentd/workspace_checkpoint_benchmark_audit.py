"""固定性能结果独立重算；只读本轮报告和Go控制日志，不连接服务。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics

from workspace_gc_joint_audit import journal

SCENARIOS = {"few-files": (4, 1024), "many-small-files": (32, 256), "partial-change": (8, 4096)}


def data(name):
    count, size = SCENARIOS[name]; rng = random.Random(20261018)
    return {f"reports/file-{i:03}.txt": "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789 ") for _ in range(size - 1)) + "\n" for i in range(count)}


def metric(values):
    values = sorted(values); position = (len(values) - 1) * .95; low = int(position)
    high = min(low + 1, len(values) - 1)
    return {"samples": len(values), "p50Ms": statistics.median(values), "p95Ms": values[low] + (values[high] - values[low]) * (position - low),
            "minMs": min(values), "maxMs": max(values)}


def compare_stats(actual, values):
    if not values:
        assert actual == {"samples": 0}; return
    for key, value in metric(values).items():
        assert math.isclose(actual[key], value, abs_tol=1e-6), key


def audit(source, output):
    assert not output.exists()
    raw = json.loads(source.read_text()); assert raw["passed"] and raw["seed"] == 20261018 and raw["concurrency"] == 1
    assert raw["mode"] == "actual-fixed-workload-nfs-v1-v2-backend-benchmark" and raw["externalModelCalls"] == 0
    v1path = Path(raw["v1Report"]); assert hashlib.sha256(v1path.read_bytes()).hexdigest() == raw["v1ReportSha256"]
    v1 = json.loads(v1path.read_text()); assert v1["passed"] and v1["seed"] == raw["seed"] and v1["repeats"] == raw["repeats"]
    assert v1["environment"]["binarySha256"] == raw["environment"]["goBinarySha256"]
    assert v1["environment"]["nfsImageId"] == raw["environment"]["nfsImageId"]
    expected_keys = {(name, i) for name in SCENARIOS for i in range(raw["repeats"])}
    assert {(r["scenario"], r["repeat"]) for r in raw["trials"]} == {(r["scenario"], r["repeat"]) for r in v1["trials"]} == expected_keys
    assert len(raw["trials"]) == len(v1["trials"]) == len(expected_keys)
    root = Path(raw["controlRoot"])
    assert root.parent == Path.home() / ".local/share/sandboxd/workspace-checkpoint-benchmark" and re.fullmatch("[a-f0-9]{32}", root.name)
    owners = set(); snapshot_ids = set(); journal_count = 0
    for row in raw["trials"]:
        assert row["passed"] and not row.get("cleanupUncertain") and "activeBackup" not in row and "activeRestore" not in row
        files = data(row["scenario"]); count = len(files)
        digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        partner = next(r for r in v1["trials"] if (r["scenario"], r["repeat"]) == (row["scenario"], row["repeat"]))
        assert row["datasetSha256"] == partner["datasetSha256"] == digest
        assert row["initialBytes"] == partner["initialBytes"] == sum(len(v.encode()) for v in files.values())
        assert row["businessPod"]["imageId"] == partner["pod"]["imageId"]
        for pod in (row["businessPod"], partner["pod"]):
            assert pod["runtimeClass"] == "gvisor" and pod["nfsMount"]["fstype"] == "nfs4" and "vers=4.1" in pod["nfsMount"]["options"]
        initial = {p: hashlib.sha256(v.encode()).hexdigest() for p, v in files.items()}; changed = dict(initial)
        if row["scenario"] == "partial-change":
            path = "reports/file-000.txt"; changed[path] = hashlib.sha256(("changed:" + files[path][8:]).encode()).hexdigest()
        for trial in (row, partner):
            assert len(trial["operations"]) == 2 * count + (row["scenario"] == "partial-change")
            for op in trial["operations"]:
                expected = changed if op["tool"] == "read_file" or op["phase"] == "update" else initial
                assert op["sha256"] == expected[op["path"]] and op["elapsedMs"] >= 0
        assert len(row["snapshots"]) == 2
        owners.add(row["owner"]["repositoryCryptoId"])
        trialroot = Path(row["controlRoot"]); assert trialroot.parent == root and re.fullmatch("[a-f0-9]{32}", trialroot.name)
        records = journal(trialroot / "go-control/workspaces.jsonl", go=True); journal_count += len(records)
        assert next(r["data"]["owner"] for r in records if r["type"] == "repository.binding") == row["owner"]
        for index, snapshot in enumerate(row["snapshots"]):
            result = snapshot["result"]; snapshot_ids.add(result["snapshotId"])
            assert result["repositoryCryptoId"] == row["owner"]["repositoryCryptoId"]
            assert snapshot["runtime"]["actualRuntimeType"] == "io.containerd.runsc.v1"
            assert snapshot["manifest"]["bytes"] == row["initialBytes"]
            assert {f["path"]: f["sha256"] for f in snapshot["manifest"]["files"]} == (initial if index == 0 else changed)
            manifest = [{k: f[k] for k in ("path", "bytes", "mode", "sha256")} for f in snapshot["manifest"]["files"]]
            assert hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest() == snapshot["manifest"]["sha256"] == result["manifestSha256"]
            assert math.isclose(snapshot["rpcMs"], sum(snapshot["actionsMs"].values()), abs_tol=1e-6)
            assert snapshot["observedPauseMs"] >= snapshot["rpcMs"] and snapshot["observationMs"] >= 0
            assert snapshot["summary"]["total_files_processed"] == count
            assert snapshot["summary"]["total_bytes_processed"] == row["initialBytes"]
            states = [r["data"] for r in records if r["type"] == "workspace.barrier" and r["data"]["operation"] == "backup" and r["data"]["checkpointId"] == snapshot["checkpointId"]]
            assert states[0]["phase"] == "reserved" and states[-1]["phase"] == "released"
            assert states[-1]["stopReason"] == "containers-terminated"
        assert row["restoreRuntime"]["actualRuntimeType"] == "io.containerd.runsc.v1"
        restores = [r["data"] for r in records if r["type"] == "workspace.barrier" and r["data"]["operation"] == "restore"]
        assert restores[-1]["phase"] == "released" and restores[-1]["restoreVerified"]
        assert restores[-1]["restoreSource"]["snapshotId"] == row["snapshots"][0]["result"]["snapshotId"]
        assert row["restore"]["toVerifiedFilesAndReleaseMs"] >= row["restore"]["rpcMs"] >= 0
        assert math.isclose(row["batchRpcMs"], sum(o["elapsedMs"] for o in row["operations"]) + sum(s["rpcMs"] for s in row["snapshots"]), abs_tol=1e-6)
        for resource in [s["resources"] for s in row["snapshots"]] + [row["restore"]["resources"]]:
            for name, counters in resource["delta"]["containers"].items():
                assert all(v >= 0 for v in counters.values())
                for key in counters:
                    assert math.isclose(counters[key], resource["after"]["containers"][name][key] - resource["before"]["containers"][name][key], abs_tol=1e-6)
    assert len(owners) == len(raw["trials"]) and len(snapshot_ids) == 2 * len(raw["trials"])
    for name in SCENARIOS:
        rows = [r for r in raw["trials"] if r["scenario"] == name]; old = [r for r in v1["trials"] if r["scenario"] == name]
        summary = raw["summary"][name]
        for key, values in {"initialCheckpointRpc": [r["snapshots"][0]["rpcMs"] for r in rows],
                            "nextCheckpointRpc": [r["snapshots"][1]["rpcMs"] for r in rows],
                            "restoreToVerifiedAndReleased": [r["restore"]["toVerifiedFilesAndReleaseMs"] for r in rows],
                            "v1IoWindow": [r["ioWindowMs"] for r in old], "v2BatchRpc": [r["batchRpcMs"] for r in rows],
                            "v2BatchWithTwoCheckpoints": [r["batchWithCheckpointsMs"] for r in rows]}.items(): compare_stats(summary[key], values)
        for tool, phase in (("write_file", "normal"), ("write_file", "update"), ("read_file", "normal")):
            compare_stats(summary[tool + ":" + phase], [o["elapsedMs"] for r in rows for o in r["operations"] if o["tool"] == tool and o["phase"] == phase])
    output.write_text(json.dumps({"schemaVersion": 1, "passed": True, "source": str(source),
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(), "auditSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "v1Trials": len(v1["trials"]), "v2Trials": len(raw["trials"]), "independentRepositories": len(owners),
        "snapshots": len(snapshot_ids), "goJournalRecords": journal_count,
        "scope": "Offline independent dataset/manifest/hash/protocol/counter/quantile recomputation of same experiment; no new service/restore/performance run"}, indent=2) + "\n")
    print("paired datasets, manifests, Go journals, counters and quantiles passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); audit(args.source, args.output)
