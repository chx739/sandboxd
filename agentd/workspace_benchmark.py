"""固定小文本负载，真实Go/gVisor/NFS文件RPC与容器网络计数。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import random
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .app.clients import SandboxdClient
from .app.redaction import public_error
from .app.runtime.recovery import new_id
from .app.tools.remote_files import RemoteFileWorkspace, WorkspaceBinding
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go
from .workspace_migrate import require_ok

SEED = 20261018
SCENARIOS = {"few-files": (4, 1024), "many-small-files": (32, 256), "partial-change": (8, 4096)}


def dataset(name: str) -> dict[str, str]:
    if name not in SCENARIOS:
        raise ValueError("unknown fixed workload")
    count, size = SCENARIOS[name]
    rng = random.Random(SEED)
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 "
    return {f"reports/file-{i:03}.txt": "".join(rng.choice(alphabet) for _ in range(size - 1)) + "\n" for i in range(count)}


def percentile(values, fraction):
    if not values or not 0 <= fraction <= 1:
        raise ValueError("invalid quantile input")
    values = sorted(values)
    position = (len(values) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def summary(values):
    if not values:
        return {"samples": 0}
    return {"samples": len(values), "p50Ms": statistics.median(values), "p95Ms": percentile(values, .95),
            "minMs": min(values), "maxMs": max(values), "quantileMethod": "linear interpolation on observed samples"}


def network_counters():
    result = {}
    for name in ("rx_bytes", "tx_bytes"):
        result[name] = int(checked(["docker", "exec", "sandboxd-workspace-nfs-1", "cat", "/sys/class/net/eth0/statistics/" + name]).strip())
    return result


def process_counters(pid):
    status = Path(f"/proc/{pid}/status").read_text()
    rss = int(next(line.split()[1] for line in status.splitlines() if line.startswith("VmRSS:")))
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"rssKiB": rss, "cpuSeconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")}


def nfs_resources():
    cpu = checked(["docker", "exec", "sandboxd-workspace-nfs-1", "cat", "/sys/fs/cgroup/cpu.stat"])
    counters = dict(line.split() for line in cpu.splitlines())
    memory = int(checked(["docker", "exec", "sandboxd-workspace-nfs-1", "cat", "/sys/fs/cgroup/memory.current"]).strip())
    return {"cpuSeconds": int(counters["usage_usec"]) / 1000000, "memoryBytes": memory,
            "scope": "container cgroup v2 cumulative CPU and current charged memory; not peak"}


async def run(output: Path, binary: Path, nfs_server: str, repeats: int, background_condition="unspecified"):
    if not 1 <= repeats <= 5:
        raise ValueError("benchmark repeats must be1..5")
    if background_condition not in {"unspecified", "serial", "parallel-local-regression"}:
        raise ValueError("invalid background condition")
    assert checked(["kubectl", "config", "current-context"]).strip() == "kind-sandboxd"
    for namespace in ("sandboxd-demo", "sandboxd-storage"):
        assert not json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", namespace, "get", "pods", "-o", "json"]))["items"], "benchmark requires an idle project workspace"
    server = json.loads(checked(["docker", "inspect", "sandboxd-workspace-nfs-1"]))[0]
    assert server["Config"]["Labels"]["com.docker.compose.project"] == "sandboxd-workspace"
    assert server["State"]["Running"] and not server["State"]["Paused"]
    assert nfs_server in [n["IPAddress"] for n in server["NetworkSettings"]["Networks"].values()]
    root = Path.home() / ".local/share/sandboxd/workspace-agent-smoke" / uuid4().hex
    root.mkdir(mode=0o700, parents=True)
    process, log, token = start_go(root, binary, nfs_server)
    client = SandboxdClient("http://127.0.0.1:10880", token)
    report = {"mode": "real-go-gvisor-nfs-fixed-workload", "passed": False, "externalModelCalls": 0, "seed": SEED,
              "repeats": repeats, "concurrency": 1, "controlRoot": str(root), "trials": [],
              "environment": {"cpuCount": os.cpu_count(), "controllerKernel": platform.release(), "machine": platform.machine(),
                              "pythonVersion": platform.python_version(),
                              "cpuModel": next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")), "unknown"),
                              "nodeKernel": checked(["docker", "exec", "sandboxd-control-plane", "uname", "-r"]).strip(),
                              "nfsImageId": server["Image"], "dockerVersion": checked(["docker", "version", "--format", "{{.Server.Version}}"]).strip(),
                              "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "deployment": "Docker Desktop single kind node; NFS on same host"},
              "cachePolicy": "sequential warm process;2 list RPC warmups per Pod; read-after-write; no kernel cache drop",
              "networkScope": "NFS container eth0 counters, including mount/metadata/protocol/observation traffic; not internet traffic or application payload bytes",
              "backgroundCondition": background_condition,
              "backgroundScope": "caller-declared project test scheduling; host/Kubernetes background not fully isolated",
              "resourceScope": "Go/controller process CPU and RSS endpoints, NFS cgroup CPU and memory endpoints; not peak or whole-system CPU",
              "limits": "small synthetic UTF-8 workspaces; no arbitrary shell from model; not production throughput/SLA; V2 not measured here"}
    net_before = None
    try:
        await ready(process)
        net_before = network_counters()
        cpu_before = process_counters(process.pid)
        nfs_before = nfs_resources()
        controller_before = process_counters(os.getpid())
        for repeat in range(repeats):
            for name in SCENARIOS:
                files = dataset(name)
                row = {"scenario": name, "repeat": repeat, "files": len(files), "initialBytes": sum(len(v.encode()) for v in files.values()),
                       "datasetSha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                       "operations": [], "passed": False}
                report["trials"].append(row)
                workspace, task, attempt = new_id("workspace"), new_id("task"), new_id("attempt")
                binding = None
                try:
                    begin = time.perf_counter()
                    require_ok(await client.prepare_workspace(workspace, "session-" + uuid4().hex[:16], new_id("branch")))
                    row["prepareMs"] = (time.perf_counter() - begin) * 1000
                    begin = time.perf_counter()
                    metadata = require_ok(await client.claim_workspace(workspace, task, attempt))
                    row["claimReadyMs"] = (time.perf_counter() - begin) * 1000
                    binding = WorkspaceBinding.from_record(metadata)
                    row["pod"] = observe_pod(metadata)
                    remote = RemoteFileWorkspace(client, binding)
                    for _ in range(2):
                        require_ok(await remote.execute("list_files", {"path": "."}))
                    begin_io = time.perf_counter()

                    async def operation(tool, args, expected_hash):
                        started = time.perf_counter()
                        response = require_ok(await remote.execute(tool, args))
                        elapsed = (time.perf_counter() - started) * 1000
                        observed = response.get("newSha256" if tool == "write_file" else "sha256")
                        assert observed == expected_hash
                        row["operations"].append({"tool": tool, "path": args["path"], "phase": "update" if "expectedSha256" in args else "normal",
                                                  "elapsedMs": elapsed, "sha256": observed})

                    hashes = {path: hashlib.sha256(content.encode()).hexdigest() for path, content in files.items()}
                    for path, content in files.items():
                        await operation("write_file", {"path": path, "content": content}, hashes[path])
                    if name == "partial-change":
                        path = next(iter(files))
                        updated = "changed:" + files[path][8:]
                        expected = hashlib.sha256(updated.encode()).hexdigest()
                        await operation("write_file", {"path": path, "content": updated, "expectedSha256": hashes[path]}, expected)
                        hashes[path] = expected
                        row["changedFiles"] = 1
                        row["changedFileFraction"] = 1 / len(files)
                    for path in files:
                        await operation("read_file", {"path": path}, hashes[path])
                    row["ioWindowMs"] = (time.perf_counter() - begin_io) * 1000
                    row["operationsPerSecondInIoWindow"] = len(row["operations"]) / (row["ioWindowMs"] / 1000)
                    inventory = require_ok(await remote.execute("list_files", {"path": "."}))
                    assert inventory.get("truncated") is False
                    assert {f["path"]: f["sha256"] for f in inventory["entries"]} == hashes
                    row["resourceSample"] = process_counters(process.pid)
                    row["passed"] = True
                    print(json.dumps({"scenario": name, "repeat": repeat, "passed": True}), flush=True)
                finally:
                    # 若认领确认丢失，从自己的工作区对账；绝不清理另一个owner。
                    current = await client.get_workspace(workspace)
                    if current.status_code == 200 and current.body.get("owner", {}).get("attemptId") == attempt:
                        stopped = require_ok(await client.release_workspace(WorkspaceBinding.from_record(current.body).as_request()))
                        assert stopped["owner"].get("stopConfirmed") is True
        net_after = network_counters()
        cpu_after = process_counters(process.pid)
        nfs_after = nfs_resources()
        controller_after = process_counters(os.getpid())
        report["network"] = {"before": net_before, "after": net_after,
                             "delta": {k: net_after[k] - net_before[k] for k in net_before}}
        report["resource"] = {"cpuSeconds": cpu_after["cpuSeconds"] - cpu_before["cpuSeconds"],
                              "rssEndpointSamplesKiB": [cpu_before["rssKiB"], cpu_after["rssKiB"]] + [r["resourceSample"]["rssKiB"] for r in report["trials"]],
                              "nfs": {"before": nfs_before, "after": nfs_after, "cpuSeconds": nfs_after["cpuSeconds"] - nfs_before["cpuSeconds"]},
                              "controller": {"before": controller_before, "after": controller_after,
                                             "cpuSeconds": controller_after["cpuSeconds"] - controller_before["cpuSeconds"]}}
        report["summary"] = {}
        for name in SCENARIOS:
            trials = [r for r in report["trials"] if r["scenario"] == name]
            report["summary"][name] = {"prepare": summary([r["prepareMs"] for r in trials]), "claimReady": summary([r["claimReadyMs"] for r in trials])}
            for tool, phase in (("write_file", "normal"), ("write_file", "update"), ("read_file", "normal")):
                values = [s["elapsedMs"] for r in trials for s in r["operations"] if s["tool"] == tool and s["phase"] == phase]
                report["summary"][name][tool + ":" + phase] = summary(values)
        report["passed"] = all(row["passed"] for row in report["trials"])
    except Exception as exc:
        report["error"] = public_error(exc)
        raise
    finally:
        await client.close()
        stop_go(process, log)
        report["timestamp"] = datetime.now(timezone.utc).isoformat()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nfs-server", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=REPO / ".cache/workspace-sandboxd")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--background-condition", choices=("unspecified", "serial", "parallel-local-regression"), default="unspecified")
    args = parser.parse_args()
    result = asyncio.run(run(args.output, args.binary, args.nfs_server, args.repeats, args.background_condition))
    print(json.dumps({"passed": result["passed"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
