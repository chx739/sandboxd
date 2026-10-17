"""从真实服务报告及保留的原始日志复查完整交互与清理；不调用模型或重放工具。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .app.runtime.session import SessionJournal, _branchable_flags
from .workspace_demo import checked
from .workspace_process_demo import entries


def retained_root(report):
    root = Path(report["controlRoot"]).resolve()
    expected = (Path.home() / ".local/share/sandboxd/workspace-agent-smoke").resolve()
    assert root.parent == expected and re.fullmatch(r"[a-f0-9]{32}", root.name)
    return root


async def audit_interruption(pod_path: Path, process_path: Path) -> dict:
    pod = json.loads(pod_path.read_text())
    process = json.loads(process_path.read_text())
    assert pod["passed"] and process["passed"] and process["kill"]["returnCode"] == -9
    assert pod["mode"] == "real-local-services-replay-agent" and len(pod["faults"]) == 1
    root = retained_root(pod)
    task = pod["task"]
    states = [e["data"] for e in entries(root / "agent-control/tasks.jsonl")
              if e["type"] == "task.state" and e["data"]["taskId"] == task["taskId"]]
    state = states[-1]
    assert state["status"] == "succeeded" and len(state["attempts"]) == 2
    first = state["attempts"][0]["attemptId"]
    journal = SessionJournal(root / "agent-control/sessions", state["sessionId"])
    raw = [json.loads(line) for line in journal.path.read_text().splitlines()]
    partial = [e for e in raw if e["type"] == "session.node" and e.get("attemptId") == first
               and e.get("message", {}).get("role") == "assistant" and not e["branchable"]
               and any(c["id"] == "read-report" for c in e["message"].get("toolCalls", []))]
    assert len(partial) == 1
    visible = await journal.active_path()
    assert partial[0]["nodeId"] not in {n["nodeId"] for n in visible}
    messages = [n["message"] for n in visible]
    assert _branchable_flags(messages)[-1]
    calls = Counter(c["id"] for m in messages if m["role"] == "assistant" for c in m.get("toolCalls", []))
    results = Counter(m["toolCallId"] for m in messages if m["role"] == "tool")
    assert calls == results and all(v == 1 for v in calls.values())
    assert "read-report" not in calls and "read-after-recovery" in calls
    assert any(e["type"] == "tool.interrupted" for e in pod["traces"][0]["events"])
    assert sum(c["kind"] == "model" for c in state["calls"].values()) == 4
    assert sum(c["kind"] == "tool" for c in state["calls"].values()) == 3
    assert any(c["attemptId"] == first and c["kind"] == "tool" and c["state"] == "uncertain" for c in state["calls"].values())
    before, after = process["stateBeforeKill"], process["stateAfterRestart"]
    unknown = [key for key, value in before["calls"].items() if value["kind"] == "model" and value["state"] == "reserved"]
    assert unknown and all(after["calls"][key]["state"] == "uncertain" for key in unknown)
    assert set(before["calls"]) <= set(after["calls"]) and before["deadline"] == after["deadline"]
    assert process["sessionPrefixPreserved"] and process["manualResumeCalls"] == 0
    return {"passed": True, "verifiedCase": "V1-08", "scope": "real Pod-tool interruption plus real SIGKILL during model response",
            "sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (pod_path, process_path)],
            "interruptedToolNodeId": partial[0]["nodeId"], "interruptedToolAttemptId": first,
            "visibleToolCallIds": list(calls), "visibleCallsPaired": True, "interruptedModelReservationIds": unknown,
            "podCaseModelSlots": 4, "podCaseToolSlots": 3, "modelCaseOriginalDeadlinePreserved": True,
            "journalSha256": hashlib.sha256((root / "agent-control/tasks.jsonl").read_bytes()).hexdigest()}


def audit_cleanup(terminal_path: Path, file_path: Path) -> dict:
    terminal = json.loads(terminal_path.read_text())
    file = json.loads(file_path.read_text())
    assert terminal["passed"] and file["passed"]
    root = retained_root(terminal)
    assert {s["expectedStatus"] for s in terminal["scenarios"]} == {"succeeded", "failed", "limit_exceeded", "cancelled"}
    observations = []
    for scenario in terminal["scenarios"]:
        state = scenario["state"]
        workspace = state["workspaceId"]
        assert re.fullmatch(r"workspace-[a-f0-9]{32}", workspace)
        metadata = scenario["workspace"]
        assert metadata["phase"] == "released" and metadata["owner"]["stopConfirmed"]
        assert metadata["owner"]["taskId"] == state["taskId"]
        directory = "/export/workspaces/" + workspace
        mode = checked(["docker", "exec", "--user", "65532:65532", "sandboxd-workspace-nfs-1", "stat", "-c", "%u:%g:%a", directory]).strip()
        assert mode == "65532:65532:700"
        claim = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo", "get", "pvc", workspace, "-o", "json"]))
        volume = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "get", "pv", claim["spec"]["volumeName"], "-o", "json"]))
        assert volume["spec"]["persistentVolumeReclaimPolicy"] == "Retain"
        assert volume["spec"]["nfs"]["path"] == "/workspaces/" + workspace
        assert (root / "agent-control/sessions" / (state["sessionId"] + ".jsonl")).exists()
        observations.append({"taskId": state["taskId"], "status": state["status"], "workspaceId": workspace,
                             "directoryOwnerMode": mode, "volume": volume["metadata"]["name"], "retained": True,
                             "stoppedPodUid": metadata["owner"]["podUid"]})
    pods = json.loads(checked(["kubectl", "--context", "kind-sandboxd", "-n", "sandboxd-demo", "get", "pods", "-o", "json"]))["items"]
    assert not ({p["metadata"]["uid"] for p in pods} & {o["stoppedPodUid"] for o in observations})
    assert file["serverFileSha256AfterRelease"] == file["expectedSha256"]
    return {"passed": True, "verifiedCase": "V1-12", "scope": "real normal/cancelled/failed/limit cleanup, retained NFS directories/PV/PVC/control logs",
            "sources": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (terminal_path, file_path)],
            "observations": observations, "normalReportHashAfterRelease": file["serverFileSha256AfterRelease"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pod-report", type=Path, required=True)
    parser.add_argument("--process-report", type=Path, required=True)
    parser.add_argument("--terminal-report", type=Path)
    parser.add_argument("--file-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [asyncio.run(audit_interruption(args.pod_report, args.process_report))]
    if args.terminal_report is not None:
        if args.file_report is None:
            parser.error("terminal cleanup audit requires --file-report")
        results.append(audit_cleanup(args.terminal_report, args.file_report))
    report = {"mode": "retained-real-service-evidence-audit", "timestamp": datetime.now(timezone.utc).isoformat(),
              "externalModelCalls": 0, "passed": True, "results": results}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": True, "cases": [r["verifiedCase"] for r in results], "output": str(args.output)}))


if __name__ == "__main__":
    main()
