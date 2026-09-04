from __future__ import annotations

import json

from app.api.sse import SSEEncoder
from app.contracts.events import SSEEvent


def test_sse_encoder_emits_event_and_utf8_json_data_block() -> None:
    event = SSEEvent(
        trace_id="trace-1",
        thread_id="thread-1",
        event_type="answer_chunk",
        message="正在生成答案",
        timestamp="2026-09-04T16:00:00+08:00",
        payload={"text": "中文内容"},
    )

    encoded = SSEEncoder.encode(event)

    event_line, data_line, terminator = encoded.split("\n", maxsplit=2)
    assert event_line == "event: answer_chunk"
    assert terminator == "\n"
    assert json.loads(data_line.removeprefix("data: ")) == {
        "trace_id": "trace-1",
        "thread_id": "thread-1",
        "event_type": "answer_chunk",
        "message": "正在生成答案",
        "timestamp": "2026-09-04T16:00:00+08:00",
        "payload": {"text": "中文内容"},
    }


def test_sse_encoder_does_not_escape_utf8_message_text() -> None:
    event = SSEEvent(
        trace_id="trace-1",
        thread_id="thread-1",
        event_type="workflow_completed",
        message="分析完成",
        timestamp="2026-09-04T16:00:00+08:00",
    )

    encoded = SSEEncoder.encode(event)

    assert "分析完成" in encoded
    assert "\\u5206" not in encoded
