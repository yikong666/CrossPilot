from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pydantic import BaseModel

from app.core.errors import StructuredOutputError
from app.graph.cypher_generator import CypherGenerator, GeneratedCypher
from app.graph.schema_registry import SchemaRegistry
from app.services.embedding import EmbeddingService
from app.services.llm import LLMClient


class ExampleOutput(BaseModel):
    answer: int


class FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self.contents = iter(contents)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=next(self.contents)))]
        )


def fake_openai(contents: list[str]) -> tuple[Any, FakeCompletions]:
    completions = FakeCompletions(contents)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


@pytest.mark.asyncio
async def test_generate_json_uses_messages_without_tool_calling() -> None:
    openai_client, completions = fake_openai(['{"answer": 7}'])
    client = LLMClient(model="fake-model", client=openai_client)

    result = await client.generate_json(ExampleOutput, [{"role": "user", "content": "answer"}])

    assert result == ExampleOutput(answer=7)
    assert completions.requests[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in completions.requests[0]
    assert "functions" not in completions.requests[0]


@pytest.mark.asyncio
async def test_generate_json_retries_twice_with_precise_validation_feedback() -> None:
    openai_client, completions = fake_openai(["not-json", '{"answer":"wrong"}', '{"answer": 9}'])
    client = LLMClient(model="fake-model", client=openai_client)

    result = await client.generate_json(ExampleOutput, [{"role": "user", "content": "answer"}])

    assert result.answer == 9
    assert len(completions.requests) == 3
    second_messages = completions.requests[1]["messages"]
    third_messages = completions.requests[2]["messages"]
    assert "json_invalid" in second_messages[-1]["content"]
    assert "int_parsing" in third_messages[-1]["content"]


@pytest.mark.asyncio
async def test_generate_json_raises_after_initial_call_and_two_retries() -> None:
    openai_client, completions = fake_openai(["{}", "{}", "{}"])
    client = LLMClient(model="fake-model", client=openai_client)

    with pytest.raises(StructuredOutputError, match="after 3 attempts"):
        await client.generate_json(ExampleOutput, [{"role": "user", "content": "answer"}])

    assert len(completions.requests) == 3


class FakeStructuredLLM:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def generate_json(
        self, schema: type[GeneratedCypher], messages: list[dict[str, str]]
    ) -> GeneratedCypher:
        assert schema is GeneratedCypher
        self.messages = messages
        return GeneratedCypher(
            cypher="MATCH (p:Product) WHERE p.product_id = $entity_id RETURN p.name LIMIT 5",
            params={"entity_id": "product-1"},
            purpose="competitor",
        )


@pytest.mark.asyncio
async def test_cypher_generator_supplies_schema_entities_and_repair_feedback() -> None:
    llm = FakeStructuredLLM()
    generator = CypherGenerator(llm, SchemaRegistry())

    generated = await generator.generate(
        question="查找相似商品",
        entity_matches=[
            {
                "entity_id": "product-1",
                "label": "Product",
                "display_name": "USB-C Hub",
                "score": 0.91,
                "needs_confirmation": False,
            }
        ],
        purpose="competitor",
        feedback="Unknown property for Product: revenue",
    )

    assert generated.params == {"entity_id": "product-1"}
    prompt = "\n".join(message["content"] for message in llm.messages)
    assert "LIMIT <= 50" in prompt
    assert "只读" in prompt
    assert "参数" in prompt
    assert "Product(" in prompt
    assert "product_id" in prompt
    assert "product-1" in prompt
    assert "Unknown property for Product: revenue" in prompt


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], bool, bool]] = []

    def encode(
        self,
        texts: list[str],
        *,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
    ) -> np.ndarray:
        self.calls.append((texts, normalize_embeddings, convert_to_numpy))
        vector = np.zeros((len(texts), 1024), dtype=np.float32)
        vector[:, 0] = 1.0
        return vector


def test_embedding_service_uses_injected_encoder_without_downloading_model() -> None:
    encoder = FakeEncoder()
    service = EmbeddingService(encoder=encoder)

    vectors = service.embed(["USB-C Hub", "扩展坞"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    assert vectors[0][0] == 1.0
    assert encoder.calls == [(["USB-C Hub", "扩展坞"], True, True)]
