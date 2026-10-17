"""真实Agent进程检查点切点：仅本夹具子进程暂停，父进程SIGKILL并原目录重启。

不在生产Runtime增加故障开关，不改写账本/时钟；Session/文件/快照全部保留。
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import time
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.model_gateway import ReplayModelGateway
from .app.redaction import public_error
from .app.runner import AgentRunner
from .app.runtime.checkpoint_coordinator import CheckpointCoordinator
from .app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from .app.runtime.execution import RecoveryAttempt
from .app.runtime.recovery import RecoveryLedger
from .app.runtime.session import _branchable_flags
from .app.runtime.session_refs import SessionReferences
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go
from .workspace_process_demo import API, TERMINAL, agent_ready, entries, start_agent, state_for, stop_agent

PHASES = ("reserved", "preparing", "data-ready", "before-commit", "committed")
CONTENT = "synthetic joint checkpoint process recovery\n"


def native_root(root: Path) -> Path:
    expected=Path.home()/".local/share/sandboxd/workspace-checkpoint-fault"
    if root.resolve().parent != expected.resolve() or not re.fullmatch(r"[a-f0-9]{32}",root.name):
        raise ValueError("requires this fixture's retained private native control directory")
    return root.resolve()


def install_cutpoint(root: Path, phase: str) -> None:
    """仅用于自建子进程：在真实fsync前/后暂停，不制造伪造状态或延长预算。"""
    root=native_root(root)
    if phase not in PHASES: raise ValueError("unknown fixed cut point")
    marker=root/"cut-point.json"
    if marker.exists(): raise ValueError("do not reuse an existing cut point")
    triggered=False

    def pause(ledger, record):
        nonlocal triggered
        if triggered:return
        triggered=True
        head=ledger.head(record["identity"]["sessionId"],record["identity"]["branchId"])
        payload={"phase":phase,"pid":os.getpid(),"checkpointId":record["checkpointId"],
                 "status":record["status"],"headCheckpointId":head["checkpointId"] if head else None,
                 "timestamp":datetime.now(timezone.utc).isoformat()}
        temporary=root/"cut-point.tmp"
        with temporary.open("x") as stream:
            os.chmod(temporary,0o600)
            json.dump(payload,stream);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,marker)
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
        print(json.dumps({"stage":"checkpoint-cutpoint-persisted",**payload}),flush=True)
        os.kill(os.getpid(),signal.SIGSTOP)

    if phase=="reserved":
        original_call=CheckpointCoordinator._call
        async def reserved(coordinator,action,binding,cp,job,recovery,**kwargs):
            result=await original_call(coordinator,action,binding,cp,job,recovery,**kwargs)
            if action=="reserve":
                state=recovery.snapshot()
                pause(coordinator.ledger,{"checkpointId":cp,"status":"RESERVED","identity":state})
            return result
        CheckpointCoordinator._call=reserved
        return

    method={"preparing":"prepare","data-ready":"data_ready","before-commit":"commit","committed":"commit"}[phase]
    original=getattr(CheckpointLedger,method)
    def wrapped(ledger,*args,**kwargs):
        if phase=="before-commit":pause(ledger,ledger.get(args[0]))
        record=original(ledger,*args,**kwargs)
        if phase!="before-commit":pause(ledger,record)
        return record
    setattr(CheckpointLedger,method,wrapped)


def observe_maintenance(barrier: dict) -> dict | None:
    if not barrier.get("podUid"):return None
    pod=json.loads(checked(["kubectl","--context","kind-sandboxd","-n","sandboxd-storage","get","pod",barrier["podName"],"-o","json"]))
    assert pod["metadata"]["uid"]==barrier["podUid"]
    sandboxes=json.loads(checked(["docker","exec","sandboxd-control-plane","crictl","pods","-o","json"]))["items"]
    sandbox=next(s for s in sandboxes if s["metadata"]["uid"]==barrier["podUid"])
    # inspectp含解析后的Secret环境，只读取白名单runtimeType，不输出/保存原对象。
    inspected=json.loads(checked(["docker","exec","sandboxd-control-plane","crictl","inspectp",sandbox["id"]]))
    runtime=inspected["info"]["runtimeType"]
    assert runtime=="io.containerd.runsc.v1"
    return {"podUid":barrier["podUid"],"podName":barrier["podName"],"actualRuntimeType":runtime,
            "imageId":pod["status"]["containerStatuses"][0]["imageID"]}


def require_idle_ports(ports=(10880,18090)) -> None:
    for port in ports:
        with socket.socket() as probe:
            # 串行上一轮服务已退出后可能仍有TIME_WAIT；仍拒绝实际监听者。
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            probe.bind(("127.0.0.1",port))


async def run(output: Path, phase: str, secret: str, endpoint: str) -> dict:
    require_idle_ports()
    assert checked(["kubectl","config","current-context"]).strip()=="kind-sandboxd"
    nodes=json.loads(checked(["kubectl","get","nodes","-o","json"]))["items"]
    assert len(nodes)==1 and nodes[0]["metadata"]["name"]=="sandboxd-control-plane"
    nfs=json.loads(checked(["docker","inspect","sandboxd-workspace-nfs-1"]))[0]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"]=="sandboxd-workspace"
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    server=nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    root=Path.home()/".local/share/sandboxd/workspace-checkpoint-fault"/uuid4().hex
    root.mkdir(mode=0o700,parents=True)
    script=root/"replay.json"
    script.write_text(json.dumps({"kind":"deterministic-policy-case","model":"checkpoint-process-replay","responses":[
        {"toolCalls":[{"id":"write-once","name":"write_file","args":{"path":"report.md","content":CONTENT}}]},
        {"toolCalls":[{"id":"read-after-restart","name":"read_file","args":{"path":"report.md"}}]},
        {"content":'{"summary":"Recovered complete checkpoint boundary","severity":"warning","recommendation":"Replay local-service evidence only"}'}]}))
    script.chmod(0o600)
    go,go_log,go_token=start_go(root,REPO/".cache/workspace-sandboxd-v2",server,backup_secret=secret,backup_minio_ip=endpoint)
    sdk=SandboxdClient("http://127.0.0.1:10880",go_token)
    api_token,alert_token=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    api=httpx.AsyncClient(base_url=API,headers={"Authorization":"Bearer "+api_token},timeout=5)
    journal=root/"agent-control/tasks.jsonl"
    cp_path=root/"agent-control/checkpoints/checkpoints.jsonl"
    processes=[];requests=[]
    report={"mode":"real-agentd-sigkill-at-joint-checkpoint","phase":phase,"passed":False,
            "externalModelCalls":0,"manualResumeCalls":0,"controlRoot":str(root),"apiRequests":requests,
            "scope":"Agent process cut point with actual Go/gVisor/NFS/restic/MinIO; not historical restore/GC or Go process crash"}

    async def request(method,path,**kwargs):
        response=await api.request(method,path,**kwargs)
        requests.append({"method":method,"path":path,"statusCode":response.status_code})
        return response

    try:
        await ready(go)
        first,log=start_agent(root,go_token,api_token,alert_token,1,checkpoint_mode="restic",checkpoint_cutpoint=phase)
        processes.append((first,log));await agent_ready(first)
        created=await request("POST","/api/v1/tasks",json={"summary":"synthetic joint checkpoint cut","labels":{"cluster":"local-kind","namespace":"sandboxd-target"}})
        assert created.status_code==202
        task_id=created.json()["taskId"];report["taskId"]=task_id
        async with asyncio.timeout(65):
            while not (root/"cut-point.json").exists():
                if first.poll() is not None:raise RuntimeError("fixture exited before requested cut point")
                await asyncio.sleep(.05)
        cut=json.loads((root/"cut-point.json").read_text())
        assert cut["pid"]==first.pid and cut["phase"]==phase
        # marker先fsync，随后SIGSTOP；等OS确认暂停再读取控制状态/执行SIGKILL。
        async with asyncio.timeout(5):
            while True:
                stopped_pid,stopped=os.waitpid(first.pid,os.WNOHANG|os.WUNTRACED)
                if stopped_pid:
                    assert os.WIFSTOPPED(stopped) and os.WSTOPSIG(stopped)==signal.SIGSTOP
                    break
                await asyncio.sleep(.01)
        before=state_for(journal,task_id);assert before and before["completeNodeId"]
        before_record=(await sdk.get_workspace(before["workspaceId"])).body
        before_pod=observe_pod(before_record)
        barrier=(await sdk.get_workspace_backup(before["workspaceId"])).body
        assert barrier["checkpointId"]==cut["checkpointId"] and barrier["phase"]!="released"
        maintenance=observe_maintenance(barrier)
        first.kill();assert first.wait(timeout=8)==-signal.SIGKILL
        killed=time.perf_counter()
        report.update(cutPoint=cut,stateBeforeKill=before,podBeforeKill=before_pod,barrierAtCut=barrier,
                      maintenanceAtCut=maintenance,kill={"signal":"SIGKILL","returnCode":first.returncode,"osStopConfirmed":True})
        refs=SessionReferences(cp_path.parent)
        ledger=CheckpointLedger(cp_path,refs)
        try:
            head=ledger.head(before["sessionId"],before["branchId"])
            if phase=="reserved":
                selected=None
                assert head is None and cut["status"]=="RESERVED"
                try:ledger.get(cut["checkpointId"])
                except CheckpointConflict:pass
                else:raise AssertionError("Reserve unexpectedly produced a Python checkpoint")
            elif phase=="committed":
                selected=ledger.get(cut["checkpointId"])
                assert selected["status"]=="COMMITTED" and head["checkpointId"]==cut["checkpointId"]
                ledger.restore_point(cut["checkpointId"])
            else:
                selected=ledger.get(cut["checkpointId"])
                assert selected["status"]==("PREPARING" if phase=="preparing" else "DATA_READY") and head is None
                try:ledger.restore_point(cut["checkpointId"])
                except CheckpointConflict:pass
                else:raise AssertionError("uncommitted checkpoint exposed as restore point")
            report["checkpointAtRestart"]=selected
        finally:ledger.close()
        session_path=root/"agent-control/sessions"/(before["sessionId"]+".jsonl")
        prefix=session_path.read_bytes()
        print(json.dumps({"stage":"joint-cut-sigkill-confirmed","phase":phase,"checkpointId":cut["checkpointId"]}),flush=True)
        second,log=start_agent(root,go_token,api_token,alert_token,2,checkpoint_mode="restic")
        processes.append((second,log));await agent_ready(second)
        report["killToAgentReadyMs"]=(time.perf_counter()-killed)*1000
        async with asyncio.timeout(85):
            while True:
                live=await request("GET","/api/v1/tasks/"+task_id);assert live.status_code==200
                if live.json()["status"] in TERMINAL:break
                await asyncio.sleep(.1)
        assert live.json()["status"]=="succeeded",live.json().get("error")
        after=state_for(journal,task_id)
        for key in ("taskId","sessionId","branchId","workspaceId","deadline","limits","timing","provider"):
            assert after[key]==before[key],key
        assert len(after["attempts"])==2 and all(a["ownerStopped"] for a in after["attempts"])
        assert all(after["calls"][k]==v for k,v in before["calls"].items())
        assert sum(c["kind"]=="model" for c in after["calls"].values())==3
        assert sum(c["operation"]=="write_file" for c in after["calls"].values())==1
        assert sum(c["operation"]=="read_file" for c in after["calls"].values())==1
        assert session_path.read_bytes().startswith(prefix)
        stop_agent(second,processes[-1][1]) # 释放控制账本锁后，再验证重建投影。
        ledger=CheckpointLedger(cp_path,refs)
        try:
            head=ledger.head(after["sessionId"],after["branchId"])
            expected=3 if phase=="committed" else 2
            assert head["revision"]==expected and not ledger.pending()
            if phase=="reserved":
                old=None
                try:ledger.get(cut["checkpointId"])
                except CheckpointConflict:pass
                else:raise AssertionError("orphan Reserve manufactured a committed checkpoint")
            else:
                old=ledger.get(cut["checkpointId"])
                assert old["status"]==("COMMITTED" if phase=="committed" else "FAILED")
            final,prefix_view=ledger.restore_point(head["checkpointId"])
            messages=[n["message"] for n in prefix_view["nodes"]]
            assert _branchable_flags(messages)[-1]
            calls=Counter(c["id"] for m in messages if m["role"]=="assistant" for c in m.get("toolCalls",[]))
            results=Counter(m["toolCallId"] for m in messages if m["role"]=="tool")
            assert calls==results and all(n==1 for n in calls.values())
            report.update(checkpointAfterRestart=old,finalCheckpoint=final,visibleToolCallIds=list(calls))
        finally:ledger.close()
        actual=checked(["docker","exec","--user","65532:65532","sandboxd-workspace-nfs-1","sha256sum","/export/workspaces/"+after["workspaceId"]+"/report.md"]).split()[0]
        assert actual==hashlib.sha256(CONTENT.encode()).hexdigest()
        final_record=(await sdk.get_workspace(after["workspaceId"])).body
        assert final_record["phase"]=="released" and final_record["owner"]["stopConfirmed"]
        assert final_record["owner"]["podUid"]!=before_record["owner"]["podUid"]
        releases=[e["data"] for e in entries(root/"go-control/workspaces.jsonl",go_format=True)
                  if e["type"]=="workspace.state" and e["data"]["phase"]=="released"]
        assert any(r["owner"]["podUid"]==before_record["owner"]["podUid"] and r["owner"]["stopConfirmed"] for r in releases)
        report.update(passed=True,stateAfterRestart=after,finalWorkspace=final_record,fileSha256=actual,
                      sessionPrefixPreserved=True,killToVerificationMs=(time.perf_counter()-killed)*1000)
    except BaseException as error:
        report["failure"]={"type":type(error).__name__,"message":public_error(error)}
        raise
    finally:
        for process,log in reversed(processes):
            if process.poll() is None:
                # 不让失败的SIGSTOP夹具等SIGTERM超时；仅本次已登记子进程。
                try:os.kill(process.pid,signal.SIGCONT)
                except ProcessLookupError:pass
            stop_agent(process,log)
        ledger=None;coordinator=None
        try:
            if journal.exists():
                ledger=RecoveryLedger(journal)
                refs=SessionReferences(cp_path.parent)
                coordinator=CheckpointCoordinator(sdk,CheckpointLedger(cp_path,refs),refs,"repo-component")
                runner=AgentRunner(object(),sdk,ReplayModelGateway(script),checkpoints=coordinator)
                for state in ledger.records():
                    if state["attempts"] and not state["attempts"][-1]["ownerStopped"]:
                        await runner.stop_attempt(RecoveryAttempt(ledger,state["taskId"],state["attempts"][-1]["attemptId"]))
        except Exception as error:
            report["cleanupError"]=public_error(error);report["passed"]=False
        finally:
            if coordinator:coordinator.close()
            if ledger:ledger.close()
        await api.aclose();await sdk.close();stop_go(go,go_log)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase",choices=PHASES)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--secret-name");parser.add_argument("--minio-ip")
    parser.add_argument("--child-phase",choices=PHASES);parser.add_argument("--child-root",type=Path)
    args=parser.parse_args()
    if args.child_phase:
        if args.child_root is None:parser.error("child root required")
        install_cutpoint(args.child_root,args.child_phase)
        import uvicorn
        from .app.main import create_app
        uvicorn.run(create_app(),host="127.0.0.1",port=18090,log_level="warning",access_log=False)
        return
    if not all((args.phase,args.output,args.secret_name,args.minio_ip)):parser.error("phase/output/secret-name/minio-ip required")
    if args.output.exists():parser.error("preserve existing report; choose another path")
    report=asyncio.run(run(args.output,args.phase,args.secret_name,args.minio_ip))
    print(json.dumps({"passed":report["passed"],"phase":args.phase,"output":str(args.output)}))
    if not report["passed"]:raise SystemExit(1)


if __name__=="__main__":main()
