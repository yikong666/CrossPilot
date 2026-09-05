from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol

import pytest

import app.agents as agent_package
from app.agents.competitor import CompetitorAgent
from app.agents.compliance import ComplianceAgent
from app.agents.market import MarketAgent
from app.agents.pricing import PricingAgent
from app.contracts import AgentName, AgentResult, AgentTask, EvidenceRecord, ProductInput
from app.graph.cypher_validator import CypherValidator
from app.graph.schema_registry import SchemaRegistry
from app.services.fee_rule_repository import FeeRule
from app.services.pricing_calculator import PricingResult
from app.workflow.state import merge_agent_results


def test_agent_package_exports_all_specialists() -> None:
    """Omitting a specialist from the package API would break workflow construction."""
    assert agent_package.MarketAgent is MarketAgent
    assert agent_package.CompetitorAgent is CompetitorAgent
    assert agent_package.PricingAgent is PricingAgent
    assert agent_package.ComplianceAgent is ComplianceAgent


class QueryService:
    def __init__(self, responses: dict[str, EvidenceRecord] | None = None) -> None:
        self._responses = responses or {}

    async def query(
        self, question: str, entity_hint: str | None, purpose: str
    ) -> EvidenceRecord:
        del question, entity_hint
        return self._responses[purpose]


class FailingQueryService:
    async def query(
        self, question: str, entity_hint: str | None, purpose: str
    ) -> EvidenceRecord:
        del question, entity_hint, purpose
        raise RuntimeError("external dependency detail")


class UnusedRuleRepository:
    async def get_rule(self, category: str, weight_kg: Decimal) -> FeeRule:
        del category, weight_kg
        raise AssertionError("a complete user input must not query fee rules")


class Calculator:
    def __call__(self, product: ProductInput, fee_rule: FeeRule | None) -> PricingResult:
        del product
        assert fee_rule is None
        return PricingResult(
            purchase_cost_usd=Decimal("2.00"),
            fixed_cost=Decimal("5.30"),
            variable_rate=Decimal("0.2700"),
            profit=Decimal("4.18"),
            margin=Decimal("0.3218"),
            break_even_price=Decimal("7.26"),
            target_price=Decimal("10.15"),
            assumptions={
                "purchase_cost_cny": Decimal("14.00"),
                "fx_cny_per_usd": Decimal("7.00"),
                "fx_source": "user_input",
                "inbound_shipping_usd": Decimal("0.80"),
                "fba_fee_usd": Decimal("2.50"),
                "fba_fee_source": "user_input",
                "commission_rate": Decimal("0.1500"),
                "commission_source": "user_input",
                "ad_rate": Decimal("0.1000"),
                "return_loss_rate": Decimal("0.0200"),
                "target_margin": Decimal("0.2000"),
            },
        )


class Specialist(Protocol):
    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult: ...


def _task(agent: AgentName) -> AgentTask:
    return AgentTask(
        task_id=f"{agent.value}-integration",
        agent=agent,
        objective=f"Run the {agent.value} assessment.",
    )


def _product(**overrides: Any) -> ProductInput:
    values: dict[str, Any] = {
        "name": "USB-C Hub",
        "category": "consumer_electronics",
        "purchase_cost_cny": "14.00",
        "selling_price_usd": "12.99",
        "fx_cny_per_usd": "7.00",
        "inbound_shipping_usd": "0.80",
        "fba_fee_usd": "2.50",
        "commission_rate": "0.15",
        "ad_rate": "0.10",
        "return_loss_rate": "0.02",
        "target_margin": "0.20",
        "provided_documents": ["FCC Supplier Declaration"],
    }
    values.update(overrides)
    return ProductInput.model_validate(values)


def _neo4j_evidence(summary: str, rows: list[dict[str, Any]]) -> EvidenceRecord:
    return EvidenceRecord(source_type="neo4j", summary=summary, rows=rows)


