from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.supervisor import StructuredLLM
from app.contracts.agents import AgentResult


class StrategyRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["enter", "cautious", "do_not_enter"]
    reasons: list[str] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    def to_answer(self) -> str:
        labels = {
            "enter": "建议进入",
            "cautious": "谨慎进入",
            "do_not_enter": "暂不建议进入",
        }
        sections = [f"结论：{labels[self.decision]}。", f"依据：{'；'.join(self.reasons)}。"]
        if self.risks:
            sections.append(f"风险：{'；'.join(self.risks)}。")
        if self.next_actions:
            sections.append(f"下一步：{'；'.join(self.next_actions)}。")
        return " ".join(sections)


class StrategyAgent:
    """Synthesize only the evidence already returned by selected specialists."""

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    async def run(
        self,
        question: str,
        results: Mapping[str, AgentResult],
    ) -> StrategyRecommendation:
        payload = {
            name: result.model_dump(mode="json") for name, result in results.items()
        }
        schema_json = json.dumps(
            StrategyRecommendation.model_json_schema(), ensure_ascii=False
        )
        return await self._llm.generate_json(
            StrategyRecommendation,
            [
                {
                    "role": "system",
                    "content": (
                        "Synthesize a CrossPilot market-entry recommendation using only the "
                        "provided specialist results and evidence. Allowed decisions: enter, "
                        "cautious, do_not_enter. Do not create a score or invent data.\n"
                        "JSON Schema: "
                        f"{schema_json}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\nSpecialist results: {payload}",
                },
            ],
        )
