import json
from collections.abc import AsyncIterator

import httpx
import pytest

from ui.api_client import (
    APIClientError,
    SSEProtocolError,
    SSEStreamInterrupted,
    parse_sse_chunks,
    resume_analysis,
    stream_analysis,
)
from ui.components import (
    apply_sse_event,
    begin_new_analysis,
    build_analysis_payload,
    business_event_message,
    initialize_session_state,
)
from ui.graph_view import trim_graph


async def chunked(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


def event_data(event_type: str, message: str) -> str:
    return json.dumps(
        {
            "trace_id": "trace-1",
            "thread_id": "thread-1",
            "event_type": event_type,
            "message": message,
            "timestamp": "2026-09-04T08:00:00Z",
            "payload": {},
        },
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_parse_sse_chunks_reassembles_split_chinese_event() -> None:
    """Removing the incremental buffer would drop or corrupt a split business event."""
    data = event_data("agent_started", "正在分析市场数据")
    split_at = data.index("市场") + 1

    events = [
        event
        async for event in parse_sse_chunks(
            chunked(f"event: agent_started\ndata: {data[:split_at]}", f"{data[split_at:]}\n\n")
        )
    ]

    assert [event.event_type for event in events] == ["agent_started"]
    assert events[0].message == "正在分析市场数据"


@pytest.mark.asyncio
async def test_parse_sse_chunks_yields_each_event_in_a_single_chunk() -> None:
    """Removing event-boundary processing would merge consecutive API events."""
    stream = "\n\n".join(
        [
            f"event: workflow_started\ndata: {event_data('workflow_started', '开始分析')}",
            f"event: answer_chunk\ndata: {event_data('answer_chunk', '第一段答案')}",
        ]
    ) + "\n\n"

    events = [event async for event in parse_sse_chunks(chunked(stream))]

    assert [(event.event_type, event.message) for event in events] == [
        ("workflow_started", "开始分析"),
        ("answer_chunk", "第一段答案"),
    ]


@pytest.mark.asyncio
async def test_parse_sse_chunks_rejects_truncated_event_at_stream_end() -> None:
    """Removing interruption detection would make an incomplete stream look successful."""
    partial = f"event: answer_chunk\ndata: {event_data('answer_chunk', '未完成')}"

    with pytest.raises(SSEStreamInterrupted, match="连接中断"):
        async for _ in parse_sse_chunks(chunked(partial)):
            pass


@pytest.mark.asyncio
async def test_parse_sse_chunks_rejects_invalid_event_data() -> None:
    """Skipping payload validation would allow malformed server events into UI state."""
    malformed = "event: agent_started\ndata: not-json\n\n"

    with pytest.raises(SSEProtocolError, match="格式异常"):
        async for _ in parse_sse_chunks(chunked(malformed)):
            pass


@pytest.mark.asyncio
async def test_stream_analysis_posts_request_and_yields_events_incrementally() -> None:
    """Posting to a non-stream endpoint would leave the progress view without events."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                f"event: workflow_started\ndata: {event_data('workflow_started', '开始分析')}\n\n"
            ),
        )

    request_payload = {
        "product": {
            "name": "磁吸充电器",
            "category": "consumer_electronics",
            "purchase_cost_cny": "70",
        },
        "question": "市场如何？",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [
            event
            async for event in stream_analysis(
                request_payload,
                base_url="https://api.example.test",
                client=client,
            )
        ]

    assert captured == {
        "path": "/api/v1/analysis/stream",
        "payload": request_payload,
    }
    assert [(event.event_type, event.message) for event in events] == [
        ("workflow_started", "开始分析"),
    ]


@pytest.mark.asyncio
async def test_stream_analysis_sanitizes_http_failure() -> None:
    """Leaking an upstream response body would expose technical details to the UI."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503, text="internal details"))
    )

    with pytest.raises(APIClientError, match="暂时不可用"):
        async for _ in stream_analysis(
            {"product": {}, "question": "测试"},
            base_url="https://api.example.test",
            client=client,
        ):
            pass

    await client.aclose()


@pytest.mark.asyncio
async def test_resume_analysis_posts_fields_envelope_without_losing_requested_values() -> None:
    """Sending bare fields would violate the paused-workflow resume contract."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                f"event: workflow_started\ndata: {event_data('workflow_started', '继续分析')}\n\n"
            ),
        )

    fields = {"selling_price_usd": "29.99", "fba_fee_usd": "4.00"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = [
            event
            async for event in resume_analysis(
                "thread-1",
                fields,
                base_url="https://api.example.test",
                client=client,
            )
        ]

    assert captured == {
        "method": "POST",
        "path": "/api/v1/analysis/thread-1/resume",
        "payload": {"fields": fields},
    }
    assert [(event.event_type, event.message) for event in events] == [
        ("workflow_started", "继续分析"),
    ]


def test_business_event_message_hides_technical_agent_name() -> None:
    """Exposing raw agent identifiers would make progress unreadable to business users."""
    event = {
        "event_type": "agent_started",
        "message": "market started",
        "payload": {"agent": "market"},
    }

    assert business_event_message(event) == "正在分析市场数据"


def test_build_analysis_payload_keeps_blank_optional_fields_out_of_request() -> None:
    """Blank numeric strings must not make otherwise optional form fields fail API validation."""
    payload = build_analysis_payload(
        {
            "name": "磁吸充电器",
            "category": "consumer_electronics",
            "purchase_cost_cny": "70",
            "selling_price_usd": "",
            "weight_kg": None,
            "has_battery": True,
            "provided_documents": "UN38.3, MSDS",
        },
        "请分析市场",
    )

    assert payload == {
        "product": {
            "name": "磁吸充电器",
            "category": "consumer_electronics",
            "purchase_cost_cny": "70",
            "has_battery": True,
            "provided_documents": ["UN38.3", "MSDS"],
        },
        "question": "请分析市场",
    }


def test_trim_graph_limits_nodes_edges_and_drops_orphaned_edges() -> None:
    """Removing graph limits would let a large response overwhelm the single-page workbench."""
    nodes = [{"id": str(index), "label": f"节点{index}"} for index in range(35)]
    edges = [
        {"source": str(index % 29), "target": str((index + 1) % 29), "label": "关联"}
        for index in range(55)
    ]

    graph = trim_graph({"nodes": nodes, "edges": edges})

    assert len(graph["nodes"]) == 30
    assert len(graph["edges"]) == 50
    allowed_ids = {node["id"] for node in graph["nodes"]}
    assert all(
        edge["source"] in allowed_ids and edge["target"] in allowed_ids for edge in graph["edges"]
    )


def test_initialize_session_state_creates_current_session_only() -> None:
    """Without defaults, a fresh page would not be able to resume or render progress safely."""
    state: dict[str, object] = {}

    initialize_session_state(state)

    assert state == {
        "thread_id": None,
        "trace_id": None,
        "recent_product": None,
        "messages": [],
        "missing_fields": [],
        "graph_data": {"nodes": [], "edges": []},
        "progress": [],
        "answer_buffer": "",
    }


def test_apply_sse_event_preserves_thread_and_only_requested_missing_fields() -> None:
    """Replacing state wholesale would lose the original thread required for resume."""
    state: dict[str, object] = {
        "thread_id": "thread-1",
        "trace_id": None,
        "recent_product": None,
        "messages": [],
        "missing_fields": [],
        "graph_data": {"nodes": [], "edges": []},
        "progress": [],
        "answer_buffer": "",
    }
    event = {
        "thread_id": "thread-1",
        "trace_id": "trace-1",
        "event_type": "input_required",
        "message": "need cost details",
        "payload": {"missing_fields": ["selling_price_usd", "fba_fee_usd"]},
    }

    apply_sse_event(state, event)

    assert state["thread_id"] == "thread-1"
    assert state["trace_id"] == "trace-1"
    assert state["missing_fields"] == ["selling_price_usd", "fba_fee_usd"]
    assert state["progress"] == ["需要补充信息后继续分析"]


def test_begin_new_analysis_clears_previous_task_state_but_keeps_chat_history() -> None:
    """Leaving a prior thread or graph in state would mix a failed new task with old evidence."""
    state: dict[str, object] = {
        "thread_id": "old-thread",
        "trace_id": "old-trace",
        "recent_product": {"name": "旧商品"},
        "messages": [{"role": "assistant", "content": "旧回答"}],
        "missing_fields": ["selling_price_usd"],
        "graph_data": {"nodes": [{"id": "old"}], "edges": []},
        "progress": ["旧进度"],
        "answer_buffer": "旧答案片段",
    }
    product = {"name": "新商品", "category": "home_goods", "purchase_cost_cny": "80"}

    begin_new_analysis(state, product)

    assert state["thread_id"] is None
    assert state["trace_id"] is None
    assert state["recent_product"] == product
    assert state["missing_fields"] == []
    assert state["graph_data"] == {"nodes": [], "edges": []}
    assert state["progress"] == []
    assert state["answer_buffer"] == ""
    assert state["messages"] == [{"role": "assistant", "content": "旧回答"}]
