"""真实Go/gVisor/NFS/restic/MinIO + TaskStore/Runner联合保存；LLM为Replay。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import socket
import time
from pathlib import Path
from uuid import uuid4

from .app.models import AlertEvent
from .app.model_gateway import ReplayModelGateway
from .app.runner import AgentRunner
from .app.store import TaskStore
from .app.runtime.checkpoints import CheckpointLedger
from .app.runtime.checkpoint_coordinator import CheckpointCoordinator
from .app.runtime.session_refs import SessionReferences
from .app.redaction import public_error
from .workspace_demo import REPO, ObservedClient, checked, ready, start_go, stop_go


class CheckpointObservedClient(ObservedClient):
    def __init__(self, token):
        super().__init__(token)
        self.maintenance = []

    async def workspace_backup(self, action, binding, cp, job, *, timeout=90):
        result = await super().workspace_backup(action,binding,cp,job,timeout=timeout)
        if action == "wait" and result.status_code == 200:
            b = result.body["barrier"]
            pod = json.loads(checked(["kubectl","--context","kind-sandboxd","-n","sandboxd-storage","get","pod",b["podName"],"-o","json"]))
            assert pod["metadata"]["uid"] == b["podUid"]
            sandboxes=json.loads(checked(["docker","exec","sandboxd-control-plane","crictl","pods","-o","json"]))["items"]
            sandbox=next(s for s in sandboxes if s["metadata"]["uid"]==b["podUid"])
            # 原始inspect含解析后的Secret env，只提取runtimeType，不保存原文。
            inspected=json.loads(checked(["docker","exec","sandboxd-control-plane","crictl","inspectp",sandbox["id"]]))
            runtime=inspected["info"]["runtimeType"]
            assert runtime=="io.containerd.runsc.v1"
            self.maintenance.append({"checkpointId":cp,"podUid":b["podUid"],"podName":b["podName"],
                                     "runtimeType":runtime,"imageId":result.body["result"]["maintenanceImageId"],
                                     "snapshotId":result.body["result"]["snapshotId"]})
        return result


async def run(output: Path, secret: str, endpoint: str):
    with socket.socket() as probe: probe.bind(("127.0.0.1",10880))
    assert checked(["kubectl","config","current-context"]).strip()=="kind-sandboxd"
    nfs=json.loads(checked(["docker","inspect","sandboxd-workspace-nfs-1"]))[0]
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    server=nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    root=Path.home()/".local/share/sandboxd/workspace-checkpoint-demo"/uuid4().hex
    root.mkdir(mode=0o700,parents=True)
    script=root/"replay.json"
    content="synthetic joint checkpoint report\n"
    script.write_text(json.dumps({"kind":"deterministic-policy-case","model":"joint-checkpoint-replay","responses":[
        {"toolCalls":[{"id":"write-report","name":"write_file","args":{"path":"report.md","content":content}}]},
        {"toolCalls":[{"id":"read-report","name":"read_file","args":{"path":"report.md"}}]},
        {"content":'{"summary":"Synthetic report verified","severity":"warning","recommendation":"review references"}'}]}))
    script.chmod(0o600)
    process,log,token=start_go(root,REPO/".cache/workspace-sandboxd-v2",server,backup_secret=secret,backup_minio_ip=endpoint)
    client=CheckpointObservedClient(token)
    store=TaskStore(root/"legacy-traces",recovery_dir=root/"agent-control")
    refs=SessionReferences(root/"agent-control/checkpoints")
    ledger=CheckpointLedger(root/"agent-control/checkpoints/checkpoints.jsonl",refs)
    coordinator=CheckpointCoordinator(client,ledger,refs,"repo-component")
    worker=None
    report={"mode":"real-agent-replay-go-gvisor-nfs-restic-minio-joint-checkpoint","passed":False,
            "externalModelCalls":0,"controlRoot":str(root),"scope":"normal complete tool groups and final diagnosis only; fault/restore/GC acceptance pending"}
    started=time.perf_counter()
    try:
        await ready(process)
        runner=AgentRunner(object(),client,ReplayModelGateway(script),checkpoints=coordinator)
        task=await store.enqueue(AlertEvent(labels={"cluster":"local-kind","namespace":"sandboxd-target"},annotations={"summary":"synthetic joint checkpoint demo"}))
        report["taskId"]=task.task_id
        worker=asyncio.create_task(store.worker(runner))
        async with asyncio.timeout(300):
            while True:
                task=await store.get(task.task_id)
                if task.status in {"succeeded","failed","interrupted","cancelled","limit_exceeded"}: break
                await asyncio.sleep(.1)
        report["taskStatus"]=task.status;report["error"]=task.error
        assert task.status=="succeeded",task.error
        state=store._ledger.get(task.task_id)
        head=ledger.head(task.session_id,state["branchId"])
        assert head["revision"]==3 and not ledger.pending()
        assert sum(c["kind"]=="model" for c in state["calls"].values())==3
        assert sum(c["kind"]=="tool" for c in state["calls"].values())==2
        chain=[];current=head
        while current:
            r,prefix=ledger.restore_point(current["checkpointId"])
            assert r["identity"]["taskId"]==task.task_id and r["identity"]["workspaceId"]==task.workspace_id
            chain.append({"checkpointId":r["checkpointId"],"revision":r["revision"],"nodeId":r["identity"]["sessionNodeId"],
                          "snapshotId":r["data"]["snapshotId"],"sessionRef":r["identity"]["sessionRef"],"messageCount":len(prefix["nodes"])})
            current=ledger.get(r["parentCheckpointId"]) if r["parentCheckpointId"] else None
        final=refs.read(head["identity"]["sessionRef"])["nodes"][-1]["message"]
        assert json.loads(final["content"])["runtimeSource"]=="final-diagnosis"
        assert final.get("runtimeGenerated")=="final-diagnosis"
        assert json.loads(final["content"])["runtimeTaskId"]==task.task_id
        assert state["attempts"][-1]["ownerStopped"]
        expected=hashlib.sha256(content.encode()).hexdigest()
        actual=checked(["docker","exec","--user","65532:65532","sandboxd-workspace-nfs-1","sha256sum","/export/workspaces/"+task.workspace_id+"/report.md"]).split()[0]
        assert actual==expected
        assert len(client.maintenance)==3
        report.update(passed=True,checkpoints=chain,maintenance=client.maintenance,businessPods=client.observations,
                      workspaceId=task.workspace_id,attempts=state["attempts"],modelReservations=3,toolReservations=2,
                      deadline=state["deadline"],timing=state["timing"],fileSha256=actual,
                      finalDiagnosis=json.loads(final["content"]),elapsedMs=(time.perf_counter()-started)*1000)
    except BaseException as error:
        report["error"]=public_error(error)
        report["maintenance"]=client.maintenance
        raise
    finally:
        if worker:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        coordinator.close();store.close();await client.close();stop_go(process,log)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--secret-name",required=True);parser.add_argument("--minio-ip",required=True)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError("preserve existing evidence; choose a new report path")
    asyncio.run(run(args.output,args.secret_name,args.minio_ip))
