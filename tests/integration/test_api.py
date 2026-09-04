from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import AppDependencies
from app.api.routes_analysis import _encode_events
from app.contracts.api import AnalysisRequest
from app.contracts.events import SSEEvent
from app.main import create_app
from app.workflow.runtime import RuntimeUnavailableError


class FakeClosable:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeNeo4j(FakeClosable):
    async def close(self) -> None:
        self.closed = True

    async def verify_connectivity(self) -> bool:
        return True


class FailingCloseNeo4j(FakeNeo4j):
    async def close(self) -> None:
        raise RuntimeError("close failed")


class FailingHealthNeo4j(FakeNeo4j):
    async def verify_connectivity(self) -> bool:
        raise RuntimeError("neo4j_password=not-for-logs")


class FakeRuntime:
    def __init__(self) -> None:
        self.resume_fields: dict[str, Any] | None = None

    async def stream(
        self,
        request: AnalysisRequest,
        *,
        trace_id: str,
        thread_id: str,
    ) -> AsyncIterator[SSEEvent]:
        yield self._event(trace_id, thread_id, "workflow_started", "Workflow started")
        yield self._event(trace_id, thread_id, "agent_started", "Market agent started")
        yield self._event(trace_id, thread_id, "workflow_completed", "Workflow completed")

    async def resume(
        self,
        *,
        thread_id: str,
        fields: dict[str, Any],
        trace_id: str,
    ) -> AsyncIterator[SSEEvent]:
        self.resume_fields = fields
        yield self._event(trace_id, thread_id, "workflow_started", "Workflow resumed")
        yield self._event(trace_id, thread_id, "workflow_completed", "Workflow completed")

    async def get_graph(self, thread_id: str) -> dict[str, list[dict[str, str]]] | None:
        if thread_id == "known-thread":
            return {
                "nodes": [{"id": "product-1", "label": "Product"}],
                "edges": [{"source": "product-1", "target": "category-1"}],
            }
        return None

    @staticmethod
    def _event(
        trace_id: str,
        thread_id: str,
        event_type: str,
        message: str,
    ) -> SSEEvent:
        return SSEEvent(
            trace_id=trace_id,
            thread_id=thread_id,
            event_type=event_type,  # type: ignore[arg-type]
            message=message,
            timestamp="2026-09-04T16:00:00+08:00",
        )


class SensitiveUnavailableRuntime(FakeRuntime):
    def stream(
        self,
        request: AnalysisRequest,
        *,
        trace_id: str,
        thread_id: str,
    ) -> AsyncIterator[SSEEvent]:
        raise RuntimeUnavailableError("llm_api_key=not-for-clients")


class IncompleteGraphRuntime(FakeRuntime):
    async def get_graph(self, thread_id: str) -> dict[str, list[dict[str, str]]] | None:
        return {"nodes": []}


class DisconnectingRequest:
    async def is_disconnected(self) -> bool:
        return True


def valid_request() -> dict[str, object]:
    return {
        "product": {
            "name": "Magnetic Wireless Charger",
            "category": "consumer_electronics",
            "purchase_cost_cny": "70.00",
        },
        "question": "市场需求怎么样？",
    }


def read_sse_events(content: str) -> list[dict[str, Any]]:
    blocks = [block for block in content.split("\n\n") if block]
    return [json.loads(block.split("\n", maxsplit=1)[1].removeprefix("data: ")) for block in blocks]


def test_stream_returns_runtime_events_in_order_with_generated_ids() -> None:
    runtime = FakeRuntime()
    app = create_app(dependencies=AppDependencies(runtime=runtime))

    with TestClient(app) as client:
        response = client.post("/api/v1/analysis/stream", json=valid_request())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = read_sse_events(response.text)
    assert [event["event_type"] for event in events] == [
        "workflow_started",
        "agent_started",
        "workflow_completed",
    ]
    assert len({event["trace_id"] for event in events}) == 1
    assert len({event["thread_id"] for event in events}) == 1


