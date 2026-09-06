from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field

from app.contracts.agents import AgentName, AgentTask
from app.contracts.api import AnalysisRequest

OutputT = TypeVar("OutputT", bound=BaseModel)


class StructuredLLM(Protocol):
    async def generate_json(
        self,
        schema: type[OutputT],
        messages: Sequence[Mapping[str, str]],
    ) -> OutputT: ...


class _PlannedTask(BaseModel):
    agent: AgentName
    objective: str = Field(min_length=1)
    required_fields: list[str] = Field(default_factory=list)


class _SupervisorOutput(BaseModel):
    tasks: list[_PlannedTask]
    market_entry_requested: bool = False


class SupervisorPlan(BaseModel):
    tasks: list[AgentTask] = Field(default_factory=list)
    market_entry_requested: bool = False


_FALLBACK_KEYWORDS: dict[AgentName, tuple[str, ...]] = {
    AgentName.PRICING: (
        "利润",
        "毛利",
        "售价",
        "成本",
        "profit",
        "margin",
        "selling price",
        "target price",
        "cost",
    ),
    AgentName.COMPLIANCE: ("认证", "检测", "材料", "合规"),
}
_PRICING_REALIZED_OUTPUT_FIELDS = ("profit", "margin")
_PRICING_REALIZED_OUTPUT_KEYWORDS = ("利润", "毛利", "profit", "margin")
_PRICING_TARGET_KEYWORDS = (
    "目标利润率",
    "目标毛利率",
    "目标售价",
    "建议售价",
    "推荐售价",
    "target margin",
    "target gross margin",
    "target profit",
    "target price",
)
_PRICING_CURRENT_OUTPUT_KEYWORDS = (
    "实际利润",
    "当前利润",
    "实际毛利",
    "当前毛利",
    "actual profit",
    "current profit",
    "actual margin",
    "current margin",
    "actual gross profit",
    "current gross profit",
    "actual gross margin",
    "current gross margin",
)


class Supervisor:
    """Create bounded specialist tasks from validated model JSON."""

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    async def plan(
        self,
        request: AnalysisRequest,
        *,
        gaps: list[str] | None = None,
    ) -> SupervisorPlan:
        output = await self._llm.generate_json(
            _SupervisorOutput,
            self._messages(request, gaps or []),
        )
        planned = list(output.tasks)
        if not gaps:
            planned = self._add_deterministic_fallbacks(planned, request.question)
        planned = self._add_deterministic_pricing_requirements(planned, request.question)
        planned = self._deduplicate(planned)
        tasks = [
            AgentTask(
                task_id=f"{task.agent.value}-{position}",
                agent=task.agent,
                objective=task.objective,
                required_fields=task.required_fields,
            )
            for position, task in enumerate(planned, start=1)
        ]
        return SupervisorPlan(
            tasks=tasks,
            market_entry_requested=output.market_entry_requested,
        )

    @staticmethod
    def _messages(request: AnalysisRequest, gaps: list[str]) -> list[dict[str, str]]:
        scope = (
            "Only plan tasks that address these validation gaps: " + "; ".join(gaps)
            if gaps
            else "Plan only the specialist tasks needed to answer the user question."
        )
        return [
            {
                "role": "system",
                "content": (
                    "You are the CrossPilot supervisor. Return JSON matching the supplied schema. "
                    "Allowed agents are market, competitor, pricing, and compliance. "
                    "Do not perform analysis or query data. "
                    f"{scope}\nJSON Schema: "
                    f"{json.dumps(_SupervisorOutput.model_json_schema(), ensure_ascii=False)}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {request.question}\n"
                    f"Product: {request.product.model_dump_json()}"
                ),
            },
        ]

    @staticmethod
    def _add_deterministic_fallbacks(
        tasks: list[_PlannedTask], question: str
    ) -> list[_PlannedTask]:
        normalized_question = question.casefold()
        selected = {task.agent for task in tasks}
        result = list(tasks)
        for agent, keywords in _FALLBACK_KEYWORDS.items():
            needs_fallback = any(keyword in normalized_question for keyword in keywords)
            if agent not in selected and needs_fallback:
                result.append(
                    _PlannedTask(
                        agent=agent,
                        objective=f"Address the user's {agent.value} question.",
                    )
                )
        return result

    @staticmethod
    def _add_deterministic_pricing_requirements(
        tasks: list[_PlannedTask], question: str
    ) -> list[_PlannedTask]:
        requires_realized_outputs = Supervisor._requires_realized_pricing_outputs(question)
        enriched_tasks: list[_PlannedTask] = []
        for task in tasks:
            if task.agent is not AgentName.PRICING:
                enriched_tasks.append(task)
                continue
            required_fields = list(dict.fromkeys(task.required_fields))
            if requires_realized_outputs:
                required_fields = list(
                    dict.fromkeys([*required_fields, *_PRICING_REALIZED_OUTPUT_FIELDS])
                )
            enriched_tasks.append(task.model_copy(update={"required_fields": required_fields}))
        return enriched_tasks

    @staticmethod
    def _requires_realized_pricing_outputs(question: str) -> bool:
        normalized_question = question.casefold()
        mentions_realized_output = any(
            keyword in normalized_question for keyword in _PRICING_REALIZED_OUTPUT_KEYWORDS
        )
        if not mentions_realized_output:
            return False
        is_target_pricing_request = any(
            keyword in normalized_question for keyword in _PRICING_TARGET_KEYWORDS
        )
        asks_for_current_output = any(
            keyword in normalized_question for keyword in _PRICING_CURRENT_OUTPUT_KEYWORDS
        )
        return asks_for_current_output or not is_target_pricing_request

    @staticmethod
    def _deduplicate(tasks: list[_PlannedTask]) -> list[_PlannedTask]:
        unique: list[_PlannedTask] = []
        seen: set[AgentName] = set()
        for task in tasks:
            if task.agent in seen:
                continue
            seen.add(task.agent)
            unique.append(task)
        return unique
