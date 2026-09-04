from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.errors import StructuredOutputError

OutputT = TypeVar("OutputT", bound=BaseModel)


class LLMClient:
    """OpenAI-compatible chat client for validated JSON text outputs."""

    def __init__(
        self,
        model: str | None = None,
        client: Any | None = None,
        *,
        max_parse_retries: int = 2,
    ) -> None:
        if client is None:
            settings = get_settings()
            client = AsyncOpenAI(
                api_key=settings.llm_api_key.get_secret_value(),
                base_url=settings.llm_base_url,
            )
            model = model or settings.llm_model
        if model is None:
            raise ValueError("model is required when constructing an LLM client")
        self.model = model
        self.client: Any = client
        self.max_parse_retries = max_parse_retries

    async def generate_json(
        self,
        schema: type[OutputT],
        messages: Sequence[Mapping[str, str]],
    ) -> OutputT:
        working_messages = [dict(message) for message in messages]
        last_error = "unknown structured-output error"
        for attempt in range(self.max_parse_retries + 1):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[dict(message) for message in working_messages],
                response_format={"type": "json_object"},
            )
            content = cast(str | None, response.choices[0].message.content)
            try:
                if content is None:
                    raise ValueError("model returned empty content")
                return schema.model_validate_json(content)
            except (ValidationError, ValueError) as exc:
                if isinstance(exc, ValidationError):
                    last_error = json.dumps(exc.errors(include_url=False), ensure_ascii=False)
                else:
                    last_error = str(exc)
                if attempt >= self.max_parse_retries:
                    break
                working_messages.extend(
                    [
                        {"role": "assistant", "content": content or ""},
                        {
                            "role": "user",
                            "content": (
                                "The previous response was invalid JSON for the required schema. "
                                f"Validation error: {last_error}. Return corrected JSON text only."
                            ),
                        },
                    ]
                )
        attempts = self.max_parse_retries + 1
        raise StructuredOutputError(
            f"Structured output remained invalid after {attempts} attempts: {last_error}"
        )
