from __future__ import annotations

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
    tasks: list[_PlannedTask] = Field(default_factory=list)
    market_entry_requested: bool = False


class SupervisorPlan(BaseModel):
    tasks: list[AgentTask] = Field(default_factory=list)
    market_entry_requested: bool = False


_FALLBACK_KEYWORDS: dict[AgentName, tuple[str, ...]] = {
    AgentName.PRICING: ("利润", "毛利", "售价", "成本"),
    AgentName.COMPLIANCE: ("认证", "检测", "材料", "合规"),
}


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
                    f"{scope}"
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
        selected = {task.agent for task in tasks}
        result = list(tasks)
        for agent, keywords in _FALLBACK_KEYWORDS.items():
            if agent not in selected and any(keyword in question for keyword in keywords):
                result.append(
                    _PlannedTask(
                        agent=agent,
                        objective=f"Address the user's {agent.value} question.",
                    )
                )
        return result

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
