from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.supervisor import Supervisor
from app.contracts import AgentName, AnalysisRequest, ProductInput
from app.core.errors import StructuredOutputError
from app.services.llm import LLMClient


def _request(question: str) -> AnalysisRequest:
    return AnalysisRequest(
        product=ProductInput(
            name="Magnetic charger",
            category="consumer_electronics",
            purchase_cost_cny=Decimal("70"),
        ),
        question=question,
    )


class FakeStructuredLLM:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self.payload = payload

    async def generate_json(self, schema: type[Any], messages: list[dict[str, str]]) -> Any:
        del messages
        if isinstance(self.payload, Exception):
            raise self.payload
        return schema.model_validate(self.payload)


@pytest.mark.asyncio
async def test_supervisor_routes_market_question_to_one_market_task() -> None:
    supervisor = Supervisor(
        FakeStructuredLLM(
            {
                "tasks": [
                    {
                        "agent": "market",
                        "objective": "Assess category demand and price band",
                        "required_fields": ["demand_level", "price_band"],
                    }
                ],
                "market_entry_requested": False,
            }
        )
    )

    plan = await supervisor.plan(_request("这个类目市场需求怎么样？"))

    assert [task.agent for task in plan.tasks] == [AgentName.MARKET]
    assert plan.tasks[0].task_id == "market-1"
    assert plan.market_entry_requested is False


@pytest.mark.asyncio
async def test_supervisor_adds_pricing_fallback_without_removing_model_market_task() -> None:
    supervisor = Supervisor(
        FakeStructuredLLM(
            {
                "tasks": [
                    {
                        "agent": "market",
                        "objective": "Assess market",
                        "required_fields": [],
                    }
                ],
                "market_entry_requested": False,
            }
        )
    )

    plan = await supervisor.plan(_request("分析市场，并计算利润"))

    assert [task.agent for task in plan.tasks] == [AgentName.MARKET, AgentName.PRICING]


@pytest.mark.asyncio
async def test_supervisor_adds_compliance_fallback_to_model_selected_competitor() -> None:
    supervisor = Supervisor(
        FakeStructuredLLM(
            {
                "tasks": [
                    {
                        "agent": "competitor",
                        "objective": "Compare alternatives",
                        "required_fields": [],
                    }
                ],
                "market_entry_requested": False,
            }
        )
    )

    plan = await supervisor.plan(_request("比较竞品并检查认证材料"))

    assert [task.agent for task in plan.tasks] == [
        AgentName.COMPETITOR,
        AgentName.COMPLIANCE,
    ]


@pytest.mark.asyncio
async def test_supervisor_accepts_all_four_specialists_for_market_entry_question() -> None:
    tasks = [
        {"agent": agent.value, "objective": f"Run {agent.value}", "required_fields": []}
        for agent in AgentName
    ]
    supervisor = Supervisor(
        FakeStructuredLLM({"tasks": tasks, "market_entry_requested": True})
    )

    plan = await supervisor.plan(_request("是否值得进入美国 Amazon？"))

    assert {task.agent for task in plan.tasks} == set(AgentName)
    assert plan.market_entry_requested is True


@pytest.mark.asyncio
async def test_supervisor_allows_no_tasks_for_unrelated_question() -> None:
    supervisor = Supervisor(
        FakeStructuredLLM({"tasks": [], "market_entry_requested": False})
    )

    plan = await supervisor.plan(_request("今天天气怎么样？"))

    assert plan.tasks == []


@pytest.mark.asyncio
async def test_supervisor_propagates_structured_output_failure() -> None:
    supervisor = Supervisor(FakeStructuredLLM(StructuredOutputError("invalid output")))

    with pytest.raises(StructuredOutputError, match="invalid output"):
        await supervisor.plan(_request("分析市场"))


@pytest.mark.asyncio
async def test_supervisor_replans_only_requested_gaps() -> None:
    supervisor = Supervisor(
        FakeStructuredLLM(
            {
                "tasks": [
                    {
                        "agent": "compliance",
                        "objective": "Fill missing compliance evidence",
                        "required_fields": ["missing"],
                    }
                ],
                "market_entry_requested": True,
            }
        )
    )

    plan = await supervisor.plan(
        _request("判断是否值得进入"), gaps=["compliance evidence is incomplete"]
    )

    assert [task.agent for task in plan.tasks] == [AgentName.COMPLIANCE]


class CapturingCompletions:
    def __init__(self, contents: list[str]) -> None:
        self._contents = iter(contents)
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=next(self._contents)))
            ]
        )


def _capturing_llm(
    contents: list[str], *, retries: int = 0
) -> tuple[LLMClient, CapturingCompletions]:
    completions = CapturingCompletions(contents)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return (
        LLMClient(
            model="fake-model",
            client=client,
            max_parse_retries=retries,
        ),
        completions,
    )


@pytest.mark.asyncio
async def test_supervisor_sends_actual_json_schema_to_llm_transport() -> None:
    llm, completions = _capturing_llm(
        ['{"tasks": [], "market_entry_requested": false}']
    )

    await Supervisor(llm).plan(_request("分析市场"))

    prompt = "\n".join(
        message["content"] for message in completions.requests[0]["messages"]
    )
    assert '"tasks"' in prompt
    assert '"agent"' in prompt
    assert '"market_entry_requested"' in prompt
    assert '"required"' in prompt


@pytest.mark.asyncio
async def test_supervisor_rejects_missing_tasks_field_in_structured_output() -> None:
    llm, _ = _capturing_llm(['{"market_entry_requested": false}'])

    with pytest.raises(StructuredOutputError):
        await Supervisor(llm).plan(_request("分析市场"))
