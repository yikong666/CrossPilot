from __future__ import annotations

import math
from typing import Any

import pytest

from app.contracts.agents import AgentName, AgentTask
from app.contracts.api import ProductInput
from app.contracts.graph import EvidenceRecord


class FakeQueryService:
    def __init__(self, evidence: EvidenceRecord | Exception) -> None:
        self.evidence = evidence

    async def query(
        self, question: str, entity_hint: str | None, purpose: str
    ) -> EvidenceRecord:
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence


def market_task() -> AgentTask:
    return AgentTask(
        task_id="market-1",
        agent=AgentName.MARKET,
        objective="Assess the Amazon US market opportunity.",
    )


def product() -> ProductInput:
    return ProductInput(
        name="USB-C Cable",
        category="consumer_electronics",
        purchase_cost_cny="10",
    )


def market_evidence(rows: list[dict[str, Any]]) -> EvidenceRecord:
    return EvidenceRecord(
        source_type="neo4j",
        summary="market aggregation",
        cypher="MATCH (p:Product) RETURN p",
        params={"category": "consumer_electronics", "marketplace": "amazon_us"},
        rows=rows,
    )


def valid_market_aggregate(**overrides: Any) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "demand_level": "high",
        "sample_size": 1200,
        "min_price": 19.99,
        "max_price": 39.99,
        "brand_count": 6,
        "product_count": 12,
    }
    aggregate.update(overrides)
    return aggregate


@pytest.mark.asyncio
async def test_market_agent_returns_offline_proxy_assessment_with_evidence() -> None:
    """Dropping aggregation-to-result mapping would lose the evidence-backed assessment."""
    from app.agents.market import MarketAgent

    evidence = market_evidence(
        [
            {
                "demand_level": "high",
                "sample_size": 1200,
                "min_price": 19.99,
                "max_price": 39.99,
                "brand_count": 6,
                "product_count": 12,
            }
        ]
    )

    result = await MarketAgent(FakeQueryService(evidence)).run(market_task(), product())

    assert result.agent is AgentName.MARKET
    assert result.status == "success"
    assert result.data == {
        "demand_level": "high",
        "price_band": {"min_usd": 19.99, "max_usd": 39.99},
        "competition_level": "fragmented",
        "sample_size": 1200,
        "assessment_basis": "offline synthetic Amazon US proxy data",
    }
    assert result.evidence == [evidence]
    assert "proxy" in result.summary.lower()


@pytest.mark.asyncio
async def test_market_agent_fails_when_graph_returns_no_market_rows() -> None:
    """Replacing an empty-result failure with fabricated metrics would break this guard."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(FakeQueryService(market_evidence([]))).run(market_task(), product())

    assert result.status == "failed"
    assert result.data == {}
    assert result.evidence == []
    assert result.errors == ["market_data_unavailable"]


@pytest.mark.asyncio
async def test_market_agent_fails_when_aggregation_fields_are_missing() -> None:
    """Accepting partial aggregations would let the agent invent missing market values."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(
        FakeQueryService(market_evidence([{"demand_level": "high", "sample_size": 1200}]))
    ).run(market_task(), product())

    assert result.status == "failed"
    assert result.data == {}
    assert result.evidence == []
    assert result.errors == ["market_data_incomplete"]


@pytest.mark.asyncio
async def test_market_agent_fails_when_product_count_cannot_support_competition_proxy() -> None:
    """Dividing by an invalid product count must not leak an aggregation error."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(
        FakeQueryService(
            market_evidence(
                [
                    {
                        "demand_level": "high",
                        "sample_size": 1200,
                        "min_price": 19.99,
                        "max_price": 39.99,
                        "brand_count": 6,
                        "product_count": 0,
                    }
                ]
            )
        )
    ).run(market_task(), product())

    assert result.status == "failed"
    assert result.data == {}
    assert result.evidence == []
    assert result.errors == ["market_data_incomplete"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "aggregate",
    [
        valid_market_aggregate(sample_size=0),
        valid_market_aggregate(sample_size=-1),
        valid_market_aggregate(sample_size=math.nan),
        valid_market_aggregate(sample_size=math.inf),
        valid_market_aggregate(product_count=-1),
        valid_market_aggregate(product_count=math.nan),
        valid_market_aggregate(product_count=math.inf),
        valid_market_aggregate(brand_count=-1),
        valid_market_aggregate(brand_count=13),
        valid_market_aggregate(brand_count=math.nan),
        valid_market_aggregate(brand_count=math.inf),
        valid_market_aggregate(min_price=-0.01),
        valid_market_aggregate(max_price=-0.01),
        valid_market_aggregate(min_price=40, max_price=39),
        valid_market_aggregate(min_price=math.nan),
        valid_market_aggregate(max_price=math.inf),
    ],
)
async def test_market_agent_rejects_semantically_invalid_aggregates(
    aggregate: dict[str, Any],
) -> None:
    """Accepting impossible or non-finite aggregates would produce a false market assessment."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(FakeQueryService(market_evidence([aggregate]))).run(
        market_task(), product()
    )

    assert result.status == "failed"
    assert result.data == {}
    assert result.evidence == []
    assert result.errors == ["market_data_incomplete"]


@pytest.mark.asyncio
async def test_market_agent_returns_stable_error_when_query_service_fails() -> None:
    """Leaking a query exception instead of a recoverable result would break orchestration."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(FakeQueryService(RuntimeError("driver password=secret"))).run(
        market_task(), product()
    )

    assert result.status == "failed"
    assert result.data == {}
    assert result.evidence == []
    assert result.errors == ["market_query_failed"]
