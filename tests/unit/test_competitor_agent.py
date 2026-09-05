from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.competitor import CompetitorAgent
from app.contracts.agents import AgentName, AgentTask
from app.contracts.api import ProductInput
from app.contracts.graph import EvidenceRecord


class FakeQueryService:
    def __init__(self, evidence: EvidenceRecord | Exception) -> None:
        self._evidence = evidence

    async def query(
        self, question: str, entity_hint: str | None, purpose: str
    ) -> EvidenceRecord:
        if isinstance(self._evidence, Exception):
            raise self._evidence
        return self._evidence


def _product() -> ProductInput:
    return ProductInput(
        name="Target USB-C Hub",
        category="consumer_electronics",
        purchase_cost_cny=Decimal("20"),
    )


def _task() -> AgentTask:
    return AgentTask(
        task_id="competitor-1",
        agent=AgentName.COMPETITOR,
        objective="Compare similar USB-C hubs",
    )


def _row(
    product_id: str,
    *,
    similarity: float,
    name: str | None = None,
    brand: str = "Brand",
    price_usd: float = 19.99,
    rating: float = 4.5,
    review_count: int = 100,
    bsr: int = 200,
    selling_points: list[str] | None = None,
    difference_summary: str = "More ports",
) -> dict[str, object]:
    return {
        "product_id": product_id,
        "name": name or product_id,
        "brand": brand,
        "price_usd": price_usd,
        "rating": rating,
        "review_count": review_count,
        "bsr": bsr,
        "selling_points": selling_points or ["USB-C"],
        "difference_summary": difference_summary,
        "similarity": similarity,
    }


def _evidence(rows: list[dict[str, object]]) -> EvidenceRecord:
    return EvidenceRecord(
        source_type="neo4j",
        summary="competitor graph query returned rows",
        rows=rows,
    )


@pytest.mark.asyncio
async def test_returns_top_five_sorted_competitors_and_excludes_target() -> None:
    evidence = _evidence(
        [
            _row("Target USB-C Hub", similarity=1.0),
            _row("p-low", similarity=0.60),
            _row("p-equal-b", similarity=0.80, name="Bravo"),
            _row("p-top", similarity=0.95),
            _row("p-equal-a", similarity=0.80, name="Alpha"),
            _row("p-mid", similarity=0.75),
            _row("p-sixth", similarity=0.50),
        ]
    )

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.agent is AgentName.COMPETITOR
    assert result.status == "success"
    assert [item["product_id"] for item in result.data["competitors"]] == [
        "p-top",
        "p-equal-a",
        "p-equal-b",
        "p-mid",
        "p-low",
    ]
    assert result.data["competitors"][0] == {
        "product_id": "p-top",
        "name": "p-top",
        "brand": "Brand",
        "price_usd": 19.99,
        "rating": 4.5,
        "review_count": 100,
        "bsr": 200,
        "selling_points": ["USB-C"],
        "difference_summary": "More ports",
    }
    assert result.evidence == [evidence]


@pytest.mark.asyncio
async def test_returns_failed_when_query_has_no_competitors() -> None:
    evidence = _evidence([])

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["competitor_not_found"]
    assert result.evidence == [evidence]


@pytest.mark.asyncio
async def test_returns_failed_when_competitor_row_is_missing_required_field() -> None:
    row = _row("p-incomplete", similarity=0.9)
    del row["bsr"]
    evidence = _evidence([row])

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["competitor_data_incomplete"]
    assert result.evidence == [evidence]


@pytest.mark.asyncio
async def test_returns_failed_when_similarity_is_not_numeric() -> None:
    row = _row("p-invalid-similarity", similarity=0.9)
    row["similarity"] = "high"
    evidence = _evidence([row])

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["competitor_data_incomplete"]
    assert result.evidence == [evidence]


@pytest.mark.asyncio
@pytest.mark.parametrize("similarity", [float("nan"), float("inf"), float("-inf")])
async def test_returns_failed_when_similarity_is_not_finite(similarity: float) -> None:
    evidence = _evidence([_row("p-invalid-similarity", similarity=similarity)])

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["competitor_data_incomplete"]
    assert result.evidence == [evidence]


@pytest.mark.asyncio
async def test_keeps_complete_competitors_when_one_candidate_is_incomplete() -> None:
    incomplete = _row("p-incomplete", similarity=0.99)
    del incomplete["bsr"]
    evidence = _evidence(
        [
            _row("p-1", similarity=0.95),
            _row("p-2", similarity=0.90),
            _row("p-3", similarity=0.85),
            _row("p-4", similarity=0.80),
            _row("p-5", similarity=0.75),
            incomplete,
        ]
    )

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert [item["product_id"] for item in result.data["competitors"]] == [
        "p-1",
        "p-2",
        "p-3",
        "p-4",
        "p-5",
    ]
    assert result.evidence == [evidence]


@pytest.mark.asyncio
async def test_uses_completeness_then_product_id_when_similarity_ties() -> None:
    evidence = _evidence(
        [
            _row("p-low-completeness", similarity=0.8, selling_points=[], difference_summary=""),
            _row("p-z", similarity=0.8),
            _row("p-a", similarity=0.8),
        ]
    )

    result = await CompetitorAgent(FakeQueryService(evidence)).run(_task(), _product())

    assert result.status == "success"
    assert [item["product_id"] for item in result.data["competitors"]] == [
        "p-a",
        "p-z",
        "p-low-completeness",
    ]


@pytest.mark.asyncio
async def test_returns_failed_when_query_service_raises() -> None:
    result = await CompetitorAgent(FakeQueryService(RuntimeError("network offline"))).run(
        _task(), _product()
    )

    assert result.status == "failed"
    assert result.errors == ["competitor_query_failed"]
    assert result.evidence == []
