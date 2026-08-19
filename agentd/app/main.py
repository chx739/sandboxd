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
from .models import AlertEvent, AlertmanagerPayload, ManualTaskRequest
from .store import QueueFullError, TaskStore

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
        )
    else:
        gateway = ReplayModelGateway(cfg.replay_file)

    runner = AgentRunner(prometheus, sandboxd, gateway)
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
            content={"taskId": task.task_id, "status": task.status},
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

    return app
