from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .clients import LinuxHostClient, PrometheusClient, SandboxdClient
from .config import Settings, load_settings
from .graph import AgentRunner
from .memory import MemoryStore
from .model_gateway import LiveModelGateway, ReplayModelGateway
from .models import (
    AlertEvent,
    AlertmanagerPayload,
    ControlMessageRequest,
    ManualTaskRequest,
)
from .plugins import build_builtin_registry
from .plugins.knowledge import KnowledgePlugin
from .plugins.logs import LogsPlugin
from .router import JevChoiceSource, ModelPrice, ModelRouter
from .runtime.checkpoint_coordinator import CheckpointCoordinator
from .runtime.checkpoints import CheckpointLedger, CheckpointConflict
from .runtime.durable import JournalCorrupt
from .runtime.session_refs import SessionReferences
from .store import (
    ControlKind,
    QueueFullError,
    TaskConflictError,
    TaskNotFoundError,
    TaskStore,
)

MAX_ALERT_BODY_BYTES = 64 << 10


def _authorized(request: Request, expected_token: str) -> None:
    actual = request.headers.get("Authorization", "")
    expected = "Bearer " + expected_token
    if not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


async def _body(request: Request) -> bytes:
    length = request.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > MAX_ALERT_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request body too large")

    value = bytearray()
    async for chunk in request.stream():
        if len(value) + len(chunk) > MAX_ALERT_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        value.extend(chunk)
    return bytes(value)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    prometheus = PrometheusClient(cfg.prometheus_url)
    sandboxd = SandboxdClient(cfg.sandboxd_url, cfg.sandboxd_token)
    linux_hosts = LinuxHostClient.from_file(cfg.linux_targets_file)
    if cfg.llm_mode == "live":
        gateway = LiveModelGateway(
            cfg.llm_base_url,
            cfg.llm_model,
            cfg.llm_api_key,
            cfg.llm_thinking,
            max_retries=0,
        )
    else:
        gateway = ReplayModelGateway(cfg.replay_file)
    model_router = None
    if cfg.router_mode == "jev":
        gateways = {
            "economy": LiveModelGateway(
                cfg.llm_base_url, cfg.router_economy_model,
                cfg.llm_api_key, cfg.llm_thinking,
                max_retries=0,
            ),
            "strong": LiveModelGateway(
                cfg.llm_base_url, cfg.router_strong_model,
                cfg.llm_api_key, cfg.llm_thinking,
                max_retries=0,
            ),
        }
        model_router = ModelRouter(
            gateways, JevChoiceSource(cfg.router_jev_api_key, ipv4_only=cfg.router_jev_ipv4_only),
            prices={tier: ModelPrice(
                item["inputUsdPerMillion"], item["outputUsdPerMillion"]
            ) for tier, item in cfg.router_prices.items()},
            router_input_usd_per_million=cfg.router_jev_input_usd_per_million,
        )

    memory_store = (
        MemoryStore(cfg.memory_root, cfg.memory_project)
        if cfg.memory_root is not None else None
    )
    knowledge_plugin = (
        KnowledgePlugin(
            cfg.retrieval_corpus, cfg.retrieval_queries,
            cfg.retrieval_embedding_dir, cfg.retrieval_reranker_dir,
        ) if cfg.retrieval_corpus is not None else None
    )
    logs_plugin = LogsPlugin(cfg.logs_file) if cfg.logs_file is not None else None
    plugins = build_builtin_registry(memory_store, knowledge_plugin, logs_plugin)
    if cfg.workspace_backend == "nfs" and cfg.recovery_dir is None:
        raise ValueError("NFS mode requires a durable recovery directory")
    if cfg.checkpoint_mode != "off" and (cfg.checkpoint_mode != "restic" or cfg.workspace_backend != "nfs"):
        raise ValueError("joint checkpoint mode requires durable NFS")
    store = TaskStore(cfg.trace_dir, recovery_dir=cfg.recovery_dir if cfg.workspace_backend == "nfs" else None)
    checkpoints = None
    checkpoint_ledger = None
    try:
        if cfg.checkpoint_mode == "restic":
            refs = SessionReferences(cfg.recovery_dir / "checkpoints")
            checkpoint_ledger = CheckpointLedger(cfg.recovery_dir / "checkpoints/checkpoints.jsonl",refs)
            checkpoints = CheckpointCoordinator(sandboxd,checkpoint_ledger,refs,cfg.checkpoint_repository_id)
            store.configure_checkpoints(checkpoints)
    except BaseException:
        if checkpoint_ledger is not None:
            checkpoint_ledger.close()
        store.close()
        raise
    runner = AgentRunner(
        prometheus,
        sandboxd,
        gateway,
        plugins,
        linux_hosts=linux_hosts,
        workspace_root=cfg.workspace_dir,
        memory_store=memory_store,
        model_router=model_router,
        checkpoints=checkpoints,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker = asyncio.create_task(store.worker(runner))
        try:
            yield
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            if checkpoints is not None:
                checkpoints.close()
            store.close()
            await prometheus.close()
            await sandboxd.close()
            if knowledge_plugin is not None:
                knowledge_plugin.close()
            if logs_plugin is not None:
                logs_plugin.close()

    app = FastAPI(
        title="sandboxd agentd",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/plugins")
    async def list_plugins(request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        return JSONResponse({"plugins": plugins.describe_plugins()})


    @app.post("/api/v1/alerts", status_code=202)
    async def receive_alerts(request: Request) -> JSONResponse:
        _authorized(request, cfg.alert_token)
        try:
            payload = AlertmanagerPayload.model_validate_json(await _body(request))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid Alertmanager payload") from exc

        firing = [
            alert
            for alert in payload.alerts
            if alert.status == "firing"
        ]
        if len(firing) > 10:
            raise HTTPException(status_code=400, detail="一次最多接收 10 条 firing alert")

        task_ids: list[str] = []
        try:
            for alert in firing:
                task = await store.enqueue(alert)
                task_ids.append(task.task_id)
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return JSONResponse(
            status_code=202,
            content={"taskIds": task_ids, "status": "queued"},
        )

    @app.post("/api/v1/tasks", status_code=202)
    async def create_task(request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            payload = ManualTaskRequest.model_validate_json(await _body(request))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid task payload") from exc

        alert = AlertEvent(
            status="firing",
            labels={"source": "manual", **payload.labels},
            annotations={"summary": payload.summary},
        )
        try:
            task = await store.enqueue(alert)
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return JSONResponse(
            status_code=202,
            content={
                "taskId": task.task_id,
                "sessionId": task.session_id,
                "status": task.status,
            },
        )


    @app.get("/api/v1/tasks")
    async def list_tasks(request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        tasks = await store.list_recent()
        return JSONResponse(
            {
                "tasks": [
                    task.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude={"trace"},
                    )
                    for task in tasks
                ]
            }
        )
    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        task = await store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(
            task.model_dump(
                mode="json",
                by_alias=True,
                exclude={"trace"},
            )
        )

    @app.get("/api/v1/tasks/{task_id}/traces")
    async def get_attempt_traces(task_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            return JSONResponse({"taskId": task_id, "attempts": await store.get_task_traces(task_id)})
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/tasks/{task_id}/trace")
    async def get_trace(task_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        task = await store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task.trace is None:
            raise HTTPException(status_code=409, detail="trace not ready")
        return JSONResponse(task.trace.model_dump(mode="json", by_alias=True))

    async def control_task(
        task_id: str,
        kind: ControlKind,
        request: Request,
    ) -> JSONResponse:
        """把 HTTP 控制命令翻译成进程内队列，不直接打断正在执行的工具。"""

        _authorized(request, cfg.api_token)
        try:
            payload = ControlMessageRequest.model_validate_json(
                await _body(request)
            )
            task = await store.send_control(
                task_id,
                kind,
                payload.content,
            )
        except HTTPException:
            raise
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="invalid control payload",
            ) from exc
        return JSONResponse(
            status_code=202,
            content={
                "taskId": task.task_id,
                "sessionId": task.session_id,
                "status": task.status,
                "accepted": kind,
            },
        )

    @app.post("/api/v1/tasks/{task_id}/steer", status_code=202)
    async def steer_task(task_id: str, request: Request) -> JSONResponse:
        return await control_task(task_id, "steer", request)

    @app.post("/api/v1/tasks/{task_id}/follow-up", status_code=202)
    async def follow_up_task(task_id: str, request: Request) -> JSONResponse:
        return await control_task(task_id, "follow-up", request)

    @app.post("/api/v1/tasks/{task_id}/cancel", status_code=202)
    async def cancel_task(task_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            task = await store.cancel(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            status_code=202,
            content={
                "taskId": task.task_id,
                "sessionId": task.session_id,
                "status": task.status,
            },
        )

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            return JSONResponse(await store.get_session(session_id))
        except (TaskNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/sessions/{session_id}/tree")
    async def get_session_tree(session_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            return JSONResponse(await store.get_session_tree(session_id))
        except (TaskNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/sessions/{session_id}/path/{node_id}")
    async def get_session_path(session_id: str, node_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            return JSONResponse(await store.get_session_path(session_id, node_id))
        except (TaskNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/sessions/{session_id}/working-memory")
    async def get_session_working_memory(
        session_id: str, request: Request, nodeId: str | None = None,
    ) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            return JSONResponse(await store.get_session_working_memory(session_id, nodeId))
        except (TaskNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def start_session_branch(
        session_id: str,
        node_id: str | None,
        request: Request,
    ) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            task = await store.resume(session_id, node_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, TaskConflictError, CheckpointConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (OSError, JournalCorrupt) as exc:
            raise HTTPException(status_code=409, detail="恢复来源缺失或校验失败") from exc
        restore = store.restore_for_task(task.task_id)
        return JSONResponse(
            status_code=202,
            content={
                "taskId": task.task_id,
                "sessionId": task.session_id,
                "status": task.status,
                "branchedFrom": node_id,
                **({"restoreId": restore["restoreId"], "checkpointId": restore["sourceCheckpointId"],
                    "branchId": task.branch_id, "workspaceId": task.workspace_id} if restore is not None else {}),
            },
        )

    @app.post("/api/v1/sessions/{session_id}/resume", status_code=202)
    async def resume_session(session_id: str, request: Request) -> JSONResponse:
        return await start_session_branch(session_id, None, request)

    @app.post("/api/v1/sessions/{session_id}/branch/{node_id}", status_code=202)
    async def branch_session(session_id: str, node_id: str, request: Request) -> JSONResponse:
        return await start_session_branch(session_id, node_id, request)

    @app.post("/api/v1/sessions/{session_id}/checkpoints/{checkpoint_id}/restore/{restore_id}", status_code=202)
    async def restore_checkpoint(session_id: str, checkpoint_id: str, restore_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            task = await store.restore_checkpoint(session_id, checkpoint_id, restore_id)
            progress = store.restore_progress(session_id, restore_id)
        except QueueFullError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, TaskConflictError, CheckpointConflict) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, JournalCorrupt) as exc:
            raise HTTPException(status_code=409, detail="恢复来源缺失或校验失败") from exc
        return JSONResponse(status_code=202, content={"taskId": task.task_id, "sessionId": task.session_id,
                    "branchId": task.branch_id, "workspaceId": task.workspace_id, "restoreId": restore_id,
                    "status": task.status, "restoreStatus": progress["status"], "checkpointId": checkpoint_id})

    @app.get("/api/v1/sessions/{session_id}/restores/{restore_id}")
    async def get_restore(session_id: str, restore_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            return JSONResponse(store.restore_progress(session_id, restore_id))
        except (TaskNotFoundError, CheckpointConflict) as exc:
            raise HTTPException(status_code=404, detail="restore not found") from exc
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app
