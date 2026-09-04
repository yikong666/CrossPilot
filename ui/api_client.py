"""Small, streaming-safe client for the CrossPilot analysis API."""

import json
import os
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from typing import Any

import httpx
from pydantic import ValidationError

from app.contracts.events import SSEEvent

DEFAULT_API_BASE_URL = "http://localhost:8000"


class APIClientError(Exception):
    """An API failure that is safe to show to an end user."""


class SSEProtocolError(APIClientError):
    """The server returned a malformed SSE record."""


class SSEStreamInterrupted(APIClientError):
    """The connection ended while a partial SSE record was buffered."""


def get_api_base_url() -> str:
    """Resolve the local API endpoint without putting configuration in source code."""
    return os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


async def parse_sse_chunks(chunks: AsyncIterable[str]) -> AsyncIterator[SSEEvent]:
    """Turn arbitrary HTTP text chunks into validated, complete SSE events."""
    buffer = ""
    async for chunk in chunks:
        buffer = (buffer + chunk).replace("\r\n", "\n")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event = _parse_sse_block(block)
            if event is not None:
                yield event

    if buffer.strip():
        raise SSEStreamInterrupted("分析连接中断，请使用原任务继续。")


def _parse_sse_block(block: str) -> SSEEvent | None:
    data_lines: list[str] = []
    has_event_line = False
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            has_event_line = True
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())

    if not has_event_line and not data_lines:
        return None
    if not has_event_line or not data_lines:
        raise SSEProtocolError("服务返回的进度数据格式异常，请稍后重试。")

    try:
        payload = json.loads("\n".join(data_lines))
        return SSEEvent.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SSEProtocolError("服务返回的进度数据格式异常，请稍后重试。") from exc


async def stream_analysis(
    request: Mapping[str, Any],
    *,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[SSEEvent]:
    """Start an analysis and yield its events as the server sends each record."""
    endpoint = f"{(base_url or get_api_base_url()).rstrip('/')}/api/v1/analysis/stream"
    if client is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as owned_client:
            async for event in _post_sse(owned_client, endpoint, dict(request)):
                yield event
        return

    async for event in _post_sse(client, endpoint, dict(request)):
        yield event


async def resume_analysis(
    thread_id: str,
    fields: Mapping[str, Any],
    *,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[SSEEvent]:
    """Resume a paused analysis with user-supplied missing fields."""
    endpoint = f"{(base_url or get_api_base_url()).rstrip('/')}/api/v1/analysis/{thread_id}/resume"
    if client is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as owned_client:
            async for event in _post_sse(owned_client, endpoint, {"fields": dict(fields)}):
                yield event
        return

    async for event in _post_sse(client, endpoint, {"fields": dict(fields)}):
        yield event


async def fetch_graph(
    thread_id: str,
    *,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch the current task's graph evidence with a user-safe failure."""
    endpoint = f"{(base_url or get_api_base_url()).rstrip('/')}/api/v1/analysis/{thread_id}/graph"
    if client is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as owned_client:
            return await _get_graph(owned_client, endpoint)
    return await _get_graph(client, endpoint)


async def _post_sse(
    client: httpx.AsyncClient, endpoint: str, payload: dict[str, Any]
) -> AsyncIterator[SSEEvent]:
    try:
        async with client.stream("POST", endpoint, json=payload) as response:
            response.raise_for_status()
            async for event in parse_sse_chunks(response.aiter_text()):
                yield event
    except httpx.HTTPStatusError as exc:
        raise APIClientError("分析服务暂时不可用，请稍后重试。") from exc
    except httpx.RequestError as exc:
        raise APIClientError("无法连接分析服务，请检查服务状态后重试。") from exc


async def _get_graph(client: httpx.AsyncClient, endpoint: str) -> dict[str, list[dict[str, Any]]]:
    try:
        response = await client.get(endpoint)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise APIClientError("图谱依据暂时无法加载，请稍后重试。") from exc

    nodes = body.get("nodes") if isinstance(body, dict) else None
    edges = body.get("edges") if isinstance(body, dict) else None
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise APIClientError("图谱依据格式异常，请稍后重试。")
    return {"nodes": nodes, "edges": edges}
