"""文档43十项V2的原始证据审计；不启动服务、不用汇总分替代条件。"""
import argparse
import hashlib
import json
from pathlib import Path
import re

from workspace_gc_joint_audit import journal

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "docs/evidence"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(output):
    assert not output.exists()
    inputs, controls, requirements = {}, {}, []
    progress = json.loads((EVIDENCE/"phase9-v2-progress.json").read_text())
    for row in progress["evidence"]:
        assert sha(REPO/row["path"]) == row["sha256"], row["path"]

    def load(name, *, passed=True):
        path = EVIDENCE/name; data = json.loads(path.read_text())
        inputs[str(path.relative_to(REPO))] = sha(path)
        if passed: assert data["passed"] is True, name
        assert data.get("externalModelCalls", 0) == 0, name
        if "controlRoot" in data:
            root = Path(data["controlRoot"])
            assert root == root.resolve() and root.is_relative_to(Path.home()/".local/share/sandboxd")
            for name, go in (("go-control/workspaces.jsonl", True), ("agent-control/checkpoints/checkpoints.jsonl", False), ("agent-control/tasks.jsonl", False)):
                path = root/name
                if path.exists() and str(path) not in controls:
                    rows = journal(path, go=go)
                    controls[str(path)] = {"records": len(rows), "sha256": sha(path)}
        return data

    def verified(number, scope, sources, checks):
        requirements.append({"id": f"V2-{number:02}", "status": "verified", "scope": scope, "sources": sources, "checks": checks})

    name = "phase9-v2-backup-component-v3.json"; component = load(name)
    operations = component["operations"]
    assert all(op["runtimeType"] == "io.containerd.runsc.v1" and op["runAsUser"] == 65532 and op["exitCode"] == 0 for op in operations)
    backups = [op for op in operations if op["operation"] == "backup"]
    restores = [op for op in operations if op["operation"] == "restore"]
    assert len(backups) == 3 and len(restores) == 2
    assert backups[0]["result"]["snapshotId"] == backups[1]["result"]["snapshotId"]
    assert backups[0]["result"]["manifest"] == backups[1]["result"]["manifest"]
    snapshots = {op["result"]["snapshotId"]: op["result"]["manifest"] for op in backups}
    assert len(snapshots) == 2 and all(re.fullmatch("[a-f0-9]{64}", k) for k in snapshots)
    assert len({op["workspaceId"] for op in restores}) == 2
    for op in restores:
        assert op["result"]["manifest"] == snapshots[op["result"]["snapshotId"]]
        assert all(f["mode"] == 0o600 and f["bytes"] > 0 and re.fullmatch("[a-f0-9]{64}", f["sha256"]) for f in op["result"]["manifest"]["files"])
    startup = load("phase9-v2-deployment-entry.json")
    load("phase9-v2-deployment-audit.json")
    assert len(startup["jointCheckpoints"]) == 3 and all(cp["status"] == "COMMITTED" for cp in startup["jointCheckpoints"])
    verified(1, "真实文件快照/恢复与权限、大小、hash元数据；加上当前owned联合保存", [name, "phase9-v2-deployment-entry.json", "phase9-v2-deployment-audit.json"],
             ["完整64位snapshot ID", "两份恢复manifest与原快照完全一致", "实际runsc/nonroot", "当前部署三份Session/文件联合COMMITTED"])
    first = {f["path"]: f for f in snapshots[component["firstSnapshotId"]]["files"]}
    second = {f["path"]: f for f in snapshots[component["secondSnapshotId"]]["files"]}
    assert set(second)-set(first) == {"added.txt"} and set(first)-set(second) == {"remove-synthetic.txt"}
    assert first["report.md"]["sha256"] != second["report.md"]["sha256"]
    assert component["versionedNewModifiedDeletedFilesVerified"] and component["restoredToDistinctEmptyWorkspaces"]
    verified(2, "合成新增/修改/管理员删除后的两版真实恢复；不是新增模型删除工具", [name],
             ["新增added.txt", "删除remove-synthetic.txt", "report.md内容变化", "两新空目标恢复清单分别相等、无另一版残留"])

    branches = load("phase9-v2-restore-branches-component.json")
    branch_audit = load("phase9-v2-restore-branches-audit.json")
    assert sha(EVIDENCE/"phase9-v2-restore-branches-component.json") == branch_audit["reportSha256"]
    assert len(branches["cases"]) == len(branch_audit["checks"]) == 2
    assert len({r["workspaceId"] for r in branches["cases"]}) == 2 and len({r["branchId"] for r in branches["cases"]}) == 2
    for row, check in zip(branches["cases"], branch_audit["checks"]):
        assert row["passed"] and row["sourceTaskUnchanged"] and row["sourceBranchHeadUnchanged"] and row["sourcePrefixPreserved"]
        assert check["pythonStates"] == ["RESERVED", "FILES_READY", "ATTACHED"] and check["goProofBeforeReleaseBeforeClaim"]
        assert row["checkpoints"][-1]["parentCheckpointId"] == branches["sourceCheckpointId"] and row["checkpoints"][-1]["revision"] == 1
    assert branches["cases"][0]["branchFilePresent"] and not branches["cases"][1]["branchFilePresent"] and branches["cases"][1]["siblingMarkerAbsent"]
    verified(3, "真实Agent ASGI/Go TCP两条历史分支，不冒称Agent TCP或进程内存恢复", ["phase9-v2-restore-branches-component.json", "phase9-v2-restore-branches-audit.json"],
             ["两独立workspace/branch", "Session精确父链及来源CP", "首份parent/source与revision1", "兄弟文件和上下文隔离", "原Task/head/文件保留"])

    cuts = load("phase9-v2-joint-six-cut-summary.json")
    assert {r["phase"] for r in cuts["realCases"]} == {"reserved", "preparing", "before-data-ready", "data-ready", "before-commit", "committed"}
    cut_sources = []
    for row in cuts["realCases"]:
        path = REPO/row["path"]; assert sha(path) == row["sha256"]
        r = load(path.name); cut_sources.append(path.name)
        assert r["kill"]["returnCode"] == -9 and r["kill"]["osStopConfirmed"] and r["manualResumeCalls"] == 0
        before, after = r["stateBeforeKill"], r["stateAfterRestart"]
        for key in ("taskId", "sessionId", "branchId", "workspaceId", "deadline", "limits", "timing", "provider"):
            assert before[key] == after[key], (row["phase"], key)
        at, recovered = r.get("checkpointAtRestart"), r.get("checkpointAfterRestart")
        if row["phase"] == "committed":
            assert at["status"] == recovered["status"] == "COMMITTED" and at["checkpointId"] == recovered["checkpointId"]
        elif row["phase"] != "reserved":
            assert at["status"] in {"PREPARING", "DATA_READY"} and recovered["status"] == "FAILED"
        else:
            assert at is None and recovered is None
        assert r["finalCheckpoint"]["status"] == "COMMITTED" and r["finalCheckpoint"]["revision"] == row["finalRevision"]
        assert r["sessionPrefixPreserved"]
    verified(4, "六个真实Agent SIGKILL切点，Go/存储保持在线；实际持久日志重读", ["phase9-v2-joint-six-cut-summary.json", *cut_sources],
             ["未提交点不能成为恢复点", "COMMITTED保留", "同Task/预算/时钟", "重开JSONL校验与head/revision一致"])

    storage = load("phase9-v2-storage-failure-matrix.json")
    assert {r["case"] for r in storage["cases"]} == {"minio-unavailable-and-timeout", "repository-lock", "partial-file"}
    storage_sources = []
    for row in storage["cases"]:
        path = EVIDENCE/row["report"]; assert sha(path) == row["sha256"]
        r = load(path.name); storage_sources.append(path.name)
        assert r["preservedCheckpoint"]["status"] == "COMMITTED" and r["preservedCheckpoint"]["revision"] == 1
        assert r["failedCheckpoint"]["status"] == "FAILED" and r["failedCheckpoint"]["data"] is None
        assert row["waitHttpStatus"] in {503, 504} and row["abortAndFinishConfirmed"]
        assert len(r["attempts"]) == 1
    verified(5, "真实MinIO不可用/Wait超时、排他锁exit11、不可读文件exit3；失败夹具另存排除", ["phase9-v2-storage-failure-matrix.json", *storage_sources],
             ["head保持revision1", "下一点FAILED且无DATA_READY", "abort/finish确认", "失败与孤立snapshot保留，不当成功"])

    refusals = load("phase9-v2-acceptance-refusals.json")
    refusal_audit = load("phase9-v2-acceptance-audit.json")
    assert sha(EVIDENCE/"phase9-v2-acceptance-refusals.json") == refusal_audit["sourceSha256"]
    assert refusals["saveRetry"]["sameResponse"] and len(refusals["saveRetry"]["repeatCalls"]) == 1 and refusals["saveRetry"]["repeatCalls"][0]["action"] == "finish"
    assert [r["revision"] for r in refusals["sourceCheckpoints"]] == [1, 2]
    verified(6, "同一真实Coordinator.save请求立即重试；后端原组件亦重复相同checkpoint请求", ["phase9-v2-acceptance-refusals.json", "phase9-v2-acceptance-audit.json", name],
             ["checkpoint/snapshot/revision相同", "仅幂等Finish", "Go/CP日志未追加", "每点唯一COMMITTED", "仓库未产生重复snapshot"])
    assert {r["kind"] for r in refusals["cases"]} == {"snapshot-missing", "manifest-mismatch", "image-id-mismatch"}
    for case in refusals["cases"]:
        assert case["passed"] and case["claimStatus"] == 409
        if case["kind"] != "image-id-mismatch": assert case["targetEmpty"] and case["waitStatus"] == 503 and case["finishStatus"] == 409
        else: assert case["fileToolStatus"] == 409 and case["actualImageId"] != case["expectedImageId"] and case["writerStopped"]
    assert refusals["sessionReferenceRefusal"]["statusCode"] == 409 and refusals["sessionReferenceRefusal"]["originalRestored"]
    assert refusals["forgotten"]["restoreStatus"] == 409 and refusals["forgotten"]["historicalMessagesStatus"] == 200
    verified(7, "真实Go缺失snapshot/错误manifest/实际CRI与期望imageID不符，Agent引用校验失败与遗忘拒绝；未测试S3 pack位翻转", ["phase9-v2-acceptance-refusals.json", "phase9-v2-acceptance-audit.json"],
             ["缺失和错误manifest目标仍为空/不可认领", "镜像不符拒绝绑定及文件工具、旧写入者停止", "坏Session引用409且原件恢复", "遗忘409/历史200", "无邻近或latest替代"])
    assert refusals["activeSourceRefusal"]["agentStatus"] == refusals["activeSourceRefusal"]["goStatus"] == 409
    verified(8, "活动Session/原目录拒绝，加两新空历史目标及精确会话/文件对应", ["phase9-v2-acceptance-refusals.json", "phase9-v2-restore-branches-component.json", "phase9-v2-restore-branches-audit.json"],
             ["活动来源Agent/Go均409", "原Task/CP/Go前缀和文件未改", "显式恢复创建独立workspace", "同一来源完整Session/文件，不覆盖原目录"])

    gc = load("phase9-v2-owned-gc-joint.json"); gc_audit = load("phase9-v2-owned-gc-joint-audit.json")
    assert sha(EVIDENCE/"phase9-v2-owned-gc-joint.json") == gc_audit["sourceReportSha256"]
    assert gc["originalTaskAndSessionUnchanged"] and len(gc["forgottenCheckpointIds"]) == 2
    assert gc_audit["freshDedicatedPrefixVerified"] and gc_audit["nativeRepositoryCryptoIdMatches"] and len(gc_audit["actualRetainedSnapshotIds"]) == 4
    assert len(gc_audit["protocol"]) == 2 and all(r["preparedBeforeSubmitAndForgottenBeforeRelease"] for r in gc_audit["protocol"])
    load("phase9-v2-owned-gc-joint-preview-audit.json")
    verified(9, "新专属仓库实际受控forget/prune、保护拒绝、管理及Go中断、锁冲突与保留文件恢复；不删除旧数据", ["phase9-v2-owned-gc-joint.json", "phase9-v2-owned-gc-joint-audit.json", "phase9-v2-owned-gc-joint-preview-audit.json"],
             ["head/pin/保留恢复源保护", "PREPARED先于提交、FORGOTTEN先于放行", "同GC/job对账与精确目标重试", "四保留snapshot/read-data", "只读预览文件字节/ctime/mode不变"])

    continued = load("phase9-v2-restore-cut-after-tool-v2.json")
    load("phase9-v2-restore-fault-four-case-audit-v2.json")
    before, after = continued["before"], continued["after"]
    assert continued["kill"]["returnCode"] == -9 and continued["kill"]["osStopConfirmed"]
    for key in ("taskId", "sessionId", "branchId", "workspaceId", "deadline", "limits", "timing", "provider"):
        assert before[key] == after[key], key
    assert set(before["calls"]) <= set(after["calls"])
    assert sum(c.get("operation") == "write_file" for c in after["calls"].values()) == 1
    assert continued["restoreSubmissions"] == 1 and continued["restoreAfter"]["status"] == "ATTACHED"
    assert len(after["attempts"]) == 2 and all(a["ownerStopped"] for a in after["attempts"])
    assert continued["finalCheckpoint"]["status"] == "COMMITTED" and continued["originalControlUnchanged"]
    verified(10, "历史恢复后已执行CAS写，再真实Agent TCP SIGKILL接续；不是新Task重发预算", ["phase9-v2-restore-cut-after-tool-v2.json", "phase9-v2-restore-fault-four-case-audit-v2.json"],
             ["原调用槽/预算/时钟不回滚", "CAS写仅1次、restore仅提交1次", "ATTACHED及来源可追溯", "两个attempt均停止", "最新NFS文件未回退旧快照"])

    assert len(requirements) == 10 and all(r["status"] == "verified" for r in requirements)
    result = {"passed": True, "mode": "offline-v2-requirement-evidence-audit", "requirements": requirements,
              "publicEvidenceSha256": inputs, "retainedControlJournals": controls,
              "priorEvidenceHashesVerified": len(progress["evidence"]), "matrixRequirementsVerified": 10,
              "scope": "Document43 ten-item V2 evidence coverage plus current retained journal checksums; no new service experiment. Full installation/regression/documentation/PR delivery acceptance audited separately.",
              "externalModelCalls": 0, "formalV2AcceptanceComplete": False}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.output)
    print(json.dumps({"passed": result["passed"], "matrixRequirementsVerified": result["matrixRequirementsVerified"], "retainedJournals": len(result["retainedControlJournals"])}))
