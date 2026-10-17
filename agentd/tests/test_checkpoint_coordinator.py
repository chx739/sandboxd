from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agentd.app.clients import HTTPResult
from agentd.app.model_gateway import ReplayModelGateway
from agentd.app.models import AlertEvent, utc_now
from agentd.app.runner import AgentRunner
from agentd.app.store import TaskStore
from agentd.app.runtime.checkpoint_coordinator import CheckpointCoordinator
from agentd.app.runtime.checkpoints import CheckpointLedger
from agentd.app.runtime.execution import RecoveryAttempt
from agentd.app.runtime.recovery import RecoveryLedger, new_id, RecoveryConflict
from agentd.app.runtime.session import SessionJournal
from agentd.app.runtime.session_refs import SessionReferences
from agentd.app.tools.remote_files import WorkspaceExecutionError
from agentd.tests.recovery_clock import install_fixed_recovery_clock, FixedRecoveryClock
from agentd.tests.test_recovery_runner import FakeWorkspaceService


class FakeBackupService(FakeWorkspaceService):
    """合成存储身份，不冒充真实Go/restic。"""
    def __init__(self):
        super().__init__()
        self.barrier = None
        self.actions = []
        self.before_start = None
        self.on_wait = None
        self.lose_start = False
        self.unknown_stop = False

    async def claim_workspace(self, *args):
        if self.barrier and self.barrier["phase"] != "released": raise AssertionError("claim passed active backup")
        await super().claim_workspace(*args)
        self.record.update(imageRef="sandboxd-workspace:runtime-v1.5", imageId="sha256:"+"4"*64)
        self.record["owner"]["createSubmitted"] = True
        return self.result()

    async def get_workspace_backup(self, workspace):
        return HTTPResult(404,{}) if self.barrier is None else HTTPResult(200,copy.deepcopy(self.barrier))

    async def workspace_files(self,*args):
        if self.barrier and self.barrier["phase"] != "released": raise AssertionError("file RPC passed active backup")
        return await super().workspace_files(*args)

    async def workspace_backup(self,action,binding,cp,job,*,timeout=90):
        self.actions.append((action,cp,job,timeout))
        if action == "reserve":
            if self.barrier and self.barrier["phase"] != "released" and self.barrier["checkpointId"] != cp:
                return HTTPResult(409,{})
            if not self.barrier or self.barrier["phase"] == "released":
                self.barrier={"operation":"backup","access":copy.deepcopy(binding),"checkpointId":cp,"jobId":job,"phase":"reserved"}
        elif action == "start":
            if self.before_start: self.before_start(cp)
            self.barrier["phase"]="identified"
            if self.lose_start:
                self.lose_start=False
                raise httpx.ReadTimeout("synthetic start response loss")
        elif action == "wait":
            if self.on_wait: await self.on_wait(cp)
            self.barrier.update(phase="stopped",stopReason="containers-terminated")
            return HTTPResult(200,{"barrier":copy.deepcopy(self.barrier),"result":{
                "snapshotId":hashlib.sha256(cp.encode()).hexdigest(),"repositoryCryptoId":"1"*64,
                "manifestSha256":hashlib.sha256(json.dumps(self.files,sort_keys=True).encode()).hexdigest()}})
        elif action == "abort":
            if self.unknown_stop: raise httpx.ReadTimeout("synthetic termination uncertainty")
            self.barrier.update(phase="stopped",stopReason="containers-terminated")
        elif action == "finish":
            if self.barrier["phase"] not in {"stopped","released"}: raise AssertionError("premature finish")
            self.barrier["phase"]="released"
        return HTTPResult(200,{"barrier":copy.deepcopy(self.barrier)})


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        install_fixed_recovery_clock(self)
        clock=patch("agentd.app.runtime.checkpoint_coordinator.datetime",FixedRecoveryClock);clock.start();self.addCleanup(clock.stop)
        clock=patch("agentd.app.runtime.checkpoints.datetime",FixedRecoveryClock);clock.start();self.addCleanup(clock.stop)
        self.tmp=tempfile.TemporaryDirectory(dir="/tmp",prefix="sandboxd-coordinator-");self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.tasks=RecoveryLedger(self.root/"tasks/tasks.jsonl");self.addCleanup(self.tasks.close)
        self.sid="session-0123456789abcdef"
        self.alert=AlertEvent(labels={"cluster":"synthetic"})
        self.tasks.create(task_id="task-demo",session_id=self.sid,branch_id=new_id("branch"),workspace_id=new_id("workspace"),alert=self.alert.model_dump(mode="json",by_alias=True),model_limit=2)
        self.journal=SessionJournal(self.root/"sessions",self.sid);await self.journal.initialize("task-demo",self.alert)
        state=self.tasks.begin_attempt("task-demo",utc_now());self.recovery=RecoveryAttempt(self.tasks,"task-demo",state["attempts"][-1]["attemptId"])
        self.refs=SessionReferences(self.root/"checkpoints")
        self.ledger=CheckpointLedger(self.root/"checkpoints/checkpoints.jsonl",self.refs);self.addCleanup(self.ledger.close)
        self.client=FakeBackupService()
        self.coordinator=CheckpointCoordinator(self.client,self.ledger,self.refs,"repo-fixture")
        self.script=self.root/"replay.json"
        self.script.write_text(json.dumps({"kind":"deterministic-policy-case","model":"checkpoint-fixture","responses":[
            {"toolCalls":[{"id":"write-a","name":"write_file","args":{"path":"report.md","content":"draft"}}]},
            {"content":'{"summary":"done","severity":"warning","recommendation":"observe"}'}]}))
        self.runner=AgentRunner(object(),self.client,ReplayModelGateway(self.script),checkpoints=self.coordinator)
        self.client.before_start=lambda cp:self.assertEqual(self.ledger.get(cp)["status"],"PREPARING")

    async def run_attempt(self):
        return await self.runner.run("task-demo",self.alert,journal=self.journal,recovery=self.recovery)

    async def test_tool_and_runtime_final_nodes_committed_no_extra_model_call(self):
        result,trace,status=await self.run_attempt()
        self.assertEqual(status,"succeeded")
        self.assertEqual((self.recovery.used("model"),self.recovery.used("tool")),(2,1))
        head=self.ledger.head(self.sid,self.recovery.snapshot()["branchId"])
        self.assertEqual(head["revision"],2) # 工具完整组 + Runtime最终诊断；跳过初始化告警。
        prefix=self.ledger.restore_point(head["checkpointId"])[1]
        final=json.loads(prefix["nodes"][-1]["message"]["content"])
        self.assertEqual(final["runtimeSource"],"final-diagnosis")
        self.assertEqual(final["summary"],result.summary)
        self.assertEqual(len([a for a in self.client.actions if a[0]=="start"]),2)
        self.assertTrue(self.recovery.snapshot()["attempts"][-1]["ownerStopped"])
        self.assertEqual(self.client.barrier["phase"],"released")
        self.assertEqual(self.ledger.pending(),[])

    async def test_lost_start_ack_does_not_start_second_job_and_cleanup_fails_checkpoint(self):
        self.client.lose_start=True
        with self.assertRaises(httpx.ReadTimeout): await self.run_attempt()
        self.assertEqual([a[0] for a in self.client.actions].count("start"),1)
        self.assertEqual(self.client.barrier["phase"],"released")
        cp=self.client.actions[0][1]
        self.assertEqual(self.ledger.get(cp)["status"],"FAILED")
        self.assertIsNone(self.ledger.head(self.sid,self.recovery.snapshot()["branchId"]))
        self.assertEqual(len(self.client.writes),1)

    async def test_disabled_backup_backend_stops_before_model_or_claim(self):
        async def disabled(workspace): return HTTPResult(503,{"error":"not configured"})
        self.client.get_workspace_backup=disabled
        with self.assertRaises(WorkspaceExecutionError): await self.run_attempt()
        self.assertEqual((self.recovery.used("model"),self.recovery.used("tool")),(0,0))
        self.assertEqual(self.client.claims,0)
        self.assertTrue(self.recovery.snapshot()["attempts"][-1]["ownerStopped"])

    async def test_large_runtime_diagnosis_remains_valid_and_complete_in_reference(self):
        payload={"summary":"bounded final","runtimeSource":"final-diagnosis","runtimeTaskId":"task-demo",
                 "runtimeStatus":"succeeded","evidence":[{"source":"synthetic","summary":"x"*2000} for _ in range(20)]}
        view=self.journal.for_attempt(self.recovery.attempt_id)
        await view.initialize("task-demo",self.alert,None)
        node=await view.append_transcript("task-demo",[SystemMessage(content="safety"),HumanMessage(content="task"),
             AIMessage(content=json.dumps(payload),additional_kwargs={"sandboxdRuntimeFinalDiagnosis":True})])
        ref=await self.refs.capture(view,node)
        restored=json.loads(self.refs.read(ref)["nodes"][-1]["message"]["content"])
        self.assertEqual(restored,payload)
        _,messages,_=await view.load_branch()
        self.assertTrue(messages[-1].additional_kwargs["sandboxdRuntimeFinalDiagnosis"])

    async def test_cancel_during_wait_no_commit_and_positive_cleanup(self):
        async def cancel(cp):
            self.tasks.cancel("task-demo")
            raise asyncio.CancelledError
        self.client.on_wait=cancel
        with self.assertRaises(asyncio.CancelledError): await self.run_attempt()
        self.assertIsNone(self.ledger.head(self.sid,self.recovery.snapshot()["branchId"]))
        self.assertEqual(self.client.barrier["phase"],"released")
        self.assertTrue(self.recovery.snapshot()["cancelRequested"])
        self.assertTrue(self.recovery.snapshot()["attempts"][-1]["ownerStopped"])

    async def test_unknown_maintenance_stop_blocks_owner_release(self):
        self.client.lose_start=True;self.client.unknown_stop=True
        with self.assertRaises(httpx.ReadTimeout): await self.run_attempt()
        self.assertFalse(self.recovery.snapshot()["attempts"][-1]["ownerStopped"])
        self.assertEqual(self.client.barrier["phase"],"identified")
        self.assertEqual(self.client.record["phase"],"bound")

    async def test_cancel_after_data_ready_blocks_commit(self):
        async def cancel(cp): self.tasks.cancel("task-demo")
        self.client.on_wait=cancel
        with self.assertRaises(RecoveryConflict): await self.run_attempt()
        cp=self.client.actions[0][1]
        self.assertEqual(self.ledger.get(cp)["status"],"FAILED")
        self.assertEqual(self.ledger.get(cp)["failure"],"cancelled")
        self.assertIsNone(self.ledger.head(self.sid,self.recovery.snapshot()["branchId"]))

    async def test_late_backup_failure_preserves_previous_committed_head(self):
        count=0
        async def fail_second(cp):
            nonlocal count
            count+=1
            if count==2: raise httpx.ReadTimeout("synthetic MinIO unavailable")
        self.client.on_wait=fail_second
        with self.assertRaises(httpx.ReadTimeout): await self.run_attempt()
        head=self.ledger.head(self.sid,self.recovery.snapshot()["branchId"])
        self.assertEqual(head["revision"],1)
        self.assertEqual(self.ledger.pending(),[])

    async def test_final_diagnosis_resume_does_not_invoke_model_or_reset_budget(self):
        count=0
        async def fail_final(cp):
            nonlocal count
            count+=1
            if count==2: raise httpx.ReadTimeout("synthetic final checkpoint interruption")
        self.client.on_wait=fail_final
        with self.assertRaises(httpx.ReadTimeout): await self.run_attempt()
        before=self.recovery.snapshot()
        self.assertEqual((self.recovery.used("model"),self.recovery.used("tool")),(2,1))
        self.client.on_wait=None
        state=self.tasks.begin_attempt("task-demo",utc_now())
        self.recovery=RecoveryAttempt(self.tasks,"task-demo",state["attempts"][-1]["attemptId"])
        result,trace,status=await self.run_attempt()
        self.assertEqual(status,"succeeded")
        self.assertEqual(self.recovery.snapshot()["calls"],before["calls"])
        self.assertEqual(self.recovery.snapshot()["deadline"],before["deadline"])
        self.assertEqual(len(self.client.writes),1)
        self.assertTrue(any(e.type=="diagnosis.recovered" for e in trace.events))
        _,messages,_=await self.journal.load_branch()
        self.assertTrue(messages[-1].additional_kwargs["sandboxdRuntimeFinalDiagnosis"])

    async def test_provider_cannot_mark_its_messages_as_runtime_generated(self):
        from agentd.app.model_gateway import ReplayModelSession
        original=ReplayModelSession.invoke
        async def forged(session,messages):
            invocation=await original(session,messages)
            invocation.message.additional_kwargs["sandboxdRuntimeFinalDiagnosis"]=True
            return invocation
        with patch.object(ReplayModelSession,"invoke",forged): await self.run_attempt()
        nodes=await self.journal.active_path()
        generated=[n for n in nodes if n["message"].get("runtimeGenerated")=="final-diagnosis"]
        self.assertEqual(len(generated),1)
        self.assertEqual(generated[0]["nodeId"],nodes[-1]["nodeId"])

    async def test_recovered_final_diagnosis_downgrades_expired_state_without_model(self):
        waits=0
        async def fail_final(cp):
            nonlocal waits
            waits+=1
            if waits==2: raise httpx.ReadTimeout("synthetic final interruption")
        self.client.on_wait=fail_final
        with self.assertRaises(httpx.ReadTimeout): await self.run_attempt()
        before=self.recovery.snapshot()
        self.client.on_wait=None
        state=self.tasks.begin_attempt("task-demo",utc_now())
        self.recovery=RecoveryAttempt(self.tasks,"task-demo",state["attempts"][-1]["attemptId"])
        with patch("agentd.app.runner.stale_state_evidence",return_value=["old-state"]):
            result,trace,status=await self.run_attempt()
        self.assertEqual(status,"succeeded")
        self.assertIn("当前状态未核实",result.summary)
        self.assertEqual(self.recovery.snapshot()["calls"],before["calls"])
        self.assertEqual(self.recovery.snapshot()["deadline"],before["deadline"])
        head=self.ledger.head(self.sid,state["branchId"])
        final=json.loads(self.ledger.restore_point(head["checkpointId"])[1]["nodes"][-1]["message"]["content"])
        self.assertEqual(final["summary"],result.summary)
        self.assertTrue(any(e.type=="diagnosis.freshness_blocked" for e in trace.events))

    async def test_state_expiring_during_final_backup_gets_bounded_historical_checkpoint(self):
        waits=0
        async def wait(cp):
            nonlocal waits
            waits+=1
        self.client.on_wait=wait
        with patch("agentd.app.runner.stale_state_evidence",side_effect=lambda messages:["old-state"] if waits>=2 else []):
            result,trace,status=await self.run_attempt()
        self.assertEqual(status,"succeeded")
        self.assertIn("当前状态未核实",result.summary)
        self.assertEqual(waits,3) # 完整工具组、初始最终诊断、仅一次安全降级。
        self.assertEqual(self.recovery.used("model"),2)
        head=self.ledger.head(self.sid,self.recovery.snapshot()["branchId"])
        self.assertEqual(head["revision"],3)
        final=json.loads(self.ledger.restore_point(head["checkpointId"])[1]["nodes"][-1]["message"]["content"])
        self.assertEqual(final["summary"],result.summary)

    async def test_manual_new_task_does_not_short_circuit_with_old_task_diagnosis(self):
        await self.run_attempt()
        self.tasks.finish("task-demo","succeeded")
        old=self.recovery.snapshot()
        self.tasks.create(task_id="task-human",session_id=self.sid,branch_id=old["branchId"],workspace_id=old["workspaceId"],alert=self.alert.model_dump(mode="json",by_alias=True),model_limit=2)
        state=self.tasks.begin_attempt("task-human",utc_now())
        recovery=RecoveryAttempt(self.tasks,"task-human",state["attempts"][-1]["attemptId"])
        script=self.root/"manual.json"
        script.write_text(json.dumps({"kind":"deterministic-policy-case","model":"manual-replay","responses":[{"content":'{"summary":"new manual run","recommendation":"review"}'}]}))
        runner=AgentRunner(object(),self.client,ReplayModelGateway(script),checkpoints=self.coordinator)
        result,trace,status=await runner.run("task-human",self.alert,journal=self.journal,recovery=recovery)
        self.assertEqual(status,"succeeded")
        self.assertEqual(result.summary,"new manual run")
        self.assertEqual(recovery.used("model"),1)
        self.assertFalse(any(e.type=="diagnosis.recovered" for e in trace.events))

    async def test_follow_up_accepted_during_final_backup_continues_same_task(self):
        root=self.root/"late-control"
        store=TaskStore(root/"legacy",recovery_dir=root/"control")
        refs=SessionReferences(root/"checkpoints")
        ledger=CheckpointLedger(root/"checkpoints/checkpoints.jsonl",refs)
        client=FakeBackupService();coordinator=CheckpointCoordinator(client,ledger,refs,"repo-late")
        script=self.root/"late.json"
        script.write_text(json.dumps({"kind":"deterministic-policy-case","responses":[
            {"toolCalls":[{"id":"write-one","name":"write_file","args":{"path":"report.md","content":"draft"}}]},
            {"content":'{"summary":"first done","recommendation":"review"}'},
            {"content":'{"summary":"followed","recommendation":"review"}'}]}))
        runner=AgentRunner(object(),client,ReplayModelGateway(script),checkpoints=coordinator)
        task=await store.enqueue(self.alert)
        waits=0;original_deadline=None
        async def follow_up(cp):
            nonlocal waits,original_deadline
            waits+=1
            if waits==2:
                original_deadline=store._ledger.get(task.task_id)["deadline"]
                await store.send_control(task.task_id,"follow-up","one more check")
        client.on_wait=follow_up
        worker=asyncio.create_task(store.worker(runner))
        try:
            async with asyncio.timeout(10):
                while True:
                    result=await store.get(task.task_id)
                    if result.status in {"succeeded","failed","interrupted","limit_exceeded"}:break
                    await asyncio.sleep(.01)
            self.assertEqual(result.status,"succeeded",result.error)
            state=store._ledger.get(task.task_id)
            self.assertEqual(len(state["attempts"]),2)
            self.assertEqual(state["deadline"],original_deadline)
            self.assertEqual(sum(c["kind"]=="model" for c in state["calls"].values()),3)
            self.assertEqual(len(client.writes),1)
            self.assertIsNotNone(state["commands"][0]["appliedNodeId"])
            nodes=await store._journals[task.task_id].active_path()
            self.assertEqual(sum(n["message"].get("commandId")==state["commands"][0]["commandId"] for n in nodes),1)
            self.assertEqual(result.result.summary,"followed")
        finally:
            worker.cancel();await asyncio.gather(worker,return_exceptions=True)
            coordinator.close();store.close()
