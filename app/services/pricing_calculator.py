"""Deterministic Decimal-based pricing calculations for the pricing agent."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts.api import ProductInput
from app.core.errors import CrossPilotError
from app.services.fee_rule_repository import FeeRule

_MONEY = Decimal("0.01")
_RATE = Decimal("0.0001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE, rounding=ROUND_HALF_UP)


def _decimal(value: Decimal | str | int | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class MissingPricingFieldsError(CrossPilotError):
    """Raised when a deterministic formula cannot be evaluated from supplied inputs."""

    def __init__(self, fields: Sequence[str]) -> None:
        self.fields = tuple(fields)
        super().__init__(f"Missing required pricing fields: {', '.join(self.fields)}")


class InvalidPricingFormulaError(CrossPilotError):
    """Raised when the requested rates leave no valid formula denominator."""


class PricingResult(BaseModel):
    """Auditable result: monetary outputs use cents and percentage outputs use four decimals."""

    model_config = ConfigDict(frozen=True)

    purchase_cost_usd: Decimal = Field(gt=0)
    fixed_cost: Decimal = Field(gt=0)
    variable_rate: Decimal = Field(ge=0, lt=1)
    profit: Decimal | None = None
    margin: Decimal | None = None
    break_even_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    assumptions: dict[str, Decimal | Literal["user_input", "fee_rule"]]

    @field_validator(
        "purchase_cost_usd",
        "fixed_cost",
        "profit",
        "break_even_price",
        "target_price",
        mode="before",
    )
    @classmethod
    def round_money_fields(cls, value: Decimal | str | int | float | None) -> Decimal | None:
        return None if value is None else _money(_decimal(value))

    @field_validator("variable_rate", "margin", mode="before")
    @classmethod
    def round_rate_fields(cls, value: Decimal | str | int | float | None) -> Decimal | None:
        return None if value is None else _rate(_decimal(value))


def _prefer_user_value(
    value: Decimal | None, rule_value: Decimal | None
) -> tuple[Decimal | None, Literal["user_input", "fee_rule"] | None]:
    if value is not None:
        return value, "user_input"
    if rule_value is not None:
        return rule_value, "fee_rule"
    return None, None


def calculate_pricing(product: ProductInput, fee_rule: FeeRule | None) -> PricingResult:
    """Calculate pricing from explicit inputs, using graph rules only as a fallback."""
    rule_fx = fee_rule.fx_cny_per_usd if fee_rule is not None else None
    rule_fba_fee = fee_rule.fba_fee_usd if fee_rule is not None else None
    rule_commission = fee_rule.commission_rate if fee_rule is not None else None
    fx, fx_source = _prefer_user_value(product.fx_cny_per_usd, rule_fx)
    fba_fee, fba_source = _prefer_user_value(product.fba_fee_usd, rule_fba_fee)
    commission_rate, commission_source = _prefer_user_value(
        product.commission_rate, rule_commission
    )

    required_values: tuple[tuple[str, Decimal | None], ...] = (
        ("fx_cny_per_usd", fx),
        ("inbound_shipping_usd", product.inbound_shipping_usd),
        ("fba_fee_usd", fba_fee),
        ("commission_rate", commission_rate),
        ("ad_rate", product.ad_rate),
        ("return_loss_rate", product.return_loss_rate),
        ("target_margin", product.target_margin),
    )
    missing_fields = tuple(field for field, value in required_values if value is None)
    if missing_fields:
        raise MissingPricingFieldsError(missing_fields)

    assert fx is not None
    assert fba_fee is not None
    assert commission_rate is not None
    assert product.inbound_shipping_usd is not None
    assert product.ad_rate is not None
    assert product.return_loss_rate is not None
    assert product.target_margin is not None

    purchase_cost_usd = product.purchase_cost_cny / fx
    fixed_cost = purchase_cost_usd + product.inbound_shipping_usd + fba_fee
    normalized_commission_rate = _rate(commission_rate)
    normalized_ad_rate = _rate(product.ad_rate)
    normalized_return_loss_rate = _rate(product.return_loss_rate)
    normalized_target_margin = _rate(product.target_margin)
    variable_rate = normalized_commission_rate + normalized_ad_rate + normalized_return_loss_rate
    break_even_denominator = Decimal("1") - variable_rate
    if break_even_denominator <= 0:
        raise InvalidPricingFormulaError(
            "Cannot calculate break-even price: denominator must be positive."
        )
    target_denominator = break_even_denominator - normalized_target_margin
    if target_denominator <= 0:
        raise InvalidPricingFormulaError(
            "Cannot calculate target price: denominator must be positive."
        )

    profit: Decimal | None = None
    margin: Decimal | None = None
    if product.selling_price_usd is not None:
        profit = product.selling_price_usd * break_even_denominator - fixed_cost
        margin = profit / product.selling_price_usd

    return PricingResult(
        purchase_cost_usd=purchase_cost_usd,
        fixed_cost=fixed_cost,
        variable_rate=variable_rate,
        profit=profit,
        margin=margin,
        break_even_price=fixed_cost / break_even_denominator,
        target_price=fixed_cost / target_denominator,
        assumptions={
            "purchase_cost_cny": product.purchase_cost_cny,
            "fx_cny_per_usd": fx,
            "fx_source": fx_source or "fee_rule",
            "inbound_shipping_usd": product.inbound_shipping_usd,
            "fba_fee_usd": fba_fee,
            "fba_fee_source": fba_source or "fee_rule",
            "commission_rate": normalized_commission_rate,
            "commission_source": commission_source or "fee_rule",
            "ad_rate": normalized_ad_rate,
            "return_loss_rate": normalized_return_loss_rate,
            "target_margin": normalized_target_margin,
        },
    )
