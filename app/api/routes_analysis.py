from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import AppDependencies, get_dependencies
from app.api.sse import SSEEncoder
from app.contracts.api import AnalysisRequest
from app.contracts.events import SSEEvent
from app.workflow.runtime import RuntimeUnavailableError

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])
Dependencies = Annotated[AppDependencies, Depends(get_dependencies)]


class ResumeRequest(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


class ClosableAsyncIterator(Protocol):
    async def aclose(self) -> None: ...


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


async def _close_events(events: AsyncIterator[SSEEvent]) -> None:
    if hasattr(events, "aclose"):
        await cast(ClosableAsyncIterator, events).aclose()


async def _encode_events(
    request: Request,
    events: AsyncIterator[SSEEvent],
    *,
    trace_id: str,
    thread_id: str,
) -> AsyncIterator[str]:
    try:
        async for event in events:
            if await request.is_disconnected():
                return
            yield SSEEncoder.encode(event)
    except Exception:
        failed_event = SSEEvent(
            trace_id=trace_id,
            thread_id=thread_id,
            event_type="workflow_failed",
            message=(
                "Analysis execution failed. "
                "Resume this thread after correcting the runtime error."
            ),
            timestamp=_timestamp(),
        )
        yield SSEEncoder.encode(failed_event)
    finally:
        await _close_events(events)


def _sse_response(
    request: Request,
    events: AsyncIterator[SSEEvent],
    *,
    trace_id: str,
    thread_id: str,
) -> StreamingResponse:
    return StreamingResponse(
        _encode_events(request, events, trace_id=trace_id, thread_id=thread_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/stream")
async def stream_analysis(
    analysis_request: AnalysisRequest,
    request: Request,
    dependencies: Dependencies,
) -> StreamingResponse:
    trace_id = str(uuid4())
    thread_id = analysis_request.thread_id or str(uuid4())
    try:
        events = dependencies.runtime.stream(
            analysis_request,
            trace_id=trace_id,
            thread_id=thread_id,
        )
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _sse_response(request, events, trace_id=trace_id, thread_id=thread_id)


@router.post("/{thread_id}/resume")
async def resume_analysis(
    thread_id: str,
    resume_request: ResumeRequest,
    request: Request,
    dependencies: Dependencies,
) -> StreamingResponse:
    trace_id = str(uuid4())
    try:
        events = dependencies.runtime.resume(
            thread_id=thread_id,
            fields=resume_request.fields,
            trace_id=trace_id,
        )
    except RuntimeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _sse_response(request, events, trace_id=trace_id, thread_id=thread_id)
