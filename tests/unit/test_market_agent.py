from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from app.contracts.agents import AgentName, AgentTask
from app.contracts.api import ProductInput
from app.contracts.graph import EvidenceRecord


class FakeQueryService:
    def __init__(self, evidence: EvidenceRecord | Exception) -> None:
        self.evidence = evidence
        self.questions: list[str] = []

    async def query(self, question: str, entity_hint: str | None, purpose: str) -> EvidenceRecord:
        self.questions.append(question)
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
        purchase_cost_cny=Decimal("10"),
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
        "prices": [10, 20, 30, 40],
        "brands": ["Alpha", "Alpha", "Beta", "Gamma"],
    }
    aggregate.update(overrides)
    return aggregate


@pytest.mark.asyncio
async def test_market_agent_returns_offline_proxy_assessment_with_evidence() -> None:
    """Dropping aggregation-to-result mapping would lose the evidence-backed assessment."""
    from app.agents.market import MarketAgent

    evidence = market_evidence([valid_market_aggregate()])
    query_service = FakeQueryService(evidence)

    result = await MarketAgent(query_service).run(market_task(), product())

    assert result.agent is AgentName.MARKET
    assert result.status == "success"
    assert result.data == {
        "demand_level": "high",
        "price_band": {"min_usd": "17.50", "max_usd": "32.50"},
        "price_statistics": {
            "min_usd": "10.00",
            "max_usd": "40.00",
            "average_usd": "25.00",
            "median_usd": "25.00",
            "mainstream_min_usd": "17.50",
            "mainstream_max_usd": "32.50",
        },
        "top_brand_share": "0.5000",
        "competition_level": "concentrated",
        "competition_basis": "top brand share among 4 aligned product samples",
        "sample_size": 1200,
        "assessment_basis": "offline synthetic Amazon US proxy data",
    }
    assert result.evidence == [evidence]
    assert "proxy" in result.summary.lower()
    assert "COLLECT" in query_service.questions[0]
    assert "prices" in query_service.questions[0]
    assert "brands" in query_service.questions[0]


@pytest.mark.asyncio
async def test_market_agent_computes_decimal_price_statistics_and_inclusive_quartiles() -> None:
    """Replacing deterministic Decimal statistics with graph-supplied values breaks this result."""
    from app.agents.market import MarketAgent

    aggregate = valid_market_aggregate(
        prices=[Decimal("12.00"), "14.00", 22, 40],
        brands=["Alpha", "Beta", "Beta", "Gamma"],
    )

    result = await MarketAgent(FakeQueryService(market_evidence([aggregate]))).run(
        market_task(), product()
    )

    assert result.status == "success"
    assert result.data["price_statistics"] == {
        "min_usd": "12.00",
        "max_usd": "40.00",
        "average_usd": "22.00",
        "median_usd": "18.00",
        "mainstream_min_usd": "13.50",
        "mainstream_max_usd": "26.50",
    }
    assert result.data["price_band"] == {
        "min_usd": "13.50",
        "max_usd": "26.50",
    }


@pytest.mark.asyncio
async def test_market_agent_uses_top_brand_distribution_for_competition_level() -> None:
    """Using only brand and product counts would classify these equal-count samples identically."""
    from app.agents.market import MarketAgent

    prices = list(range(10, 20))
    concentrated = valid_market_aggregate(
        prices=prices,
        brands=["A"] * 6 + ["B", "C", "D", "E"],
    )
    fragmented = valid_market_aggregate(
        prices=prices,
        brands=["A", "A", "B", "B", "C", "C", "D", "D", "E", "E"],
    )
    balanced = valid_market_aggregate(
        prices=list(range(10, 18)),
        brands=["A", "A", "B", "B", "C", "C", "D", "D"],
    )

    concentrated_result = await MarketAgent(FakeQueryService(market_evidence([concentrated]))).run(
        market_task(), product()
    )
    fragmented_result = await MarketAgent(FakeQueryService(market_evidence([fragmented]))).run(
        market_task(), product()
    )
    balanced_result = await MarketAgent(FakeQueryService(market_evidence([balanced]))).run(
        market_task(), product()
    )

    assert concentrated_result.data["top_brand_share"] == "0.6000"
    assert concentrated_result.data["competition_level"] == "concentrated"
    assert fragmented_result.data["top_brand_share"] == "0.2000"
    assert fragmented_result.data["competition_level"] == "fragmented"
    assert balanced_result.data["top_brand_share"] == "0.2500"
    assert balanced_result.data["competition_level"] == "balanced"


@pytest.mark.asyncio
async def test_market_agent_uses_single_price_as_degenerate_mainstream_band() -> None:
    """Calling quartile calculation on a single sample would fail instead of returning its price."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(
        FakeQueryService(
            market_evidence([valid_market_aggregate(prices=["19.99"], brands=["Solo"])])
        )
    ).run(market_task(), product())

    assert result.status == "success"
    assert result.data["price_band"] == {
        "min_usd": "19.99",
        "max_usd": "19.99",
    }
    assert result.data["price_statistics"]["average_usd"] == "19.99"
    assert result.data["price_statistics"]["median_usd"] == "19.99"


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
async def test_market_agent_fails_when_price_and_brand_samples_are_not_aligned() -> None:
    """Computing brand share from a differently sized brand list would corrupt the denominator."""
    from app.agents.market import MarketAgent

    result = await MarketAgent(
        FakeQueryService(
            market_evidence(
                [
                    {
                        "demand_level": "high",
                        "sample_size": 1200,
                        "prices": [19.99, 39.99],
                        "brands": ["Alpha"],
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
        valid_market_aggregate(prices=[]),
        valid_market_aggregate(brands=[]),
        valid_market_aggregate(prices=[10, -0.01]),
        valid_market_aggregate(prices=[10, math.nan]),
        valid_market_aggregate(prices=[10, math.inf]),
        valid_market_aggregate(prices="10,20"),
        valid_market_aggregate(brands="Alpha,Beta"),
        valid_market_aggregate(brands=["Alpha", ""]),
        valid_market_aggregate(brands=["Alpha", None]),
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
