"""固定同条件V1/V2后端微基准；不模拟联合提交，也不调用模型。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import time
from uuid import uuid4

from .app.clients import SandboxdClient
from .app.runtime.recovery import new_id
from .app.redaction import public_error
from .app.tools.remote_files import WorkspaceBinding, RemoteFileWorkspace
from .workspace_benchmark import dataset, SCENARIOS, SEED, summary, run as baseline, process_counters
from .workspace_backup_component import KUBE, MINIO, private_json, kube_object
from .workspace_checkpoint_fault_demo import require_idle_ports, observe_maintenance
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go
from .workspace_gc_joint_demo import bootstrap
from .workspace_migrate import require_ok

CONTAINERS = ("sandboxd-workspace-nfs-1", MINIO, "sandboxd-control-plane")


def benchmark_binary(binary=None):
    path = Path(binary) if binary is not None else REPO / ".cache/workspace-sandboxd"
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("build current trusted binary with workspace.sh build-storage before measuring")
    return path.resolve()


def restic_binary():
    return Path.home() / ".local/share/sandboxd-tools/restic-0.18.1/restic"


def counters(pid):
    result = {"go": process_counters(pid), "controller": process_counters(os.getpid()), "containers": {}}
    for name in CONTAINERS:
        net = {k: int(checked(["docker", "exec", name, "cat", "/sys/class/net/eth0/statistics/" + k])) for k in ("rx_bytes", "tx_bytes")}
        cpu = dict(line.split() for line in checked(["docker", "exec", name, "cat", "/sys/fs/cgroup/cpu.stat"]).splitlines())
        memory = int(checked(["docker", "exec", name, "cat", "/sys/fs/cgroup/memory.current"]))
        result["containers"][name] = {**net, "cpuSeconds": int(cpu["usage_usec"]) / 1e6, "memoryBytes": memory}
    return result


def delta(before, after):
    result = {"goCpuSeconds": after["go"]["cpuSeconds"] - before["go"]["cpuSeconds"],
              "controllerCpuSeconds": after["controller"]["cpuSeconds"] - before["controller"]["cpuSeconds"], "containers": {}}
    for name in CONTAINERS:
        result["containers"][name] = {k: after["containers"][name][k] - before["containers"][name][k] for k in ("rx_bytes", "tx_bytes", "cpuSeconds")}
        assert all(v >= 0 for v in result["containers"][name].values())
    return {"before": before, "after": after, "delta": result}


def repository_stats(values, root_id, trial_id, owner):
    binary = restic_binary()
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == "01143daba61a1dc8afb0cb3d03ba83e6b118f55b6b146b27473a09efc3df13d6"
    env = {"PATH": os.environ["PATH"], "HOME": str(Path.home()), "RESTIC_PASSWORD": values["password"],
           "RESTIC_REPOSITORY": "s3:http://127.0.0.1:9002/sandboxd-workspace/owned-perf/" + root_id + "/" + trial_id,
           "AWS_ACCESS_KEY_ID": values["access-key"], "AWS_SECRET_ACCESS_KEY": values["secret-key"], "AWS_DEFAULT_REGION": "us-east-1"}
    command = [str(binary), "--no-cache", "-o", "s3.connections=2", "-o", "s3.bucket-lookup=path"]
    def call(*args):
        result = subprocess.run(command + list(args), env=env, capture_output=True, timeout=60)
        if result.returncode: raise RuntimeError("fixed repository measurement failed, exit=" + str(result.returncode))
        return json.loads(result.stdout)
    assert call("cat", "config")["id"] == owner["repositoryCryptoId"]
    stats = call("stats", "--mode", "raw-data", "--json")
    physical = int(checked(["docker", "exec", MINIO, "du", "-sb", "/data/sandboxd-workspace/owned-perf/" + root_id + "/" + trial_id]).split()[0])
    return {"resticRawDataStats": stats, "minioPrefixApparentBytes": physical,
            "scope": "restic referenced raw-data stats plus MinIO prefix apparent filesystem bytes including metadata/directories; not S3 billable bytes"}


async def run(output: Path, repeats=3, binary=None):
    if not 1 <= repeats <= 5: raise ValueError("repeats must be1..5")
    base_output = output.with_name(output.stem + "-v1.json")
    if output.exists() or base_output.exists(): raise ValueError("preserve previous evidence")
    binary = benchmark_binary(binary)
    require_idle_ports((10880,))
    for ns in ("sandboxd-demo", "sandboxd-storage"):
        assert not json.loads(checked([*KUBE, "-n", ns, "get", "pods", "-o", "json"]))["items"]
    nfs = json.loads(checked(["docker", "inspect", CONTAINERS[0]]))[0]
    minio = json.loads(checked(["docker", "inspect", MINIO]))[0]
    assert nfs["State"]["Running"] and minio["State"]["Running"] and not minio["State"]["Paused"]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert minio["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace-backup"
    nfs_ip, endpoint = nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"], minio["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    assert {"HostIp": "127.0.0.1", "HostPort": "9002"} in minio["NetworkSettings"]["Ports"]["9000/tcp"]
    root = Path.home() / ".local/share/sandboxd/workspace-checkpoint-benchmark" / uuid4().hex
    root.mkdir(parents=True, mode=0o700)
    report = {"passed": False, "mode": "actual-fixed-workload-nfs-v1-v2-backend-benchmark", "externalModelCalls": 0,
              "controlRoot": str(root), "seed": SEED, "repeats": repeats, "concurrency": 1,
              "cachePolicy": "2 list RPC warmups; warm read after write; no kernel cache drop; fresh workspace and repository per trial",
              "backgroundCondition": "serial project benchmark; no concurrent project tests/install; host/Kubernetes background not isolated",
              "scope": "trusted backend maintenance microbenchmark; one backup per batch, not default Agent checkpoint per tool group; excludes model/Session/Task journal cost",
              "resourceScope": "Go/controller and container cumulative CPU plus memory endpoints, not peaks; kind includes maintenance Pods and all K8s background; controller excludes child CPU",
              "networkScope": "container eth0 deltas include metadata/headers/protocol/background; not application PUT bytes or public egress; restic data_added_packed separately recorded",
              "environment": {"python": platform.python_version(), "kernel": platform.release(), "cpuCount": os.cpu_count(),
                              "goBinarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "nfsImageId": nfs["Image"], "minioImageId": minio["Image"],
                              "dockerVersion": checked(["docker", "version", "--format", "{{.Server.Version}}"]).strip(), "deployment": "same host kind/NFS/MinIO"},
              "v1Report": str(base_output), "trials": []}
    try:
        v1 = await baseline(base_output, binary, nfs_ip, repeats, "serial")
        assert v1["passed"]
        report["v1ReportSha256"] = hashlib.sha256(base_output.read_bytes()).hexdigest()
        credentials = private_json(Path.home() / ".local/share/sandboxd/workspace-backup/minio-credentials.json")
        for repeat in range(repeats):
            for name in SCENARIOS:
                trial_id = uuid4().hex; trial_root = root / trial_id; trial_root.mkdir(mode=0o700)
                files = dataset(name); hashes = {p: hashlib.sha256(v.encode()).hexdigest() for p, v in files.items()}
                row = {"scenario": name, "repeat": repeat, "passed": False, "controlRoot": str(trial_root), "files": len(files),
                       "initialBytes": sum(len(v.encode()) for v in files.values()), "datasetSha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                       "operations": [], "snapshots": []}
                report["trials"].append(row)
                secret = "restic-" + uuid4().hex[:16]
                values = {"access-key": credentials["access"], "secret-key": credentials["secret"], "password": secrets.token_urlsafe(32),
                          "repository": "s3:http://" + endpoint + ":9000/sandboxd-workspace/owned-perf/" + root.name + "/" + trial_id}
                kube_object("create", {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": secret,
                    "namespace": "sandboxd-storage", "labels": {"sandbox.io/managed-by": "sandboxd-backup"}},
                    "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()}})
                owner = (await bootstrap(secret, endpoint, new_id("controller")))["owner"]
                row.update(secretName=secret, owner=owner, repositoryBaseline=repository_stats(values, root.name, trial_id, owner))
                process, log, token = start_go(trial_root, binary, nfs_ip, backup_secret=secret, backup_minio_ip=endpoint,
                    backup_image="sandboxd-restic:0.18.1-v3", backup_owner=owner)
                client = SandboxdClient("http://127.0.0.1:10880", token); binding = target_binding = None
                workspace, task, attempt = new_id("workspace"), new_id("task"), new_id("attempt")
                try:
                    await ready(process)
                    begin = time.perf_counter(); require_ok(await client.prepare_workspace(workspace, "session-" + uuid4().hex[:16], new_id("branch")))
                    row["prepareMs"] = (time.perf_counter() - begin) * 1000
                    begin = time.perf_counter(); metadata = require_ok(await client.claim_workspace(workspace, task, attempt))
                    row["claimReadyMs"] = (time.perf_counter() - begin) * 1000
                    binding = WorkspaceBinding.from_record(metadata); row["businessPod"] = observe_pod(metadata)
                    remote = RemoteFileWorkspace(client, binding)
                    for _ in range(2): require_ok(await remote.execute("list_files", {"path": "."}))
                    async def operation(tool, args, expected):
                        started = time.perf_counter(); result = require_ok(await remote.execute(tool, args))
                        elapsed = (time.perf_counter() - started) * 1000
                        assert result.get("newSha256" if tool == "write_file" else "sha256") == expected
                        row["operations"].append({"tool": tool, "path": args["path"], "phase": "update" if "expectedSha256" in args else "normal", "elapsedMs": elapsed, "sha256": expected})
                    async def checkpoint(phase):
                        cp, job = new_id("cp"), "perf-" + uuid4().hex
                        row["activeBackup"] = {"checkpointId": cp, "jobId": job, "binding": binding.as_request()}
                        before = counters(process.pid); actions = {}; begin = time.perf_counter()
                        for action in ("reserve", "start", "wait"):
                            t = time.perf_counter(); body = require_ok(await client.workspace_backup(action, binding.as_request(), cp, job))
                            actions[action] = (time.perf_counter() - t) * 1000
                        observe_start = time.perf_counter(); barrier, result = body["barrier"], body["result"]
                        runtime = observe_maintenance(barrier)
                        raw = json.loads(checked([*KUBE, "-n", "sandboxd-storage", "logs", barrier["podName"]]))
                        assert raw["ok"] and raw["snapshotId"] == result["snapshotId"] and raw["repository"]["repositoryCryptoId"] == owner["repositoryCryptoId"]
                        private_json(trial_root / (cp + ".json"), {"kind": "backend-performance-receipt", "sessionBound": False, "checkpointId": cp, "result": result})
                        observation_ms = (time.perf_counter() - observe_start) * 1000
                        t = time.perf_counter(); require_ok(await client.workspace_backup("finish", binding.as_request(), cp, job)); actions["finish"] = (time.perf_counter() - t) * 1000
                        elapsed = (time.perf_counter() - begin) * 1000; after = counters(process.pid)
                        item = {"phase": phase, "checkpointId": cp, "result": result, "runtime": runtime, "summary": raw["summary"],
                                "manifest": raw["manifest"], "actionsMs": actions, "rpcMs": sum(actions.values()),
                                "observedPauseMs": elapsed, "observationMs": observation_ms, "resources": delta(before, after),
                                "repository": repository_stats(values, root.name, trial_id, owner)}
                        assert {f["path"]: f["sha256"] for f in raw["manifest"]["files"]} == hashes
                        row["snapshots"].append(item)
                        row.pop("activeBackup")
                        return item
                    io_start = time.perf_counter()
                    for path, content in files.items(): await operation("write_file", {"path": path, "content": content}, hashes[path])
                    initial = await checkpoint("initial-fresh-repository")
                    if name == "partial-change":
                        path = next(iter(files)); updated = "changed:" + files[path][8:]
                        expected = hashlib.sha256(updated.encode()).hexdigest()
                        await operation("write_file", {"path": path, "content": updated, "expectedSha256": hashes[path]}, expected); hashes[path] = expected
                        row.update(changedFiles=1, changedFileFraction=1 / len(files))
                    await checkpoint("partial-change" if name == "partial-change" else "unchanged")
                    for path in files: await operation("read_file", {"path": path}, hashes[path])
                    row["batchWithCheckpointsMs"] = (time.perf_counter() - io_start) * 1000
                    row["batchRpcMs"] = sum(o["elapsedMs"] for o in row["operations"]) + sum(s["rpcMs"] for s in row["snapshots"])
                    assert {e["path"]: e["sha256"] for e in require_ok(await remote.execute("list_files", {"path": "."}))["entries"]} == hashes
                    require_ok(await client.release_workspace(binding.as_request())); binding = None
                    target = {"workspaceId": new_id("workspace"), "sessionId": "session-" + uuid4().hex[:16], "branchId": new_id("branch")}
                    before = counters(process.pid); restore_start = time.perf_counter()
                    require_ok(await client.prepare_workspace(target["workspaceId"], target["sessionId"], target["branchId"]))
                    source = {"snapshotId": initial["result"]["snapshotId"], "manifestSha256": initial["result"]["manifestSha256"],
                              "repositoryCryptoId": owner["repositoryCryptoId"], "imageRef": metadata["imageRef"], "imageId": metadata["imageId"]}
                    restore_job = "restore-" + uuid4().hex; restore_actions = {}
                    row["activeRestore"] = {"target": target, "checkpointId": initial["checkpointId"], "jobId": restore_job, "source": source}
                    for action in ("reserve", "start", "wait", "finish"):
                        t = time.perf_counter(); result = require_ok(await client.workspace_restore(action, target, initial["checkpointId"], restore_job, source)); restore_actions[action] = (time.perf_counter() - t) * 1000
                        if action == "wait": row["restoreRuntime"] = observe_maintenance(result["barrier"])
                    target_binding = WorkspaceBinding.from_record(require_ok(await client.claim_workspace(target["workspaceId"], new_id("task"), new_id("attempt"))))
                    target_remote = RemoteFileWorkspace(client, target_binding)
                    restored = require_ok(await target_remote.execute("list_files", {"path": "."}))
                    expected_initial = {p: hashlib.sha256(v.encode()).hexdigest() for p, v in files.items()}
                    assert not restored["truncated"] and {e["path"]: e["sha256"] for e in restored["entries"]} == expected_initial
                    require_ok(await client.release_workspace(target_binding.as_request())); target_binding = None
                    row["restore"] = {"toVerifiedFilesAndReleaseMs": (time.perf_counter() - restore_start) * 1000,
                        "actionsMs": restore_actions, "rpcMs": sum(restore_actions.values()), "resources": delta(before, counters(process.pid)),
                        "scope": "fresh target prepare + restore RPC + runtime observation + claim/list hash verification + release; restores initial snapshot"}
                    row.pop("activeRestore")
                    row["passed"] = True
                    print(json.dumps({"scenario": name, "repeat": repeat, "v2Passed": True}), flush=True)
                finally:
                    if "activeBackup" in row:
                        active = row["activeBackup"]
                        try:
                            require_ok(await client.workspace_backup("abort", active["binding"], active["checkpointId"], active["jobId"], timeout=30))
                            private_json(trial_root / (active["checkpointId"] + "-failed.json"), {"kind": "backend-performance-failed-receipt", "sessionBound": False, "checkpointId": active["checkpointId"]})
                            require_ok(await client.workspace_backup("finish", active["binding"], active["checkpointId"], active["jobId"], timeout=30))
                        except Exception: row["cleanupUncertain"] = True
                    if "activeRestore" in row:
                        active = row["activeRestore"]
                        try:
                            for action in ("abort", "fail"):
                                require_ok(await client.workspace_restore(action, active["target"], active["checkpointId"], active["jobId"], active["source"], timeout=30))
                        except Exception: row["cleanupUncertain"] = True
                    for b in (binding, target_binding):
                        if b is not None:
                            try: require_ok(await client.release_workspace(b.as_request()))
                            except Exception: row["cleanupUncertain"] = True
                    await client.close(); stop_go(process, log)
        report["summary"] = {}
        for name in SCENARIOS:
            rows = [r for r in report["trials"] if r["scenario"] == name]
            compare = [r for r in v1["trials"] if r["scenario"] == name]
            assert len(rows) == len(compare) == repeats and {r["datasetSha256"] for r in rows} == {r["datasetSha256"] for r in compare}
            result = {"initialCheckpointRpc": summary([r["snapshots"][0]["rpcMs"] for r in rows]),
                      "nextCheckpointRpc": summary([r["snapshots"][1]["rpcMs"] for r in rows]),
                      "restoreToVerifiedAndReleased": summary([r["restore"]["toVerifiedFilesAndReleaseMs"] for r in rows]),
                      "v1IoWindow": summary([r["ioWindowMs"] for r in compare]),
                      "v2BatchRpc": summary([r["batchRpcMs"] for r in rows]),
                      "v2BatchWithTwoCheckpoints": summary([r["batchWithCheckpointsMs"] for r in rows])}
            for tool, phase in (("write_file", "normal"), ("write_file", "update"), ("read_file", "normal")):
                result[tool + ":" + phase] = summary([o["elapsedMs"] for r in rows for o in r["operations"] if o["tool"] == tool and o["phase"] == phase])
            report["summary"][name] = result
        report["passed"] = all(r["passed"] for r in report["trials"])
    except BaseException as error:
        report["error"] = public_error(error); raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--binary", type=Path, help="显式固定可信Go二进制；默认统一build-storage产物")
    args = parser.parse_args(); asyncio.run(run(args.output, args.repeats, args.binary))
