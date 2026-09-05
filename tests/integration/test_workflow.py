from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.result_validator import ResultValidator
from app.agents.strategy import StrategyAgent, StrategyRecommendation
from app.agents.supervisor import SupervisorPlan
from app.contracts import (
    AgentName,
    AgentResult,
    AgentTask,
    AnalysisRequest,
    EvidenceRecord,
    ProductInput,
)
from app.workflow.builder import WorkflowDependencies, build_workflow
from app.workflow.runtime import LangGraphWorkflowRuntime


class CountingValidationLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, schema: type[Any], messages: Any) -> Any:
        del messages
        self.calls += 1
        return schema.model_validate({"action": "pass"})


@pytest.mark.asyncio
async def test_validator_deterministic_missing_input_precedes_semantic_model() -> None:
    llm = CountingValidationLLM()
    validator = ResultValidator(llm)
    task = AgentTask(
        task_id="pricing-1",
        agent=AgentName.PRICING,
        objective="Calculate profit",
    )
    result = AgentResult(
        agent=AgentName.PRICING,
        status="need_input",
        summary="Need a selling price",
        missing_fields=["selling_price_usd"],
    )

    decision = await validator.validate([task], {"pricing": result})

    assert decision.action == "need_input"
    assert decision.missing_fields == ["selling_price_usd"]
    assert decision.follow_up_question == "请补充以下信息：selling_price_usd"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_validator_replans_missing_evidence_before_semantic_model() -> None:
    llm = CountingValidationLLM()
    validator = ResultValidator(llm)
    task = AgentTask(
        task_id="market-1",
        agent=AgentName.MARKET,
        objective="Assess demand",
        required_fields=["demand_level"],
    )
    result = AgentResult(
        agent=AgentName.MARKET,
        status="success",
        summary="Demand is high",
        data={"demand_level": "high"},
    )

    decision = await validator.validate([task], {"market": result})

    assert decision.action == "replan"
    assert decision.gaps == ["market: missing evidence"]
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_validator_replans_when_evidence_contains_no_query_or_calculator_rows() -> None:
    llm = CountingValidationLLM()
    validator = ResultValidator(llm)
    task = AgentTask(
        task_id="market-1",
        agent=AgentName.MARKET,
        objective="Assess demand",
    )
    result = AgentResult(
        agent=AgentName.MARKET,
        status="success",
        summary="Demand is high",
        evidence=[EvidenceRecord(source_type="neo4j", summary="empty query")],
    )

    decision = await validator.validate([task], {"market": result})

    assert decision.action == "replan"
    assert decision.gaps == ["market: evidence rows empty"]
    assert llm.calls == 0


class FakeStrategyLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def generate_json(self, schema: type[Any], messages: Any) -> Any:
        del messages
        return schema.model_validate(self.payload)


@pytest.mark.asyncio
async def test_strategy_accepts_only_bounded_decisions_and_formats_answer() -> None:
    strategy = StrategyAgent(
        FakeStrategyLLM(
            {
                "decision": "cautious",
                "reasons": ["Demand proxy is positive"],
                "risks": ["Compliance material is missing"],
                "next_actions": ["Request the material from the factory"],
            }
        )
    )
    results = {
        "market": AgentResult(
            agent=AgentName.MARKET,
            status="success",
            summary="Demand proxy is positive",
            evidence=[EvidenceRecord(source_type="neo4j", summary="market evidence")],
        ),
        "compliance": AgentResult(
            agent=AgentName.COMPLIANCE,
            status="success",
            summary="Compliance material is missing",
            evidence=[EvidenceRecord(source_type="neo4j", summary="rule evidence")],
        ),
    }

    recommendation = await strategy.run("Should we enter?", results)

    assert recommendation.decision == "cautious"
    assert "谨慎进入" in recommendation.to_answer()
    assert "Compliance material is missing" in recommendation.to_answer()


@pytest.mark.asyncio
async def test_strategy_rejects_unapproved_decision_or_score() -> None:
    strategy = StrategyAgent(
        FakeStrategyLLM(
            {
                "decision": "strong_buy",
                "reasons": ["invented"],
                "risks": [],
                "next_actions": [],
                "score": 99,
            }
        )
    )

    with pytest.raises(ValidationError):
        await strategy.run("Should we enter?", {})


def _analysis_request(
    question: str, *, selling_price: Decimal | None = Decimal("25")
) -> AnalysisRequest:
    return AnalysisRequest(
        product=ProductInput(
            name="Magnetic charger",
            category="consumer_electronics",
            purchase_cost_cny=Decimal("70"),
            selling_price_usd=selling_price,
        ),
        question=question,
    )


def _task(agent: AgentName) -> AgentTask:
    return AgentTask(
        task_id=f"{agent.value}-1",
        agent=agent,
        objective=f"Run {agent.value}",
    )


