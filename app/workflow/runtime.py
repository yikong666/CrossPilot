from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.contracts.api import AnalysisRequest
from app.contracts.events import SSEEvent

GraphData = dict[str, list[dict[str, Any]]]
RUNTIME_UNAVAILABLE_DETAIL = (
    "Analysis service is unavailable. Configure the workflow runtime and retry."
)


class RuntimeUnavailableError(RuntimeError):
    """Raised when analysis is requested before Task 8 configures a workflow runtime."""


class WorkflowRuntime(Protocol):
    """Boundary implemented by the LangGraph runtime in the next workflow stage."""

    def stream(
        self,
        request: AnalysisRequest,
        *,
        trace_id: str,
        thread_id: str,
    ) -> AsyncIterator[SSEEvent]: ...

    def resume(
        self,
        *,
        thread_id: str,
        fields: dict[str, Any],
        trace_id: str,
    ) -> AsyncIterator[SSEEvent]: ...

    async def get_graph(self, thread_id: str) -> GraphData | None: ...


class UnavailableWorkflowRuntime:
    """Safe default that makes missing Task 8 wiring visible to API callers."""

    def stream(
        self,
        request: AnalysisRequest,
        *,
        trace_id: str,
        thread_id: str,
    ) -> AsyncIterator[SSEEvent]:
        raise RuntimeUnavailableError(
            "Analysis runtime is not configured. "
            "Configure the workflow runtime before starting analysis."
        )

    def resume(
        self,
        *,
        thread_id: str,
        fields: dict[str, Any],
        trace_id: str,
    ) -> AsyncIterator[SSEEvent]:
        raise RuntimeUnavailableError(
            "Analysis runtime is not configured. "
            "Configure the workflow runtime before resuming analysis."
        )

    async def get_graph(self, thread_id: str) -> GraphData | None:
        return None
