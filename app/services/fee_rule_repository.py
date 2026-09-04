"""Read deterministic Amazon US fee rules from the graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from app.core.errors import CrossPilotError

_MONEY = Decimal("0.01")
_RATE = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE, rounding=ROUND_HALF_UP)


class AsyncReadClient(Protocol):
    """Minimal graph dependency required by the fee-rule repository."""

    async def execute_read(
        self, cypher: str, params: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]:
        """Execute one read-only Cypher query and return record mappings."""


class FeeRule(BaseModel):
    commission_rate: Decimal = Field(ge=0, lt=1)
    fba_fee_usd: Decimal = Field(ge=0)
    fx_cny_per_usd: Decimal = Field(gt=0)
    fee_id: str | None = None

    @field_validator("commission_rate")
    @classmethod
    def round_commission_rate(cls, value: Decimal) -> Decimal:
        return _rate(value)

    @field_validator("fba_fee_usd")
    @classmethod
    def round_fba_fee(cls, value: Decimal) -> Decimal:
        return _money(value)

    @field_validator("fx_cny_per_usd")
    @classmethod
    def round_exchange_rate(cls, value: Decimal) -> Decimal:
        return _rate(value)


class FeeRuleNotFoundError(CrossPilotError):
    """Raised when the graph cannot supply one usable fee rule."""

    def __init__(self, message: str, fields: Sequence[str]) -> None:
        self.fields = tuple(fields)
        super().__init__(message)


FEE_RULE_CYPHER = """
MATCH (category:Category {category_id: $category})-[:USES_FEE_RULE]->(rule:FeeRule)
WHERE rule.marketplace = 'amazon_us'
  AND rule.effective_date <= date()
  AND (rule.min_weight_kg IS NULL OR rule.min_weight_kg <= $weight_kg)
  AND (rule.max_weight_kg IS NULL OR $weight_kg < rule.max_weight_kg)
RETURN rule.fee_id AS fee_id,
       rule.commission_rate AS commission_rate,
       rule.fba_fee_usd AS fba_fee_usd,
       rule.fx_cny_per_usd AS fx_cny_per_usd
ORDER BY rule.effective_date DESC
LIMIT 2
""".strip()


class FeeRuleRepository:
    """Fetch the single applicable Amazon US rule through a read-only client."""

    def __init__(self, read_client: AsyncReadClient) -> None:
        self._read_client = read_client

    async def get_rule(self, category: str, weight_kg: Decimal) -> FeeRule:
        rows = await self._read_client.execute_read(
            FEE_RULE_CYPHER,
            {"category": category, "weight_kg": weight_kg},
        )
        if not rows:
            raise FeeRuleNotFoundError(
                "No applicable Amazon US fee rule was found.",
                ("commission_rate", "fba_fee_usd", "fx_cny_per_usd"),
            )
        if len(rows) != 1:
            raise FeeRuleNotFoundError(
                "Multiple applicable Amazon US fee rules were found.",
                ("commission_rate", "fba_fee_usd", "fx_cny_per_usd"),
            )
        row = rows[0]
        required_fields = ("commission_rate", "fba_fee_usd", "fx_cny_per_usd")
        missing_fields = tuple(field for field in required_fields if row.get(field) is None)
        if missing_fields:
            raise FeeRuleNotFoundError(
                "The applicable Amazon US fee rule is incomplete.", missing_fields
            )
        return FeeRule.model_validate(dict(row))