def _success(agent: AgentName, *, graph_node_ids: list[str] | None = None) -> AgentResult:
    return AgentResult(
        agent=agent,
        status="success",
        summary=f"{agent.value} completed",
        evidence=[
            EvidenceRecord(
                source_type="neo4j",
                summary=f"{agent.value} evidence",
                rows=[{"agent": agent.value}],
                graph_node_ids=graph_node_ids or [],
            )
        ],
    )


class StaticSupervisor:
    def __init__(self, agents: list[AgentName], *, market_entry: bool = False) -> None:
        self.agents = agents
        self.market_entry = market_entry
        self.calls: list[list[str]] = []

    async def plan(
        self, request: AnalysisRequest, *, gaps: list[str] | None = None
    ) -> SupervisorPlan:
        del request
        self.calls.append(list(gaps or []))
        return SupervisorPlan(
            tasks=[_task(agent) for agent in self.agents],
            market_entry_requested=self.market_entry,
        )


class SequenceAgent:
    def __init__(self, results: list[AgentResult]) -> None:
        self.results = results
        self.calls = 0

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        del task, product
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


class RecordingStrategy:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self, question: str, results: Mapping[str, AgentResult]
    ) -> StrategyRecommendation:
        del question, results
        self.calls += 1
        return StrategyRecommendation(
            decision="cautious",
            reasons=["Combined evidence requires caution"],
        )


def _runtime(
    supervisor: StaticSupervisor,
    agents: Mapping[AgentName, Any],
    *,
    strategy: RecordingStrategy | None = None,
) -> tuple[LangGraphWorkflowRuntime, RecordingStrategy]:
    strategy = strategy or RecordingStrategy()
    graph = build_workflow(
        WorkflowDependencies(
            supervisor=supervisor,
            validator=ResultValidator(),
            strategy=strategy,
            agents=agents,
        )
    )
    return LangGraphWorkflowRuntime(graph), strategy


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_count", [1, 4])
async def test_workflow_executes_the_selected_one_or_four_specialists(
    agent_count: int,
) -> None:
    selected = list(AgentName)[:agent_count]
    runtime, _ = _runtime(
        StaticSupervisor(selected),
        {agent: SequenceAgent([_success(agent)]) for agent in selected},
    )

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Run selected analysis"),
            trace_id=f"trace-{agent_count}",
            thread_id=f"thread-{agent_count}",
        )
    ]

    completed_agents = {
        event.payload["agent"]
        for event in events
        if event.event_type == "agent_completed"
    }
    assert completed_agents == {agent.value for agent in selected}
    assert events[-1].event_type == "workflow_completed"


class CoordinatedAgent:
    def __init__(self, agent: AgentName, release: asyncio.Event) -> None:
        self.agent = agent
        self.release = release
        self.started = asyncio.Event()

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        del task, product
        self.started.set()
        await self.release.wait()
        return _success(self.agent)


@pytest.mark.asyncio
async def test_workflow_fans_out_agents_before_any_agent_completes() -> None:
    release = asyncio.Event()
    market = CoordinatedAgent(AgentName.MARKET, release)
    competitor = CoordinatedAgent(AgentName.COMPETITOR, release)
    runtime, strategy = _runtime(
        StaticSupervisor([AgentName.MARKET, AgentName.COMPETITOR]),
        {AgentName.MARKET: market, AgentName.COMPETITOR: competitor},
    )
    events: list[Any] = []

    async def consume() -> None:
        async for event in runtime.stream(
            _analysis_request("Compare market and competitors"),
            trace_id="trace-parallel",
            thread_id="thread-parallel",
        ):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(
        asyncio.gather(market.started.wait(), competitor.started.wait()), timeout=1
    )
    assert not any(event.event_type == "agent_completed" for event in events)
    release.set()
    await asyncio.wait_for(consumer, timeout=1)

    starts = [event.timestamp for event in events if event.event_type == "agent_started"]
    completions = [
        event.timestamp for event in events if event.event_type == "agent_completed"
    ]
    assert len(starts) == 2
    assert max(starts) <= min(completions)
    assert strategy.calls == 1


class PricingNeedsSellingPrice:
    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        del task
        if product.selling_price_usd is None:
            return AgentResult(
                agent=AgentName.PRICING,
                status="need_input",
                summary="Selling price is required",
                missing_fields=["selling_price_usd"],
            )
        return _success(AgentName.PRICING)


