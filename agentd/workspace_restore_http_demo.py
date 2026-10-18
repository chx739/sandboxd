"""Python固定客户端→真实Go HTTP→gVisor/NFS/restic/MinIO；不是Session分支验收。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import socket
from uuid import uuid4

import httpx

from .app.clients import SandboxdClient
from .app.redaction import public_error
from .app.runtime.recovery import new_id
from .app.tools.remote_files import WorkspaceBinding
from .workspace_checkpoint_fault_demo import observe_maintenance
from .workspace_demo import REPO, checked, observe_pod, ready, start_go, stop_go


async def run(output: Path, source_report: Path, secret: str, endpoint: str):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);probe.bind(("127.0.0.1",10880))
    assert checked(["kubectl","config","current-context"]).strip()=="kind-sandboxd"
    nfs=json.loads(checked(["docker","inspect","sandboxd-workspace-nfs-1"]))[0]
    assert nfs["State"]["Running"] and not nfs["State"]["Paused"]
    assert nfs["Config"]["Labels"]["com.docker.compose.project"]=="sandboxd-workspace"
    server=nfs["NetworkSettings"]["Networks"]["kind"]["IPAddress"]
    evidence=json.loads(source_report.read_text());assert evidence["passed"]
    cp=evidence["checkpointAtRestart"];assert cp["status"]=="COMMITTED"
    data,identity=cp["data"],cp["identity"]
    source={"snapshotId":data["snapshotId"],"manifestSha256":data["manifestSha256"],"repositoryCryptoId":data["repositoryCryptoId"],"imageRef":identity["imageRef"],"imageId":identity["imageId"]}
    root=Path.home()/".local/share/sandboxd/workspace-restore-http"/uuid4().hex
    root.mkdir(mode=0o700,parents=True)
    binary=REPO/".cache/workspace-sandboxd-restore-http"
    process,log,token=start_go(root,binary,server,backup_secret=secret,backup_minio_ip=endpoint,backup_image="sandboxd-restic:0.18.1-v2")
    client=SandboxdClient("http://127.0.0.1:10880",token)
    targets=[];cases=[]
    report={"passed":False,"mode":"real-python-client-go-http-gvisor-nfs-restic-minio-restore","externalModelCalls":0,
            "controlRoot":str(root),"sourceCheckpointId":cp["checkpointId"],"cases":cases,
            "binarySha256":hashlib.sha256(binary.read_bytes()).hexdigest(),"scope":"actual limited HTTP/client file restore only; no Session branch, model or GC"}
    async def require(result,code=200):
        assert result.status_code==code,(result.status_code,result.body)
        return result.body
    try:
        await ready(process)
        async with httpx.AsyncClient() as anonymous:
            response=await anonymous.get("http://127.0.0.1:10880/api/v1/workspaces/workspace-"+"1"*32+"/restore")
            assert response.status_code==401
        report["anonymousRejected"]=True
        for bad in (False,True):
            target={"workspaceId":new_id("workspace"),"sessionId":identity["sessionId"],"branchId":new_id("branch")}
            selected=dict(source)
            if bad:selected["repositoryCryptoId"]="0"*64 if source["repositoryCryptoId"]!="0"*64 else "1"*64
            job="restore-"+uuid4().hex
            targets.append((target,job,selected))
            await require(await client.prepare_workspace(target["workspaceId"],target["sessionId"],target["branchId"]),201)
            # 直接绕过Python输入校验，Go仍拒绝执行配置/成功自报。
            payload={"sessionId":target["sessionId"],"branchId":target["branchId"],"checkpointId":cp["checkpointId"],"jobId":job,"source":selected,"success":True}
            refused=await client._client.post("http://127.0.0.1:10880/api/v1/workspaces/"+target["workspaceId"]+"/restore/reserve",headers=client._headers,json=payload)
            assert refused.status_code==400
            await require(await client.workspace_restore("reserve",target,cp["checkpointId"],job,selected))
            await require(await client.claim_workspace(target["workspaceId"],"task-restored-http","attempt-a"),409)
            wrong={**target,"branchId":new_id("branch")}
            await require(await client.workspace_restore("abort",wrong,cp["checkpointId"],job,selected),409)
            await require(await client.workspace_restore("start",target,cp["checkpointId"],job,selected))
            waited=await client.workspace_restore("wait",target,cp["checkpointId"],job,selected)
            barrier=await require(await client.get_workspace_restore(target["workspaceId"]))
            maintenance=observe_maintenance(barrier)
            assert maintenance and maintenance["actualRuntimeType"]=="io.containerd.runsc.v1"
            if bad:
                await require(waited,503)
                await require(await client.workspace_restore("finish",target,cp["checkpointId"],job,selected),409)
                await require(await client.workspace_restore("abort",target,cp["checkpointId"],job,selected))
                await require(await client.workspace_restore("fail",target,cp["checkpointId"],job,selected))
                await require(await client.claim_workspace(target["workspaceId"],"task-restored-http","attempt-a"),409)
                contents=checked(["docker","exec","--user","65532:65532","sandboxd-workspace-nfs-1","ls","-A","/export/workspaces/"+target["workspaceId"]])
                assert not contents.strip()
                cases.append({"kind":"wrong-expected-repository","passed":True,"target":target,"maintenance":maintenance,"targetEmpty":True,"claimRejected":True,"callerSuccessRejected":True})
                continue
            result=(await require(waited))["result"]
            assert all(result[k]==selected[k] for k in ("snapshotId","manifestSha256","repositoryCryptoId"))
            finished=await require(await client.workspace_restore("finish",target,cp["checkpointId"],job,selected))
            assert finished["barrier"]["phase"]=="released" and finished["barrier"]["restoreVerified"]
            await require(await client.workspace_restore("finish",target,cp["checkpointId"],job,selected))
            record=await require(await client.claim_workspace(target["workspaceId"],"task-restored-http","attempt-a"),201)
            binding=WorkspaceBinding.from_record(record)
            business=observe_pod(record)
            restored=await require(await client.workspace_files(binding.as_request(),{"operation":"read_file","path":"report.md"}))
            assert restored["sha256"]==evidence["fileSha256"] and hashlib.sha256(restored["content"].encode()).hexdigest()==restored["sha256"]
            assert record["imageId"]==source["imageId"]
            server_hash=checked(["docker","exec","--user","65532:65532","sandboxd-workspace-nfs-1","sha256sum","/export/workspaces/"+target["workspaceId"]+"/report.md"]).split()[0]
            assert server_hash==restored["sha256"]
            await require(await client.release_workspace(binding.as_request()))
            cases.append({"kind":"normal","passed":True,"target":target,"maintenance":maintenance,"business":business,"fileSha256":server_hash,"restoreResult":result,"finishIdempotent":True})
        report["passed"]=True
    except BaseException as error:
        report["failure"]={"type":type(error).__name__,"message":public_error(error)}
        raise
    finally:
        for target,job,selected in reversed(targets):
            try:
                state=await client.get_workspace_restore(target["workspaceId"])
                if state.status_code==200 and state.body["phase"]!="released":
                    aborted=await require(await client.workspace_restore("abort",target,cp["checkpointId"],job,selected))
                    action="finish" if aborted["barrier"].get("restoreVerified") else "fail"
                    await require(await client.workspace_restore(action,target,cp["checkpointId"],job,selected))
                record=await client.get_workspace(target["workspaceId"])
                if record.status_code==200 and record.body.get("owner",{}).get("taskId")=="task-restored-http" and record.body["phase"]!="released":
                    await require(await client.release_workspace(WorkspaceBinding.from_record(record.body).as_request()))
            except Exception as error:
                report["cleanupError"]=public_error(error);report["passed"]=False
        await client.close();stop_go(process,log)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--source-report",type=Path,default=REPO/"docs/evidence/phase9-v2-agent-cut-committed.json")
    parser.add_argument("--secret-name",required=True);parser.add_argument("--minio-ip",required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError("preserve existing evidence; use a new output path")
    result=asyncio.run(run(args.output,args.source_report,args.secret_name,args.minio_ip))
    print(json.dumps({"passed":result["passed"],"scope":result["scope"]}))
    if not result["passed"]:raise SystemExit(1)
