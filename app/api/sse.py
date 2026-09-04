from __future__ import annotations

import json

from app.contracts.events import SSEEvent


class SSEEncoder:
    """Encode public workflow events using the server-sent events wire format."""

    @staticmethod
    def encode(event: SSEEvent) -> str:
        data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        return f"event: {event.event_type}\ndata: {data}\n\n"
