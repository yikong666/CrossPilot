from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Protocol

from langgraph.types import Command

from app.contracts.api import AnalysisRequest
from app.contracts.events import SSEEvent

GraphData = dict[str, list[dict[str, Any]]]
logger = logging.getLogger(__name__)
RUNTIME_UNAVAILABLE_DETAIL = (
    "Analysis service is unavailable. Configure the workflow runtime and retry."
)


class RuntimeUnavailableError(RuntimeError):
    """Raised when analysis is requested before Task 8 configures a workflow runtime."""


class WorkflowRuntime(Protocol):
    """Structural boundary used by the API dependency container."""

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


class LangGraphWorkflowRuntime:
    """Translate one compiled LangGraph execution into the locked SSE contract."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph
        self._thread_trace_ids: dict[str, str] = {}
        self._thread_status: dict[str, str] = {}

    def stream(
        self,
        request: AnalysisRequest,
        *,
        trace_id: str,
        thread_id: str,
    ) -> AsyncIterator[SSEEvent]:
        if thread_id in self._thread_status:
            return self._thread_failure(
                trace_id=trace_id,
                thread_id=thread_id,
                message="该 thread_id 已存在；请使用 resume 接口继续原工作流。",
            )
        self._thread_trace_ids[thread_id] = trace_id
        self._thread_status[thread_id] = "active"
        initial_state = {
            "trace_id": trace_id,
            "thread_id": thread_id,
            "request": request,
            "tasks": [],
            "agent_results": {},
            "validation": None,
            "replan_count": 0,
            "final_answer": None,
            "graph_nodes": [],
            "graph_edges": [],
            "errors": [],
        }
        return self._run(initial_state, trace_id=trace_id, thread_id=thread_id)

    def resume(
        self,
        *,
        thread_id: str,
        fields: dict[str, Any],
        trace_id: str,
    ) -> AsyncIterator[SSEEvent]:
        status = self._thread_status.get(thread_id)
        if status is None:
            return self._thread_failure(
                trace_id=trace_id,
                thread_id=thread_id,
                message="找不到可恢复的工作流。",
            )
        if status != "interrupted":
            return self._thread_failure(
                trace_id=trace_id,
                thread_id=thread_id,
                message="当前工作流不处于等待输入状态，无法 resume。",
            )
        self._thread_status[thread_id] = "active"
        return self._run(
            Command(resume=fields, update={"trace_id": trace_id}),
            trace_id=trace_id,
            thread_id=thread_id,
        )

    async def get_graph(self, thread_id: str) -> GraphData | None:
        if thread_id not in self._thread_trace_ids:
            return None
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snapshot = await self._graph.aget_state(config)
        except Exception as exc:
            logger.warning(
                "Graph evidence lookup failed thread_id=%s exception_type=%s",
                thread_id,
                type(exc).__name__,
            )
            return None
        values = snapshot.values or {}
        node_ids: set[str] = set()
        edge_ids: set[str] = set()
        for result in values.get("agent_results", {}).values():
            for evidence in result.evidence:
                node_ids.update(evidence.graph_node_ids)
                edge_ids.update(evidence.graph_edge_ids)
        return {
            "nodes": [{"id": node_id} for node_id in sorted(node_ids)],
            "edges": [{"id": edge_id} for edge_id in sorted(edge_ids)],
        }

    async def _run(
        self,
        graph_input: Any,
        *,
        trace_id: str,
        thread_id: str,
    ) -> AsyncIterator[SSEEvent]:
        config = {"configurable": {"thread_id": thread_id}}
        if not isinstance(graph_input, Command):
            yield self._event(
                trace_id,
                thread_id,
                "workflow_started",
                "工作流已启动。",
            )
        try:
            async for mode, chunk in self._graph.astream(
                graph_input,
                config,
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom" and isinstance(chunk, SSEEvent):
                    if chunk.event_type == "workflow_completed":
                        self._thread_status[thread_id] = "completed"
                    elif chunk.event_type == "workflow_failed":
                        self._thread_status[thread_id] = "failed"
                    yield chunk
                elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                    interrupts = chunk["__interrupt__"]
                    value = interrupts[0].value if interrupts else {}
                    payload = value if isinstance(value, dict) else {}
                    self._thread_status[thread_id] = "interrupted"
                    yield self._event(
                        trace_id,
                        thread_id,
                        "input_required",
                        str(payload.get("follow_up_question") or "请补充必要信息。"),
                        payload,
                    )
        except Exception as exc:
            self._thread_status[thread_id] = "failed"
            logger.warning(
                "Workflow execution failed thread_id=%s node=graph exception_type=%s",
                thread_id,
                type(exc).__name__,
            )
            yield self._event(
                trace_id,
                thread_id,
                "workflow_failed",
                "工作流执行失败，请稍后重试。",
            )

    async def _thread_failure(
        self, *, trace_id: str, thread_id: str, message: str
    ) -> AsyncIterator[SSEEvent]:
        yield self._event(
            trace_id,
            thread_id,
            "workflow_failed",
            message,
        )

    @staticmethod
    def _event(
        trace_id: str,
        thread_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> SSEEvent:
        return SSEEvent.model_validate(
            {
                "trace_id": trace_id,
                "thread_id": thread_id,
                "event_type": event_type,
                "message": message,
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": payload or {},
            }
        )


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
