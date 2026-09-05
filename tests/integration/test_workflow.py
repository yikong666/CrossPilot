from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.competitor import CompetitorAgent
from app.agents.compliance import ComplianceAgent
from app.agents.market import MarketAgent
from app.agents.pricing import PricingAgent
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
from app.services.llm import LLMClient
from app.services.pricing_calculator import PricingResult
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

    decision = await validator.validate(
        _analysis_request("Calculate profit"), [task], {"pricing": result}
    )

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

    decision = await validator.validate(
        _analysis_request("Assess demand"), [task], {"market": result}
    )

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

    decision = await validator.validate(
        _analysis_request("Assess demand"), [task], {"market": result}
    )

    assert decision.action == "replan"
    assert decision.gaps == ["market: evidence rows empty"]
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_validator_rejects_need_input_without_missing_fields() -> None:
    validator = ResultValidator(CountingValidationLLM())
    task = AgentTask(
        task_id="pricing-1",
        agent=AgentName.PRICING,
        objective="Calculate profit",
    )
    result = AgentResult(
        agent=AgentName.PRICING,
        status="need_input",
        summary="More information is required",
        missing_fields=[],
    )

    decision = await validator.validate(
        _analysis_request("Calculate profit"), [task], {"pricing": result}
    )

    assert decision.action == "fail"
    assert decision.gaps == ["pricing: need_input missing field list"]


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


class CapturingWorkflowCompletions:
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


def _workflow_llm(contents: list[str]) -> tuple[LLMClient, CapturingWorkflowCompletions]:
    completions = CapturingWorkflowCompletions(contents)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return LLMClient(model="fake-model", client=client, max_parse_retries=0), completions


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


@pytest.mark.asyncio
async def test_validator_transport_receives_original_question_tasks_results_and_schema() -> None:
    llm, completions = _workflow_llm(
        ['{"action": "replan", "gaps": ["pricing dimension omitted"]}']
    )
    validator = ResultValidator(llm)
    task = _task(AgentName.MARKET)
    result = _success(AgentName.MARKET)
    request = _analysis_request("Assess demand and calculate profit")

    decision = await validator.validate(request, [task], {"market": result})

    assert decision.action == "replan"
    prompt = "\n".join(
        message["content"] for message in completions.requests[0]["messages"]
    )
    assert request.question in prompt
    assert "market-1" in prompt
    assert "market completed" in prompt
    assert '"action"' in prompt
    assert '"required"' in prompt


@pytest.mark.asyncio
async def test_validator_semantic_review_can_replan_conflicting_success_results() -> None:
    llm, _ = _workflow_llm(
        ['{"action": "replan", "gaps": ["market and pricing conflict"]}']
    )
    validator = ResultValidator(llm)
    tasks = [_task(AgentName.MARKET), _task(AgentName.PRICING)]
    results = {
        "market": _success(AgentName.MARKET),
        "pricing": _success(AgentName.PRICING),
    }

    decision = await validator.validate(
        _analysis_request("Reconcile market and pricing"), tasks, results
    )

    assert decision.action == "replan"
    assert decision.gaps == ["market and pricing conflict"]


@pytest.mark.asyncio
async def test_strategy_transport_receives_recommendation_json_schema() -> None:
    llm, completions = _workflow_llm(
        [
            '{"decision":"enter","reasons":["supported"],'
            '"risks":[],"next_actions":[]}'
        ]
    )
    strategy = StrategyAgent(llm)

    await strategy.run("Should we enter?", {"market": _success(AgentName.MARKET)})

    prompt = "\n".join(
        message["content"] for message in completions.requests[0]["messages"]
    )
    assert '"decision"' in prompt
    assert '"do_not_enter"' in prompt
    assert '"required"' in prompt


class StaticEvidenceQuery:
    def __init__(self, evidence: EvidenceRecord) -> None:
        self.evidence = evidence

    async def query(
        self, question: str, entity_hint: str | None, purpose: str
    ) -> EvidenceRecord:
        del question, entity_hint, purpose
        return self.evidence


class UnusedFeeRepository:
    async def get_rule(self, category: str, weight_kg: Decimal) -> Any:
        raise AssertionError(f"unexpected fee rule query: {category} {weight_kg}")


class StaticPricingCalculator:
    def __call__(self, product: ProductInput, fee_rule: Any) -> PricingResult:
        del product, fee_rule
        return PricingResult(
            purchase_cost_usd="10.00",
            fixed_cost="16.00",
            variable_rate="0.3000",
            profit="1.50",
            margin="0.0600",
            break_even_price="22.86",
            target_price="32.00",
            assumptions={"fx_source": "user_input"},
        )


