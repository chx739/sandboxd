from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .clients import PrometheusClient, SandboxdClient
from .config import Settings, load_settings
from .graph import AgentRunner
from .model_gateway import LiveModelGateway, ReplayModelGateway
from .models import (
    AlertEvent,
    AlertmanagerPayload,
    ControlMessageRequest,
    ManualTaskRequest,
)
from .plugins import build_builtin_registry
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
    if cfg.llm_mode == "live":
        gateway = LiveModelGateway(
            cfg.llm_base_url,
            cfg.llm_model,
            cfg.llm_api_key,
            cfg.llm_thinking,
        )
    else:
        gateway = ReplayModelGateway(cfg.replay_file)

    plugins = build_builtin_registry()
    runner = AgentRunner(prometheus, sandboxd, gateway, plugins)
    store = TaskStore(cfg.trace_dir)

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
            await prometheus.close()
            await sandboxd.close()

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

    @app.post("/api/v1/sessions/{session_id}/resume", status_code=202)
    async def resume_session(session_id: str, request: Request) -> JSONResponse:
        _authorized(request, cfg.api_token)
        try:
            task = await store.resume(session_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    return app
