from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from statistics import mean, median, quantiles
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

    _CONCENTRATED_SHARE = Decimal("0.5")
    _BALANCED_SHARE = Decimal("0.25")

    _REQUIRED_AGGREGATION_FIELDS = frozenset(
        {
            "demand_level",
            "sample_size",
            "prices",
            "brands",
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
            prices = [self._finite_decimal(value) for value in aggregate["prices"]]
            brands = [str(value).strip().casefold() for value in aggregate["brands"]]
            price_statistics = self._price_statistics(prices)
            top_brand_share = self._top_brand_share(brands)
            competition_level = self._competition_level(top_brand_share)
        except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
            return self._failed("market_data_incomplete")

        mainstream_band = {
            "min_usd": price_statistics["mainstream_min_usd"],
            "max_usd": price_statistics["mainstream_max_usd"],
        }
        return AgentResult(
            agent=AgentName.MARKET,
            status="success",
            summary=(
                "Amazon US market assessment is an offline synthetic-data proxy, "
                "not a live demand or capacity estimate."
            ),
            data={
                "demand_level": aggregate["demand_level"],
                "price_band": mainstream_band,
                "price_statistics": price_statistics,
                "top_brand_share": self._format_share(top_brand_share),
                "competition_level": competition_level,
                "competition_basis": (
                    f"top brand share among {len(brands)} aligned product samples"
                ),
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
        except (InvalidOperation, TypeError, ValueError):
            return False
        prices = aggregate["prices"]
        brands = aggregate["brands"]
        if not isinstance(prices, list) or not isinstance(brands, list):
            return False
        if not prices or len(prices) != len(brands):
            return False
        if not all(isinstance(brand, str) and brand.strip() for brand in brands):
            return False
        try:
            price_values = [cls._finite_decimal(value) for value in prices]
        except (InvalidOperation, TypeError, ValueError):
            return False
        return sample_size > 0 and all(price >= 0 for price in price_values)

    @staticmethod
    def _finite_decimal(value: Any) -> Decimal:
        numeric_value = Decimal(str(value))
        if not numeric_value.is_finite():
            raise ValueError("aggregation value must be finite")
        return numeric_value

    @classmethod
    def _price_statistics(cls, prices: list[Decimal]) -> dict[str, str]:
        if len(prices) == 1:
            lower_quartile = upper_quartile = prices[0]
        else:
            lower_quartile, _, upper_quartile = quantiles(prices, n=4, method="inclusive")
        return {
            "min_usd": cls._format_usd(min(prices)),
            "max_usd": cls._format_usd(max(prices)),
            "average_usd": cls._format_usd(mean(prices)),
            "median_usd": cls._format_usd(median(prices)),
            "mainstream_min_usd": cls._format_usd(lower_quartile),
            "mainstream_max_usd": cls._format_usd(upper_quartile),
        }

    @staticmethod
    def _top_brand_share(brands: list[str]) -> Decimal:
        return Decimal(max(Counter(brands).values())) / Decimal(len(brands))

    @staticmethod
    def _competition_level(top_brand_share: Decimal) -> str:
        if top_brand_share >= MarketAgent._CONCENTRATED_SHARE:
            return "concentrated"
        if top_brand_share >= MarketAgent._BALANCED_SHARE:
            return "balanced"
        return "fragmented"

    @staticmethod
    def _format_usd(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.01")), "f")

    @staticmethod
    def _format_share(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.0001")), "f")

    @staticmethod
    def _market_question(task: AgentTask, product: ProductInput) -> str:
        return (
            f"{task.objective} For Amazon US category {product.category}, return one "
            "offline aggregate with demand_level and sample_size, plus COLLECT of each "
            "product price AS prices and the corresponding brand name in the same product "
            "order AS brands, for deterministic price statistics and top-brand share."
        )

    @staticmethod
    def _failed(error: str) -> AgentResult:
        return AgentResult(
            agent=AgentName.MARKET,
            status="failed",
            summary="Market analysis could not be completed from available offline graph data.",
            errors=[error],
        )
