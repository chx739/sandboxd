"""本地合成账本/fake后端；不是实际restic删除或进程SIGKILL实验。"""
import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import httpx

from langchain_core.messages import HumanMessage, AIMessage
from agentd.app.clients import HTTPResult
from agentd.app.models import AlertEvent
from agentd.app.runtime.checkpoints import CheckpointLedger, CheckpointConflict
from agentd.app.runtime.durable import JournalError, JournalCorrupt
from agentd.app.runtime.recovery import new_id
from agentd.app.runtime.session import SessionJournal
from agentd.app.runtime.session_refs import SessionReferences
from agentd.app.runtime.checkpoint_coordinator import CheckpointCoordinator
from agentd.checkpoint_cleanup import CleanupCoordinator, CleanupClient, execute
from agentd.checkpoint_admin import perform


OWNER = {"controllerId": "controller-12345678123442348abc123456789abc", "repositoryCryptoId": "1" * 64, "ownerSnapshotId": "9" * 64}


class SimulatedCut(BaseException):
    pass


class FakeCleanup:
    def __init__(self):
        self.barrier = None; self.calls = []; self.cut = None; self.fail = False; self.bad_proof = False

    async def get(self):
        return HTTPResult(200, copy.deepcopy(self.barrier)) if self.barrier else HTTPResult(404, {})

    async def request(self, action, record, *, job_id=None, next_job_id=None):
        self.calls.append(action)
        if action == "reserve":
            self.barrier = {"schemaVersion": 1, "workspaceId": "workspace-" + OWNER["controllerId"][11:],
                "checkpointId": "cp-" + record["gcId"][3:], "jobId": record["jobId"], "operation": record["operation"],
                "cleanup": {"gcId": record["gcId"]}, "job": {"owner": OWNER}, "phase": "reserved"}
            if record["snapshotId"]: self.barrier["cleanup"]["snapshotId"] = record["snapshotId"]
        b = self.barrier
        if action == "start":
            b.update(phase="identified", podUid="synthetic-uid-" + record["jobId"])
        elif action == "wait":
            b.update(phase="stopped", stopReason="containers-terminated")
            if self.fail: return HTTPResult(503, {})
            proof = {"operation": record["operation"], "owner": copy.deepcopy(OWNER),
                     "maintenanceImageId": "sha256:" + "8" * 64, "podUid": b["podUid"]}
            if record["operation"] == "forget": proof["snapshotId"] = record["snapshotId"]
            else: proof.update(outputSha256="7" * 64, outputBytes=17)
            if self.bad_proof: proof["owner"]["repositoryCryptoId"] = "0" * 64
            b["cleanupResult"] = proof
        elif action == "finish": b["phase"] = "released"
        elif action == "retry":
            b.update(phase="reserved", jobId=next_job_id)
            for field in ("podUid", "stopReason", "cleanupResult"): b.pop(field, None)
        elif action == "abort": b.update(phase="stopped", stopReason="never-submitted" if b["phase"] == "reserved" else "containers-terminated")
        elif action == "cancel-reservation": b.update(phase="released", stopReason="never-submitted", cleanupCancelled=True)
        if action == self.cut:
            self.cut = None; raise SimulatedCut(action)
        return HTTPResult(200, {"barrier": copy.deepcopy(b), **({"result": copy.deepcopy(b["cleanupResult"])} if action == "wait" else {})})


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp"); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.directory = self.root / "checkpoints"
        self.catalogue = patch("agentd.app.runtime.maintenance.CATALOGUE_ROOT", self.root / "catalogue")
        self.catalogue.start(); self.addCleanup(self.catalogue.stop)
        self.refs = SessionReferences(self.directory)
        self.ledger = CheckpointLedger(self.directory / "checkpoints.jsonl", self.refs)
        self.addCleanup(lambda: self.ledger.close())
        self.ledger.maintenance.bind(OWNER)
        session = SessionJournal(self.root / "sessions", "session-0123456789abcdef")
        await session.initialize("task-cleanup", AlertEvent(labels={"cluster": "synthetic"}))
        node = await session.append_transcript("task-cleanup", [HumanMessage(content="synthetic"), AIMessage(content="done")])
        self.identity = dict(sessionId=session.session_id, branchId=new_id("branch"), sessionNodeId=node,
            workspaceId=new_id("workspace"), taskId="task-cleanup", attemptId=new_id("attempt"),
            sandboxId="sandbox-cleanup", podUid="pod-cleanup", generation=1,
            imageRef="sandboxd-workspace:runtime-v1.5", imageId="sha256:" + "4" * 64,
            sessionRef=await self.refs.capture(session, node))
        self.data = dict(repositoryId="repo-synthetic", repositoryCryptoId="1" * 64, snapshotId="2" * 64, manifestSha256="3" * 64)
        self.first = self.checkpoint(None, "2" * 64); self.head = self.checkpoint(self.first, "5" * 64)
        self.client = FakeCleanup()

    def checkpoint(self, parent, snapshot):
        cp = new_id("cp"); identity = dict(self.identity, taskId=new_id("task"))
        self.ledger.prepare(cp, identity, parent); self.ledger.data_ready(cp, dict(self.data, snapshotId=snapshot)); self.ledger.commit(cp)
        return cp

    def plan(self, operation="forget", snapshot="2" * 64):
        return self.ledger.maintenance.plan(new_id("gc"), operation, snapshot, self.ledger.cleanup_preview()["planSha256"])

    def reopen(self):
        self.ledger.close(); self.ledger = CheckpointLedger(self.directory / "checkpoints.jsonl", self.refs)

    async def test_forget_protocol_order_terminal_idempotence_and_rebuild(self):
        record = self.plan(); coordinator = CleanupCoordinator(self.ledger, self.client)
        done = await coordinator.run(record["gcId"])
        self.assertEqual(done["status"], "DONE"); self.assertEqual(self.client.calls, ["reserve", "start", "wait", "finish"])
        self.assertEqual(self.ledger.file_availability(self.first), "forgotten")
        self.assertEqual(self.ledger.restore_point(self.head)[0]["checkpointId"], self.head)
        before = (self.directory / "checkpoints.jsonl").read_bytes(); self.reopen()
        self.assertEqual(await CleanupCoordinator(self.ledger, self.client).run(record["gcId"]), done)
        self.assertEqual(before, (self.directory / "checkpoints.jsonl").read_bytes())
        events = self.ledger._journal.entries(); types = [r["type"] for r in events]
        forgets = [r["data"]["status"] for r in events if r["type"] == "checkpoint.forget"]
        self.assertEqual(forgets, ["PREPARED", "FORGOTTEN"]); self.assertEqual(types[-1], "cleanup.execution")

    async def test_fake_cut_points_preserve_request_and_no_repeated_start(self):
        for action in ("reserve", "start", "wait", "finish"):
            # 每个切点先用独立prune请求；这里只验证协议回放，不冒称真实进程故障。
            record = self.plan("prune", ""); self.client = FakeCleanup(); self.client.cut = action
            with self.assertRaises(SimulatedCut): await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
            self.reopen(); done = await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
            self.assertEqual(done["status"], "DONE")
            self.assertEqual(self.client.calls.count("start"), 1); self.assertEqual(self.client.calls.count("reserve"), 1)

    async def test_failure_no_automatic_retry_and_explicit_fixed_target_attempt(self):
        record = self.plan(); self.client.fail = True
        with self.assertRaises(JournalError): await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
        self.assertEqual(self.ledger.file_availability(self.first), "forget-pending")
        self.reopen()
        with self.assertRaises(JournalError): await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
        self.assertEqual(self.client.calls.count("start"), 1)
        self.client.fail = False; self.client.cut = "retry"
        with self.assertRaises(SimulatedCut): await CleanupCoordinator(self.ledger, self.client).retry(record["gcId"])
        attempted = self.ledger.maintenance.get(record["gcId"])
        self.assertEqual(attempted["attempt"], 2); self.assertNotEqual(attempted["jobId"], record["jobId"])
        self.reopen(); done = await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
        self.assertEqual(done["snapshotId"], record["snapshotId"]); self.assertEqual(done["status"], "DONE")
        self.assertEqual(self.client.calls.count("retry"), 1)

    async def test_bad_proof_cannot_forget_or_finish(self):
        record = self.plan(); self.client.bad_proof = True
        with self.assertRaises(JournalError): await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
        self.assertEqual(self.ledger.forget_request(record["gcId"])["status"], "PREPARED")
        self.assertNotIn("finish", self.client.calls)

    async def test_stale_preview_cancels_only_before_durable_intent(self):
        record = self.plan(); self.ledger.retain(self.first, True)
        with self.assertRaises(CheckpointConflict): await CleanupCoordinator(self.ledger, self.client).run(record["gcId"])
        self.assertEqual(self.client.calls, ["reserve", "cancel-reservation"])
        self.assertEqual(self.ledger.maintenance.get(record["gcId"])["status"], "CANCELLED")
        self.assertEqual(self.ledger.file_availability(self.first), "available")

    async def test_pending_blocks_save_restore_and_competing_plan(self):
        record = self.plan()
        with self.assertRaises(CheckpointConflict): self.checkpoint(self.head, "6" * 64)
        with self.assertRaises(JournalCorrupt): self.plan("prune", "")
        with self.assertRaises(JournalCorrupt):
            self.ledger.prepare_restore(new_id("restore"), self.head, session_id=self.identity["sessionId"],
                branch_id=new_id("branch"), workspace_id=new_id("workspace"), task_id=new_id("task"))
        before = (self.directory / "checkpoints.jsonl").read_bytes()
        self.assertEqual(self.ledger.maintenance.plan(record["gcId"], "forget", record["snapshotId"], record["planSha256"]), record)
        self.assertEqual(before, (self.directory / "checkpoints.jsonl").read_bytes())

    async def test_unique_directory_claim_clone_and_old_adoption_refused(self):
        other = self.root / "other"; refs = SessionReferences(other)
        ledger = CheckpointLedger(other / "checkpoints.jsonl", refs)
        try:
            with self.assertRaises(JournalError): ledger.maintenance.bind(OWNER)
        finally: ledger.close()
        self.ledger.close(); clone = self.root / "clone"; shutil.copytree(self.directory, clone)
        with self.assertRaises((JournalError, JournalCorrupt)):
            CheckpointLedger(clone / "checkpoints.jsonl", SessionReferences(clone))
        self.reopen()
        with self.assertRaises(JournalError): self.ledger.maintenance.bind(dict(OWNER, repositoryCryptoId="0" * 64))

    async def test_operator_client_rejects_nonloopback_and_free_actions(self):
        for url in ("https://example.com", "http://user:password@127.0.0.1", "http://127.0.0.1/free/path"):
            with self.assertRaises(ValueError): CleanupClient(url, "synthetic-operator")
        client = CleanupClient("http://127.0.0.1:10880", "synthetic-operator")
        try:
            with self.assertRaises(ValueError): await client.request("unlock", {})
        finally: await client.close()
        self.ledger.close(); before = (self.directory / "checkpoints.jsonl").read_bytes()
        with patch.dict("os.environ", {"SANDBOXD_OPERATOR_TOKEN": ""}):
            with self.assertRaises(ValueError): await execute(self.directory, "apply", gc_id=new_id("gc"))
        self.assertEqual(before, (self.directory / "checkpoints.jsonl").read_bytes()); self.reopen()

    async def test_operator_http_shape_and_no_redirect_or_environment_proxy(self):
        record = self.plan(); seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"barrier": {"phase": "reserved"}})
        client = CleanupClient("http://127.0.0.1:10880", "synthetic-operator")
        await client._http.aclose(); client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            response = await client.request("reserve", record)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(seen[0].url.path, "/api/v1/repository/cleanup/reserve")
            self.assertEqual(seen[0].headers["Authorization"], "Bearer synthetic-operator")
            import json
            body = json.loads(seen[0].content)
            self.assertEqual(set(body), {"gcId", "jobId", "operation", "snapshotId", "timeoutMs"})
            self.assertNotIn("owner", body)
        finally: await client.close()

    async def test_invalid_complete_execution_and_early_done_rejected(self):
        record = self.plan()
        with self.assertRaises(JournalCorrupt): self.ledger.maintenance.complete(record["gcId"])
        forged = dict(record, jobId="other-job", attempt=1)
        self.ledger._journal.append("cleanup.execution", forged); self.ledger.close()
        with self.assertRaises(JournalCorrupt):
            CheckpointLedger(self.directory / "checkpoints.jsonl", self.refs)

    async def test_pending_preflight_stops_before_backend_or_model_routing(self):
        self.plan()
        class Recovery:
            def remaining(self): return 120
        class Client:
            async def get_workspace_backup(self, *args):
                raise AssertionError("pending maintenance must fail before backend request")
        coordinator = CheckpointCoordinator(Client(), self.ledger, self.refs, "repo-synthetic")
        with self.assertRaises(CheckpointConflict): await coordinator.preflight(Recovery())

    async def test_pending_service_start_refuses_worker_and_keeps_checkpoint_bytes(self):
        from agentd.app.config import Settings
        from agentd.app.main import create_app
        record = self.plan(); self.ledger.close()
        before = (self.directory / "checkpoints.jsonl").read_bytes()
        project = Path(__file__).resolve().parents[1]
        settings = Settings(listen_host="127.0.0.1", listen_port=8090, api_token="synthetic-api",
            alert_token="synthetic-alert", prometheus_url="http://127.0.0.1:9090", sandboxd_url="http://127.0.0.1:10880",
            sandboxd_token="synthetic-agent", llm_mode="replay", llm_base_url="", llm_model="", llm_api_key="",
            llm_thinking="default", replay_file=project / "testdata/injection-denied.replay.json",
            trace_dir=self.root / "traces", workspace_backend="nfs", recovery_dir=self.root, checkpoint_mode="restic")
        with patch("agentd.app.main.PrometheusClient"), patch("agentd.app.main.SandboxdClient"), patch("agentd.app.main.LinuxHostClient"), patch("agentd.app.main.AgentRunner", side_effect=AssertionError("Worker must not be initialized")):
            with self.assertRaises(CheckpointConflict): create_app(settings)
        self.assertEqual(before, (self.directory / "checkpoints.jsonl").read_bytes()); self.reopen()
        self.assertEqual(self.ledger.maintenance.get(record["gcId"])["status"], "PENDING")

    async def test_owned_preview_reads_pending_cursor_without_rewriting_control(self):
        record = self.plan(); self.ledger.close()
        paths = [self.directory / "checkpoints.jsonl", self.root / "catalogue" / (OWNER["repositoryCryptoId"] + ".json")]
        before = [(p.read_bytes(), p.stat().st_ctime_ns, p.stat().st_mode) for p in paths]
        view = perform(self.directory, "preview")
        self.assertEqual(view["maintenance"]["owner"], OWNER)
        self.assertEqual(view["maintenance"]["pendingExecutions"], [record])
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_ctime_ns, p.stat().st_mode) for p in paths])
        self.reopen()

    async def test_client_close_failure_still_releases_control_lock(self):
        record = self.plan("prune", ""); self.ledger.close()
        self.client.close = AsyncMock(side_effect=RuntimeError("synthetic close failure"))
        with patch.dict("os.environ", {"SANDBOXD_OPERATOR_TOKEN": "synthetic-operator"}), patch("agentd.checkpoint_cleanup.CleanupClient", return_value=self.client):
            with self.assertRaises(RuntimeError):
                await execute(self.directory, "reconcile", gc_id=record["gcId"])
        self.reopen()
        self.assertEqual(self.ledger.maintenance.get(record["gcId"])["status"], "DONE")
