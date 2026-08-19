from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

MAX_HTTP_BODY_BYTES = 64 << 10


@dataclass(frozen=True)
class HTTPResult:
    status_code: int
    body: Any

    def as_tool_text(self) -> str:
        return json.dumps(
            {"statusCode": self.status_code, "body": self.body},
            ensure_ascii=False,
            separators=(",", ":"),
        )


async def _bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> HTTPResult:
    async with client.stream(method, url, **kwargs) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_HTTP_BODY_BYTES:
                raise ValueError("HTTP 响应超过 64 KiB 上限")
            body.extend(chunk)

        if not body:
            parsed: Any = None
        else:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = body.decode("utf-8", errors="replace")
        return HTTPResult(status_code=response.status_code, body=parsed)


class PrometheusClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(5.0))

    async def query(self, promql: str) -> HTTPResult:
        return await _bounded_request(
            self._client,
            "GET",
            self._base_url + "/api/v1/query",
            params={"query": promql},
        )

    async def close(self) -> None:
        await self._client.aclose()


class SandboxdClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url
        self._headers = {"Authorization": "Bearer " + token}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(35.0))

    async def claim(self) -> dict[str, Any]:
        result = await _bounded_request(
            self._client,
            "POST",
            self._base_url + "/api/v1/sandboxes",
            headers=self._headers,
            timeout=190.0,
        )
        if result.status_code != 201 or not isinstance(result.body, dict):
            raise RuntimeError("认领 sandbox 失败，HTTP %d" % result.status_code)
        return result.body

    async def release(self, sandbox_id: str) -> None:
        result = await _bounded_request(
            self._client,
            "DELETE",
            self._base_url + "/api/v1/sandboxes/" + sandbox_id,
            headers=self._headers,
        )
        if result.status_code not in {204, 404}:
            raise RuntimeError("释放 sandbox 失败，HTTP %d" % result.status_code)

    async def kubernetes_read(
        self,
        sandbox_id: str,
        arguments: dict[str, Any],
    ) -> HTTPResult:
        return await _bounded_request(
            self._client,
            "POST",
            self._base_url
            + "/api/v1/sandboxes/"
            + sandbox_id
            + "/diagnostics/kubernetes",
            headers=self._headers,
            json=arguments,
        )

    async def propose_plan(self, arguments: dict[str, Any]) -> HTTPResult:
        return await _bounded_request(
            self._client,
            "POST",
            self._base_url + "/api/v1/plans",
            headers=self._headers,
            json=arguments,
        )

    async def close(self) -> None:
        await self._client.aclose()
