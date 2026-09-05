from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.contracts.agents import AgentName, AgentResult, AgentTask
from app.contracts.api import ProductInput
from app.contracts.graph import EvidenceRecord


class CompetitorQueryService(Protocol):
    async def query(
        self,
        question: str,
        entity_hint: str | None,
        purpose: str,
    ) -> EvidenceRecord: ...


class CompetitorAgent:
    """Build a bounded, evidence-backed comparison set from graph query rows."""

    _OUTPUT_FIELDS = (
        "product_id",
        "name",
        "brand",
        "price_usd",
        "rating",
        "review_count",
        "bsr",
        "selling_points",
        "difference_summary",
    )

    def __init__(self, query_service: CompetitorQueryService) -> None:
        self._query_service = query_service

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        try:
            evidence = await self._query_service.query(
                question=(
                    f"{task.objective}. Find similar competing products for "
                    f"{product.name}."
                ),
                entity_hint=product.name,
                purpose=AgentName.COMPETITOR.value,
            )
        except Exception:
            return AgentResult(
                agent=AgentName.COMPETITOR,
                status="failed",
                summary="Competitor evidence could not be retrieved.",
                errors=["competitor_query_failed"],
            )

        candidates = [
            row
            for row in evidence.rows
            if isinstance(row, Mapping) and not self._is_target_product(row, product)
        ]
        if not candidates:
            return self._failed_not_found(evidence)

        if any(not self._is_complete(row) for row in candidates):
            return AgentResult(
                agent=AgentName.COMPETITOR,
                status="failed",
                summary="Competitor evidence is missing required comparison fields.",
                evidence=[evidence],
                errors=["competitor_data_incomplete"],
            )

        ordered = sorted(candidates, key=self._sort_key)[:5]
        competitors = [self._to_output(row) for row in ordered]
        return AgentResult(
            agent=AgentName.COMPETITOR,
            status="success",
            summary=f"Found {len(competitors)} comparable products.",
            data={"competitors": competitors},
            evidence=[evidence],
        )

    @staticmethod
    def _is_target_product(row: Mapping[str, Any], product: ProductInput) -> bool:
        product_name = product.name.casefold()
        return any(
            isinstance(value, str) and value.casefold() == product_name
            for value in (row.get("product_id"), row.get("name"))
        )

    @classmethod
    def _is_complete(cls, row: Mapping[str, Any]) -> bool:
        return (
            all(cls._has_value(row.get(field)) for field in cls._OUTPUT_FIELDS)
            and cls._is_numeric_similarity(row.get("similarity"))
        )

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value is not None and value != "" and value != []

    @classmethod
    def _sort_key(cls, row: Mapping[str, Any]) -> tuple[float, int, str]:
        similarity = cls._numeric_score(row["similarity"])
        completeness = sum(cls._has_value(row.get(field)) for field in cls._OUTPUT_FIELDS)
        return (-similarity, -completeness, str(row["product_id"]))

    @staticmethod
    def _numeric_score(value: Any) -> float:
        return float(value)

    @staticmethod
    def _is_numeric_similarity(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def _to_output(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        return {field: row[field] for field in cls._OUTPUT_FIELDS}

    @staticmethod
    def _failed_not_found(evidence: EvidenceRecord) -> AgentResult:
        return AgentResult(
            agent=AgentName.COMPETITOR,
            status="failed",
            summary="No comparable competitor evidence was found.",
            evidence=[evidence],
            errors=["competitor_not_found"],
        )