def test_resume_keeps_thread_id_and_passes_only_submitted_fields() -> None:
    runtime = FakeRuntime()
    app = create_app(dependencies=AppDependencies(runtime=runtime))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analysis/thread-42/resume",
            json={"fields": {"selling_price_usd": "29.99"}},
        )

    assert response.status_code == 200
    events = read_sse_events(response.text)
    assert [event["thread_id"] for event in events] == ["thread-42", "thread-42"]
    assert runtime.resume_fields == {"selling_price_usd": "29.99"}


def test_graph_returns_runtime_trace_or_404_for_unknown_thread() -> None:
    app = create_app(dependencies=AppDependencies(runtime=FakeRuntime()))

    with TestClient(app) as client:
        known = client.get("/api/v1/analysis/known-thread/graph")
        unknown = client.get("/api/v1/analysis/missing-thread/graph")

    assert known.status_code == 200
    assert known.json() == {
        "nodes": [{"id": "product-1", "label": "Product"}],
        "edges": [{"source": "product-1", "target": "category-1"}],
    }
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "No analysis trace found for thread_id 'missing-thread'."


def test_invalid_request_returns_field_validation_error_without_starting_runtime() -> None:
    app = create_app(dependencies=AppDependencies(runtime=FakeRuntime()))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analysis/stream",
            json={"product": valid_request()["product"]},
        )

    assert response.status_code == 422
    assert any(error["loc"][-1] == "question" for error in response.json()["detail"])


def test_health_reports_component_states_without_exposing_secrets_and_closes_dependencies() -> None:
    neo4j = FakeNeo4j()
    http_client = FakeClosable()
    app = create_app(
        dependencies=AppDependencies(
            runtime=FakeRuntime(),
            neo4j_client=neo4j,
            http_client=http_client,
            llm_configured=True,
        )
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"api": "ok", "neo4j": "ok", "llm_config": "configured"}
    assert "api_key" not in response.text.lower()
    assert "password" not in response.text.lower()
    assert neo4j.closed is True
    assert http_client.closed is True


def test_stream_returns_actionable_error_when_runtime_is_not_configured() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.post("/api/v1/analysis/stream", json=valid_request())

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Analysis service is unavailable. Configure the workflow runtime and retry."
    )


def test_shutdown_closes_http_client_even_when_neo4j_close_raises() -> None:
    http_client = FakeClosable()
    app = create_app(
        dependencies=AppDependencies(
            runtime=FakeRuntime(),
            neo4j_client=FailingCloseNeo4j(),
            http_client=http_client,
        )
    )

    with pytest.raises(RuntimeError, match="close failed"):
        with TestClient(app) as client:
            client.get("/health")

    assert http_client.closed is True


def test_runtime_unavailable_error_returns_fixed_safe_detail() -> None:
    app = create_app(dependencies=AppDependencies(runtime=SensitiveUnavailableRuntime()))

    with TestClient(app) as client:
        response = client.post("/api/v1/analysis/stream", json=valid_request())

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Analysis service is unavailable. Configure the workflow runtime and retry."
    )
    assert "not-for-clients" not in response.text


@pytest.mark.asyncio
async def test_disconnect_closes_the_runtime_event_iterator() -> None:
    closed = False

    async def events() -> AsyncIterator[SSEEvent]:
        nonlocal closed
        try:
            yield FakeRuntime._event("trace-1", "thread-1", "workflow_started", "Started")
        finally:
            closed = True

    chunks = [
        chunk
        async for chunk in _encode_events(
            cast(Any, DisconnectingRequest()),
            events(),
            trace_id="trace-1",
            thread_id="thread-1",
        )
    ]

    assert chunks == []
    assert closed is True


def test_graph_rejects_runtime_data_without_edges() -> None:
    app = create_app(dependencies=AppDependencies(runtime=IncompleteGraphRuntime()))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/analysis/known-thread/graph")

    assert response.status_code == 500


def test_health_logs_redacted_connectivity_failure(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(
        dependencies=AppDependencies(
            runtime=FakeRuntime(),
            neo4j_client=FailingHealthNeo4j(),
        )
    )
    caplog.set_level(logging.WARNING, logger="app.api.dependencies")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.json()["neo4j"] == "unavailable"
    assert "Neo4j health check failed (RuntimeError)" in caplog.text
    assert "not-for-logs" not in caplog.text