def _complete_product(**overrides: Any) -> ProductInput:
    payload: dict[str, Any] = {
        "name": "Magnetic charger",
        "category": "consumer_electronics",
        "purchase_cost_cny": "70",
        "selling_price_usd": "25",
        "fx_cny_per_usd": "7",
        "inbound_shipping_usd": "2",
        "fba_fee_usd": "4",
        "commission_rate": "0.15",
        "ad_rate": "0.10",
        "return_loss_rate": "0.05",
        "target_margin": "0.20",
        "weight_kg": "0.2",
    }
    payload.update(overrides)
    return ProductInput.model_validate(payload)


async def _single_real_agent_answer(
    agent_name: AgentName,
    agent: Any,
    product: ProductInput,
) -> str:
    graph = build_workflow(
        WorkflowDependencies(
            supervisor=StaticSupervisor([agent_name]),
            validator=ResultValidator(CountingValidationLLM()),
            strategy=RecordingStrategy(),
            agents={agent_name: agent},
        )
    )
    runtime = LangGraphWorkflowRuntime(graph)
    events = [
        event
        async for event in runtime.stream(
            AnalysisRequest(product=product, question=f"Run {agent_name.value} analysis"),
            trace_id=f"trace-real-{agent_name.value}",
            thread_id=f"thread-real-{agent_name.value}",
        )
    ]
    return next(event.message for event in events if event.event_type == "answer_chunk")


@pytest.mark.asyncio
async def test_market_answer_contains_real_demand_price_band_and_sample_fields() -> None:
    evidence = EvidenceRecord(
        source_type="neo4j",
        summary="Synthetic category aggregation",
        rows=[
            {
                "demand_level": "high",
                "sample_size": 36,
                "min_price": "19.99",
                "max_price": "39.99",
                "brand_count": 12,
                "product_count": 36,
            }
        ],
    )
    answer = await _single_real_agent_answer(
        AgentName.MARKET,
        MarketAgent(StaticEvidenceQuery(evidence)),
        _complete_product(),
    )

    assert all(value in answer for value in ["high", "19.99", "39.99", "36"])
    assert "Synthetic category aggregation" in answer


@pytest.mark.asyncio
async def test_competitor_answer_contains_real_competitor_fields() -> None:
    evidence = EvidenceRecord(
        source_type="neo4j",
        summary="Nearest graph competitor",
        rows=[
            {
                "product_id": "comp-1",
                "name": "Comp Alpha",
                "brand": "Brand A",
                "price_usd": "24.99",
                "rating": 4.6,
                "review_count": 1200,
                "bsr": 321,
                "selling_points": ["MagSafe"],
                "difference_summary": "Lower price",
                "similarity": 0.94,
            }
        ],
    )
    answer = await _single_real_agent_answer(
        AgentName.COMPETITOR,
        CompetitorAgent(StaticEvidenceQuery(evidence)),
        _complete_product(),
    )

    assert all(value in answer for value in ["Comp Alpha", "Brand A", "24.99", "Lower price"])
    assert "Nearest graph competitor" in answer


@pytest.mark.asyncio
async def test_pricing_answer_contains_real_calculator_amounts() -> None:
    answer = await _single_real_agent_answer(
        AgentName.PRICING,
        PricingAgent(UnusedFeeRepository(), StaticPricingCalculator()),
        _complete_product(),
    )

    assert all(value in answer for value in ["16.00", "1.50", "22.86", "32.00"])
    assert "Decimal pricing calculation" in answer


@pytest.mark.asyncio
async def test_compliance_answer_contains_real_document_groups() -> None:
    evidence = EvidenceRecord(
        source_type="neo4j",
        summary="Battery documentation rule",
        rows=[{"document": "UN38.3", "applicability": "required"}],
    )
    answer = await _single_real_agent_answer(
        AgentName.COMPLIANCE,
        ComplianceAgent(StaticEvidenceQuery(evidence)),
        _complete_product(),
    )

    assert "UN38.3" in answer
    assert "missing" in answer
    assert "非法律意见" in answer
    assert "Battery documentation rule" in answer


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


class GapOnlySupervisor:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def plan(
        self, request: AnalysisRequest, *, gaps: list[str] | None = None
    ) -> SupervisorPlan:
        del request
        current_gaps = list(gaps or [])
        self.calls.append(current_gaps)
        if current_gaps:
            return SupervisorPlan(tasks=[_task(AgentName.COMPETITOR)])
        return SupervisorPlan(
            tasks=[_task(AgentName.MARKET), _task(AgentName.COMPETITOR)],
            market_entry_requested=True,
        )


class SequenceAgent:
    def __init__(self, results: list[AgentResult]) -> None:
        self.results = results
        self.calls = 0
        self.task_ids: list[str] = []

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        del product
        self.task_ids.append(task.task_id)
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


class RecordingStrategy:
    def __init__(self) -> None:
        self.calls = 0
        self.result_keys: list[set[str]] = []

    async def run(
        self, question: str, results: Mapping[str, AgentResult]
    ) -> StrategyRecommendation:
        del question
        self.calls += 1
        self.result_keys.append(set(results))
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
            validator=ResultValidator(CountingValidationLLM()),
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


