from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.contracts.agents import AgentName, AgentResult, AgentTask
from app.contracts.api import ProductInput
from app.contracts.graph import EvidenceRecord


class MarketQueryService(Protocol):
    """Narrow read-only dependency required by the Market Agent."""

    async def query(
        self,
        question: str,
        entity_hint: str | None,
        purpose: str,
    ) -> EvidenceRecord: ...


class MarketAgent:
    """Produces an evidence-backed market proxy assessment for Amazon US MVP categories."""

    _REQUIRED_AGGREGATION_FIELDS = frozenset(
        {
            "demand_level",
            "sample_size",
            "min_price",
            "max_price",
            "brand_count",
            "product_count",
        }
    )

    def __init__(self, query_service: MarketQueryService) -> None:
        self._query_service = query_service

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        try:
            evidence = await self._query_service.query(
                question=self._market_question(task, product),
                entity_hint=product.name,
                purpose="market",
            )
        except Exception:
            return self._failed("market_query_failed")

        if not evidence.rows:
            return self._failed("market_data_unavailable")

        aggregate = evidence.rows[0]
        if not self._has_valid_aggregates(aggregate):
            return self._failed("market_data_incomplete")
        try:
            competition_level = self._competition_level(
                self._finite_decimal(aggregate["brand_count"]),
                self._finite_decimal(aggregate["product_count"]),
            )
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            return self._failed("market_data_incomplete")

        return AgentResult(
            agent=AgentName.MARKET,
            status="success",
            summary=(
                "Amazon US market assessment is an offline synthetic-data proxy, "
                "not a live demand or capacity estimate."
            ),
            data={
                "demand_level": aggregate["demand_level"],
                "price_band": {
                    "min_usd": aggregate["min_price"],
                    "max_usd": aggregate["max_price"],
                },
                "competition_level": competition_level,
                "sample_size": aggregate["sample_size"],
                "assessment_basis": "offline synthetic Amazon US proxy data",
            },
            evidence=[evidence],
        )

    @classmethod
    def _has_valid_aggregates(cls, aggregate: dict[str, Any]) -> bool:
        if not all(aggregate.get(field) is not None for field in cls._REQUIRED_AGGREGATION_FIELDS):
            return False
        try:
            sample_size = cls._finite_decimal(aggregate["sample_size"])
            min_price = cls._finite_decimal(aggregate["min_price"])
            max_price = cls._finite_decimal(aggregate["max_price"])
            brand_count = cls._finite_decimal(aggregate["brand_count"])
            product_count = cls._finite_decimal(aggregate["product_count"])
        except (InvalidOperation, TypeError, ValueError):
            return False
        return (
            sample_size > 0
            and product_count > 0
            and 0 <= brand_count <= product_count
            and min_price >= 0
            and max_price >= 0
            and min_price <= max_price
        )

    @staticmethod
    def _finite_decimal(value: Any) -> Decimal:
        numeric_value = Decimal(str(value))
        if not numeric_value.is_finite():
            raise ValueError("aggregation value must be finite")
        return numeric_value

    @staticmethod
    def _competition_level(brand_count: Decimal, product_count: Decimal) -> str:
        brand_share = brand_count / product_count
        if brand_share >= Decimal("0.5"):
            return "fragmented"
        if brand_share >= Decimal("0.25"):
            return "balanced"
        return "concentrated"

    @staticmethod
    def _market_question(task: AgentTask, product: ProductInput) -> str:
        return (
            f"{task.objective} For Amazon US category {product.category}, return one "
            "offline aggregate with demand_level, sample_size, min_price, max_price, "
            "brand_count, and product_count for the market proxy assessment."
        )

    @staticmethod
    def _failed(error: str) -> AgentResult:
        return AgentResult(
            agent=AgentName.MARKET,
            status="failed",
            summary="Market analysis could not be completed from available offline graph data.",
            errors=[error],
        )
