from typing import Any, Literal

from pydantic import BaseModel, Field


class SSEEvent(BaseModel):
    trace_id: str
    thread_id: str
    event_type: Literal[
        "workflow_started",
        "intent_identified",
        "agents_selected",
        "agent_started",
        "agent_completed",
        "cypher_retry",
        "input_required",
        "validation_completed",
        "answer_chunk",
        "workflow_completed",
        "workflow_failed",
    ]
    message: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)