@pytest.mark.asyncio
async def test_workflow_interrupts_for_input_and_resumes_same_thread(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supervisor = StaticSupervisor([AgentName.PRICING])
    runtime, _ = _runtime(
        supervisor,
        {AgentName.PRICING: PricingNeedsSellingPrice()},
    )

    first_events = [
        event
        async for event in runtime.stream(
            _analysis_request("Calculate profit", selling_price=None),
            trace_id="trace-resume",
            thread_id="thread-resume",
        )
    ]
    resumed_events = [
        event
        async for event in runtime.resume(
            thread_id="thread-resume",
            fields={"selling_price_usd": "25"},
            trace_id="trace-resumed",
        )
    ]

    assert first_events[-1].event_type == "input_required"
    assert first_events[-1].payload["missing_fields"] == ["selling_price_usd"]
    assert resumed_events[-1].event_type == "workflow_completed"
    assert {event.trace_id for event in resumed_events} == {"trace-resumed"}
    assert supervisor.calls == [[], []]
    assert "Deserializing unregistered type" not in caplog.text


@pytest.mark.asyncio
async def test_workflow_replans_only_gaps_then_uses_supplemented_result() -> None:
    supervisor = StaticSupervisor([AgentName.MARKET])
    agent = SequenceAgent(
        [
            AgentResult(
                agent=AgentName.MARKET,
                status="failed",
                summary="No rows",
                errors=["no_rows"],
            ),
            _success(AgentName.MARKET),
        ]
    )
    runtime, _ = _runtime(supervisor, {AgentName.MARKET: agent})

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Assess market"),
            trace_id="trace-replan",
            thread_id="thread-replan",
        )
    ]

    assert events[-1].event_type == "workflow_completed"
    assert supervisor.calls == [[], ["market: specialist failed"]]
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_workflow_stops_after_two_replans() -> None:
    supervisor = StaticSupervisor([AgentName.MARKET])
    agent = SequenceAgent(
        [
            AgentResult(
                agent=AgentName.MARKET,
                status="failed",
                summary="No rows",
                errors=["no_rows"],
            )
        ]
    )
    runtime, _ = _runtime(supervisor, {AgentName.MARKET: agent})

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Assess market"),
            trace_id="trace-cap",
            thread_id="thread-cap",
        )
    ]

    assert events[-1].event_type == "workflow_failed"
    assert supervisor.calls == [
        [],
        ["market: specialist failed"],
        ["market: specialist failed"],
    ]
    assert agent.calls == 3


@pytest.mark.asyncio
async def test_strategy_is_not_called_for_single_specialist() -> None:
    supervisor = StaticSupervisor([AgentName.MARKET])
    runtime, strategy = _runtime(
        supervisor,
        {AgentName.MARKET: SequenceAgent([_success(AgentName.MARKET)])},
    )

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Assess market"),
            trace_id="trace-single",
            thread_id="thread-single",
        )
    ]

    answers = [event.message for event in events if event.event_type == "answer_chunk"]
    assert strategy.calls == 0
    assert answers == ["market completed"]


@pytest.mark.asyncio
async def test_strategy_is_called_for_explicit_market_entry_with_one_specialist() -> None:
    supervisor = StaticSupervisor([AgentName.MARKET], market_entry=True)
    runtime, strategy = _runtime(
        supervisor,
        {AgentName.MARKET: SequenceAgent([_success(AgentName.MARKET)])},
    )

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Should this product launch?"),
            trace_id="trace-entry",
            thread_id="thread-entry",
        )
    ]

    assert events[-1].event_type == "workflow_completed"
    assert strategy.calls == 1


@pytest.mark.asyncio
async def test_failed_parallel_agent_does_not_remove_successful_agent_evidence() -> None:
    supervisor = StaticSupervisor([AgentName.MARKET, AgentName.COMPETITOR])
    failed_competitor = AgentResult(
        agent=AgentName.COMPETITOR,
        status="failed",
        summary="Competitor query failed",
        errors=["query_failed"],
    )
    runtime, _ = _runtime(
        supervisor,
        {
            AgentName.MARKET: SequenceAgent(
                [_success(AgentName.MARKET, graph_node_ids=["market-node"])]
            ),
            AgentName.COMPETITOR: SequenceAgent([failed_competitor]),
        },
    )

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Compare market and competitors"),
            trace_id="trace-partial",
            thread_id="thread-partial",
        )
    ]
    graph = await runtime.get_graph("thread-partial")

    assert events[-1].event_type == "workflow_failed"
    assert graph == {"nodes": [{"id": "market-node"}], "edges": []}


class ExplodingStrategy:
    async def run(
        self, question: str, results: Mapping[str, AgentResult]
    ) -> StrategyRecommendation:
        del question, results
        raise RuntimeError("secret backend detail")


@pytest.mark.asyncio
async def test_runtime_masks_internal_exception_details() -> None:
    selected = [AgentName.MARKET, AgentName.COMPETITOR]
    graph = build_workflow(
        WorkflowDependencies(
            supervisor=StaticSupervisor(selected),
            validator=ResultValidator(),
            strategy=ExplodingStrategy(),
            agents={agent: SequenceAgent([_success(agent)]) for agent in selected},
        )
    )
    runtime = LangGraphWorkflowRuntime(graph)

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Compare market and competitors"),
            trace_id="trace-mask",
            thread_id="thread-mask",
        )
    ]

    assert events[-1].event_type == "workflow_failed"
    assert "secret backend detail" not in events[-1].message
