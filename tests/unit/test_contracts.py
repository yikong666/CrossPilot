from __future__ import annotations

from decimal import Decimal
from typing import Annotated, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from app.contracts.agents import (
    AgentName,
    AgentResult,
    AgentTask,
    ValidationDecision,
)
from app.contracts.api import AnalysisRequest, ProductInput
from app.contracts.events import SSEEvent
from app.contracts.graph import EvidenceRecord
from app.workflow.state import WorkflowState, merge_agent_results


def valid_product_data() -> dict[str, object]:
    return {
        "name": "Magnetic Wireless Charger",
        "category": "consumer_electronics",
        "purchase_cost_cny": "70.00",
    }


def successful_result(agent: AgentName, summary: str) -> AgentResult:
    return AgentResult(agent=agent, status="success", summary=summary)


def test_product_input_accepts_valid_boundary_values() -> None:
    product = ProductInput(
        **valid_product_data(),
        commission_rate="0",
        ad_rate="0.9999",
        return_loss_rate="0",
        target_margin="0.9999",
    )

    assert product.purchase_cost_cny == Decimal("70.00")
    assert product.commission_rate == Decimal("0")
    assert product.ad_rate == Decimal("0.9999")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("purchase_cost_cny", "-0.01"),
        ("category", "books"),
        ("commission_rate", "1"),
        ("ad_rate", "1.01"),
        ("return_loss_rate", "1"),
        ("target_margin", "1"),
    ],
)
def test_product_input_rejects_invalid_boundaries(field: str, value: object) -> None:
    data = valid_product_data()
    data[field] = value

    with pytest.raises(ValidationError):
        ProductInput(**data)


def test_analysis_request_rejects_question_shorter_than_two_characters() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(product=ProductInput(**valid_product_data()), question="?")


def test_mutable_defaults_are_isolated_between_instances() -> None:
    first_product = ProductInput(**valid_product_data())
    second_product = ProductInput(**valid_product_data())
    first_product.provided_documents.append("FCC report")

    first_evidence = EvidenceRecord(source_type="neo4j", summary="first")
    second_evidence = EvidenceRecord(source_type="neo4j", summary="second")
    first_evidence.params["category"] = "charger"
    first_evidence.rows.append({"sample_size": 35})

    assert second_product.provided_documents == []
    assert second_evidence.params == {}
    assert second_evidence.rows == []


def test_agent_contracts_keep_structured_results() -> None:
    task = AgentTask(
        task_id="market-1",
        agent=AgentName.MARKET,
        objective="Analyze the market",
    )
    result = successful_result(AgentName.MARKET, "Demand is medium")
    decision = ValidationDecision(action="pass")

    assert task.agent is AgentName.MARKET
    assert result.status == "success"
    assert result.evidence == []
    assert decision.action == "pass"


def test_sse_event_rejects_unknown_event_type_and_isolates_payload() -> None:
    first = SSEEvent(
        trace_id="trace-1",
        thread_id="thread-1",
        event_type="workflow_started",
        message="Started",
        timestamp="2026-09-04T16:00:00+08:00",
    )
    second = SSEEvent(
        trace_id="trace-2",
        thread_id="thread-2",
        event_type="workflow_completed",
        message="Completed",
        timestamp="2026-09-04T16:01:00+08:00",
    )
    first.payload["agent"] = "market"

    assert second.payload == {}
    with pytest.raises(ValidationError):
        SSEEvent(
            trace_id="trace-3",
            thread_id="thread-3",
            event_type="unknown_event",
            message="Unknown",
            timestamp="2026-09-04T16:02:00+08:00",
        )


def test_merge_agent_results_preserves_parallel_agent_outputs() -> None:
    market = successful_result(AgentName.MARKET, "market result")
    competitor = successful_result(AgentName.COMPETITOR, "competitor result")

    merged = merge_agent_results(
        {AgentName.MARKET.value: market},
        {AgentName.COMPETITOR.value: competitor},
    )

    assert merged == {
        "market": market,
        "competitor": competitor,
    }


def test_merge_agent_results_overwrites_only_the_same_agent() -> None:
    original_market = successful_result(AgentName.MARKET, "old market")
    updated_market = successful_result(AgentName.MARKET, "new market")
    competitor = successful_result(AgentName.COMPETITOR, "competitor result")

    merged = merge_agent_results(
        {"market": original_market, "competitor": competitor},
        {AgentName.MARKET: updated_market},
    )

    assert merged["market"] == updated_market
    assert merged["competitor"] == competitor


def test_workflow_state_declares_all_required_fields_and_reducer() -> None:
    hints = get_type_hints(WorkflowState, include_extras=True)

    assert set(hints) == {
        "trace_id",
        "thread_id",
        "request",
        "tasks",
        "agent_results",
        "validation",
        "replan_count",
        "final_answer",
        "graph_nodes",
        "graph_edges",
        "errors",
    }
    assert get_origin(hints["agent_results"]) is Annotated
    assert merge_agent_results in get_args(hints["agent_results"])[1:]