@pytest.mark.asyncio
async def test_all_specialists_share_the_run_contract_and_emit_evidence() -> None:
    """Changing one specialist's call or result contract must break workflow integration."""
    market_evidence = _neo4j_evidence(
        "market aggregates",
        [
            {
                "demand_level": "high",
                "sample_size": 100,
                "prices": ["9.99", "14.99", "19.99", "29.99"],
                "brands": ["Acme", "Acme", "Nova", "Orbit"],
            }
        ],
    )
    competitor_evidence = _neo4j_evidence(
        "comparable products",
        [
            {
                "product_id": "competitor-1",
                "name": "Comparable Hub",
                "brand": "Example Brand",
                "price_usd": 19.99,
                "rating": 4.5,
                "review_count": 100,
                "bsr": 200,
                "selling_points": ["seven ports"],
                "difference_summary": "Adds an HDMI port.",
                "similarity": 0.9,
            }
        ],
    )
    compliance_evidence = _neo4j_evidence(
        "applicable requirements",
        [
            {
                "document": "FCC Supplier Declaration",
                "applicability": "required",
            }
        ],
    )
    query_service = QueryService(
        {
            "market": market_evidence,
            "competitor": competitor_evidence,
            "compliance": compliance_evidence,
        }
    )
    specialists: dict[AgentName, Specialist] = {
        AgentName.MARKET: MarketAgent(query_service),
        AgentName.COMPETITOR: CompetitorAgent(query_service),
        AgentName.PRICING: PricingAgent(UnusedRuleRepository(), Calculator()),
        AgentName.COMPLIANCE: ComplianceAgent(query_service),
    }

    results = {
        name: await specialist.run(_task(name), _product())
        for name, specialist in specialists.items()
    }

    assert set(results) == set(AgentName)
    for name, result in results.items():
        assert isinstance(result, AgentResult)
        assert result.agent is name
        assert result.status == "success"
        assert result.evidence
    assert results[AgentName.MARKET].data["sample_size"] == 100
    market_data = results[AgentName.MARKET].data
    assert market_data["demand_level"] == "high"
    assert market_data["price_statistics"] == {
        "min_usd": "9.99",
        "max_usd": "29.99",
        "average_usd": "18.74",
        "median_usd": "17.49",
        "mainstream_min_usd": "13.74",
        "mainstream_max_usd": "22.49",
    }
    assert market_data["price_band"] == {
        "min_usd": "13.74",
        "max_usd": "22.49",
    }
    assert market_data["top_brand_share"] == "0.5000"
    assert market_data["competition_level"] == "concentrated"
    assert market_data["sample_size"] == 100
    assert results[AgentName.COMPETITOR].data["competitors"][0]["product_id"] == "competitor-1"
    assert results[AgentName.PRICING].evidence[0].source_type == "calculator"
    assert results[AgentName.COMPLIANCE].data["available"] == [
        "FCC Supplier Declaration"
    ]


@pytest.mark.asyncio
async def test_specialist_failures_and_missing_inputs_are_results_not_exceptions() -> None:
    """Letting a dependency failure or missing price input escape would break orchestration."""
    failing_query = FailingQueryService()
    query_results = [
        await MarketAgent(failing_query).run(_task(AgentName.MARKET), _product()),
        await CompetitorAgent(failing_query).run(_task(AgentName.COMPETITOR), _product()),
        await ComplianceAgent(failing_query).run(_task(AgentName.COMPLIANCE), _product()),
    ]
    pricing_result = await PricingAgent(UnusedRuleRepository(), Calculator()).run(
        _task(AgentName.PRICING),
        _product(
            fx_cny_per_usd=None,
            fba_fee_usd=None,
            commission_rate=None,
            weight_kg=None,
        ),
    )

    assert [(result.agent, result.status) for result in query_results] == [
        (AgentName.MARKET, "failed"),
        (AgentName.COMPETITOR, "failed"),
        (AgentName.COMPLIANCE, "failed"),
    ]
    assert pricing_result.agent is AgentName.PRICING
    assert pricing_result.status == "need_input"
    assert pricing_result.missing_fields == ["weight_kg"]


@pytest.mark.asyncio
async def test_parallel_specialist_results_merge_without_overwriting_other_agents() -> None:
    """Using a replacement reducer would discard a successful parallel agent result."""
    market_result = await MarketAgent(
        QueryService(
            {
                "market": _neo4j_evidence(
                    "market aggregates",
                    [
                        {
                            "demand_level": "medium",
                            "sample_size": 50,
                            "prices": ["8.00", "12.00", "20.00"],
                            "brands": ["Acme", "Acme", "Nova"],
                        }
                    ],
                )
            }
        )
    ).run(_task(AgentName.MARKET), _product())
    pricing_result = await PricingAgent(UnusedRuleRepository(), Calculator()).run(
        _task(AgentName.PRICING), _product()
    )

    assert market_result.status == "success"
    assert market_result.data["sample_size"] == 50
    assert market_result.data["price_statistics"] == {
        "min_usd": "8.00",
        "max_usd": "20.00",
        "average_usd": "13.33",
        "median_usd": "12.00",
        "mainstream_min_usd": "10.00",
        "mainstream_max_usd": "16.00",
    }
    assert market_result.data["top_brand_share"] == "0.6667"
    merged = merge_agent_results(
        {AgentName.MARKET: market_result},
        {AgentName.PRICING: pricing_result},
    )

    assert merged == {"market": market_result, "pricing": pricing_result}


def test_market_brand_count_query_is_accepted_by_the_schema_validator() -> None:
    """Dropping COUNT(DISTINCT graph-variable) support would block market analysis."""
    validated = CypherValidator(SchemaRegistry()).validate(
        "MATCH (p:Product)-[:MADE_BY]->(b:Brand) "
        "RETURN COUNT(DISTINCT b) AS brand_count LIMIT 1",
        {},
    )

    assert validated.return_fields == frozenset({"brand_count"})
