from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.graph.schema_registry import SchemaRegistry


class GeneratedCypher(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cypher: str = Field(min_length=1)
    params: dict[str, Any]
    purpose: str = Field(min_length=1)


class StructuredLLM(Protocol):
    async def generate_json(
        self,
        schema: type[GeneratedCypher],
        messages: Sequence[Mapping[str, str]],
    ) -> GeneratedCypher: ...


class CypherGenerator:
    def __init__(self, llm: StructuredLLM, schema: SchemaRegistry) -> None:
        self.llm = llm
        self.schema = schema

    async def generate(
        self,
        question: str,
        entity_matches: Sequence[Mapping[str, Any]],
        purpose: str,
        feedback: str | None = None,
    ) -> GeneratedCypher:
        system_prompt = (
            "你是 CrossPilot 的 Neo4j 查询生成器。仅输出 JSON 文本，字段为 cypher、params、"
            "purpose。Cypher 必须是单条、参数化、只读查询；不得将用户文本拼入 Cypher；"
            "必须包含 LIMIT，且 LIMIT <= 50；只可使用给定 Schema。"
            "每个节点必须显式标注允许的标签，每条关系必须显式标注允许的类型；"
            "禁止字符串字面量，业务条件值全部放入 params；"
            "RETURN 不得返回完整节点或关系，只能返回允许属性或受控聚合并使用明确别名。\n"
            f"Schema version: {self.schema.version_hash}\n{self.schema.compact_text()}"
        )
        context: dict[str, Any] = {
            "question": question,
            "purpose": purpose,
            "resolved_entities": list(entity_matches),
        }
        if feedback:
            context["previous_error"] = feedback
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]
        return await self.llm.generate_json(GeneratedCypher, messages)