@pytest.mark.asyncio
async def test_immediate_agents_emit_all_started_before_first_completed() -> None:
    selected = [AgentName.MARKET, AgentName.COMPETITOR]
    runtime, _ = _runtime(
        StaticSupervisor(selected),
        {agent: SequenceAgent([_success(agent)]) for agent in selected},
    )

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Compare market and competitors"),
            trace_id="trace-immediate",
            thread_id="thread-immediate",
        )
    ]
    event_types = [event.event_type for event in events]
    start_positions = [
        index for index, event_type in enumerate(event_types) if event_type == "agent_started"
    ]
    complete_positions = [
        index
        for index, event_type in enumerate(event_types)
        if event_type == "agent_completed"
    ]

    assert max(start_positions) < min(complete_positions)


@pytest.mark.asyncio
async def test_immediate_and_waiting_agents_both_start_before_completion() -> None:
    release = asyncio.Event()
    waiting = CoordinatedAgent(AgentName.COMPETITOR, release)
    runtime, _ = _runtime(
        StaticSupervisor([AgentName.MARKET, AgentName.COMPETITOR]),
        {
            AgentName.MARKET: SequenceAgent([_success(AgentName.MARKET)]),
            AgentName.COMPETITOR: waiting,
        },
    )
    events: list[Any] = []

    async def consume() -> None:
        async for event in runtime.stream(
            _analysis_request("Compare market and competitors"),
            trace_id="trace-mixed",
            thread_id="thread-mixed",
        ):
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(waiting.started.wait(), timeout=1)
    release.set()
    await asyncio.wait_for(consumer, timeout=1)
    event_types = [event.event_type for event in events]
    starts = [index for index, value in enumerate(event_types) if value == "agent_started"]
    completions = [
        index for index, value in enumerate(event_types) if value == "agent_completed"
    ]
    assert max(starts) < min(completions)


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
async def test_gap_only_replan_preserves_full_plan_and_strategy_inputs() -> None:
    supervisor = GapOnlySupervisor()
    strategy = RecordingStrategy()
    market = SequenceAgent([_success(AgentName.MARKET)])
    competitor = SequenceAgent(
        [
            AgentResult(
                agent=AgentName.COMPETITOR,
                status="failed",
                summary="No competitor rows",
                errors=["no_rows"],
            ),
            _success(AgentName.COMPETITOR),
        ]
    )
    runtime, _ = _runtime(
        supervisor,
        {AgentName.MARKET: market, AgentName.COMPETITOR: competitor},
        strategy=strategy,
    )

    events = [
        event
        async for event in runtime.stream(
            _analysis_request("Provide the launch decision from market and competitor analysis"),
            trace_id="trace-gap-plan",
            thread_id="thread-gap-plan",
        )
    ]

    assert events[-1].event_type == "workflow_completed"
    assert market.calls == 1
    assert competitor.calls == 2
    assert competitor.task_ids == ["round-0-competitor-1", "round-1-competitor-1"]
    assert strategy.calls == 1
    assert strategy.result_keys == [{"market", "competitor"}]


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
    assert len(answers) == 1
    assert "market completed" in answers[0]
    assert "market evidence" in answers[0]


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
            validator=ResultValidator(CountingValidationLLM()),
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


@pytest.mark.asyncio
async def test_duplicate_stream_for_same_thread_is_rejected_without_second_execution() -> None:
    agent = SequenceAgent([_success(AgentName.MARKET)])
    runtime, _ = _runtime(
        StaticSupervisor([AgentName.MARKET]),
        {AgentName.MARKET: agent},
    )
    first_events = [
        event
        async for event in runtime.stream(
            _analysis_request("Assess first product"),
            trace_id="trace-first",
            thread_id="thread-duplicate",
        )
    ]
    second_events = [
        event
        async for event in runtime.stream(
            AnalysisRequest(
                product=_analysis_request("xx").product.model_copy(
                    update={"name": "Different product"}
                ),
                question="Assess a different product",
            ),
            trace_id="trace-second",
            thread_id="thread-duplicate",
        )
    ]

    assert first_events[-1].event_type == "workflow_completed"
    assert [event.event_type for event in second_events] == ["workflow_failed"]
    assert "resume" in second_events[0].message.casefold()
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_resume_after_completed_thread_returns_explicit_failure() -> None:
    runtime, _ = _runtime(
        StaticSupervisor([AgentName.MARKET]),
        {AgentName.MARKET: SequenceAgent([_success(AgentName.MARKET)])},
    )
    completed = [
        event
        async for event in runtime.stream(
            _analysis_request("Assess market"),
            trace_id="trace-completed",
            thread_id="thread-completed",
        )
    ]
    resumed = [
        event
        async for event in runtime.resume(
            thread_id="thread-completed",
            fields={"selling_price_usd": "30"},
            trace_id="trace-after-complete",
        )
    ]

    assert completed[-1].event_type == "workflow_completed"
    assert [event.event_type for event in resumed] == ["workflow_failed"]
