from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.contracts.api import ProductInput
from app.services.fee_rule_repository import FeeRule
from app.services.pricing_calculator import (
    InvalidPricingFormulaError,
    MissingPricingFieldsError,
    PricingResult,
    calculate_pricing,
)


def product(**overrides: object) -> ProductInput:
    values: dict[str, object] = {
        "name": "Magnetic Wireless Charger",
        "category": "consumer_electronics",
        "purchase_cost_cny": "70.00",
        "fx_cny_per_usd": "7.00",
        "inbound_shipping_usd": "2.00",
        "fba_fee_usd": "4.00",
        "commission_rate": "0.15",
        "ad_rate": "0.10",
        "return_loss_rate": "0.05",
        "selling_price_usd": "25.00",
        "target_margin": "0.20",
    }
    values.update(overrides)
    return ProductInput(**values)


def fee_rule(**overrides: object) -> FeeRule:
    values: dict[str, object] = {
        "commission_rate": "0.15",
        "fba_fee_usd": "4.00",
        "fx_cny_per_usd": "7.00",
    }
    values.update(overrides)
    return FeeRule(**values)


def test_calculates_hand_checked_pricing_example() -> None:
    """Fails if any formula factor or monetary rounding changes."""
    result = calculate_pricing(product(), fee_rule())

    assert result.fixed_cost == Decimal("16.00")
    assert result.variable_rate == Decimal("0.3000")
    assert result.profit == Decimal("1.50")
    assert result.margin == Decimal("0.0600")
    assert result.break_even_price == Decimal("22.86")
    assert result.target_price == Decimal("32.00")
    assert result.assumptions["fx_cny_per_usd"] == Decimal("7.00")


def test_user_values_override_fee_rule_values() -> None:
    """Fails if graph defaults replace explicit user-entered fees or exchange rate."""
    result = calculate_pricing(
        product(fx_cny_per_usd="10", fba_fee_usd="3", commission_rate="0.10"),
        fee_rule(fx_cny_per_usd="7", fba_fee_usd="4", commission_rate="0.15"),
    )

    assert result.fixed_cost == Decimal("12.00")
    assert result.variable_rate == Decimal("0.2500")
    assert result.profit == Decimal("6.75")


def test_zero_logistics_cost_is_included_as_zero() -> None:
    """Fails if a valid zero logistics input is mistaken for missing data."""
    result = calculate_pricing(product(inbound_shipping_usd="0", fba_fee_usd="0"), fee_rule())

    assert result.fixed_cost == Decimal("10.00")


def test_missing_selling_price_returns_target_without_current_profit() -> None:
    """Fails if current profit is fabricated when a selling price is absent."""
    result = calculate_pricing(product(selling_price_usd=None), fee_rule())

    assert result.profit is None
    assert result.margin is None
    assert result.target_price == Decimal("32.00")


def test_missing_formula_inputs_name_every_required_field() -> None:
    """Fails if absent inputs are silently defaulted instead of surfaced to the caller."""
    with pytest.raises(MissingPricingFieldsError) as raised:
        calculate_pricing(
            product(
                fx_cny_per_usd=None,
                inbound_shipping_usd=None,
                fba_fee_usd=None,
                commission_rate=None,
                ad_rate=None,
                return_loss_rate=None,
                target_margin=None,
            ),
            None,
        )

    assert raised.value.fields == (
        "fx_cny_per_usd",
        "inbound_shipping_usd",
        "fba_fee_usd",
        "commission_rate",
        "ad_rate",
        "return_loss_rate",
        "target_margin",
    )


def test_rejects_rates_that_leave_no_price_denominator() -> None:
    """Fails if unsafe variable-rate denominators are allowed to divide by zero."""
    with pytest.raises(InvalidPricingFormulaError, match="break-even"):
        calculate_pricing(
            product(commission_rate="0.9999", ad_rate="0.0001", return_loss_rate="0"), fee_rule()
        )


def test_rejects_target_margin_that_leaves_no_target_price_denominator() -> None:
    """Fails if unsafe target-price denominators are allowed to divide by zero."""
    with pytest.raises(InvalidPricingFormulaError, match="target price"):
        calculate_pricing(
            product(
                commission_rate="0.70",
                ad_rate="0.10",
                return_loss_rate="0",
                target_margin="0.20",
            ),
            fee_rule(),
        )


def test_rounds_money_half_up_and_rates_to_four_places() -> None:
    """Fails if monetary outputs use binary floats or banker's rounding."""
    result = calculate_pricing(
        product(
            purchase_cost_cny="1.005",
            fx_cny_per_usd="1",
            inbound_shipping_usd="0",
            fba_fee_usd="0",
            commission_rate="0.11111",
            ad_rate="0.11111",
            return_loss_rate="0.11111",
            selling_price_usd="1.005",
            target_margin="0.1",
        ),
        fee_rule(),
    )

    assert result.fixed_cost == Decimal("1.01")
    assert result.variable_rate == Decimal("0.3333")
    assert result.profit == Decimal("-0.33")
    assert result.margin == Decimal("-0.3333")


def test_rejects_rate_that_quantizes_to_one() -> None:
    """Fails if rate normalization can turn a legal input into an illegal stored rate."""
    with pytest.raises(ValidationError):
        fee_rule(commission_rate="0.99999")


@pytest.mark.parametrize("rate_field", ["commission_rate", "ad_rate", "return_loss_rate"])
def test_rejects_user_rate_that_quantizes_to_one_before_formula(rate_field: str) -> None:
    """Fails if a user-priority rate is checked before, but displayed after, normalization."""
    rates: dict[str, object] = {
        "commission_rate": "0",
        "ad_rate": "0",
        "return_loss_rate": "0",
    }
    rates[rate_field] = "0.99999"

    with pytest.raises(InvalidPricingFormulaError, match="break-even"):
        calculate_pricing(product(target_margin="0", **rates), fee_rule())


def test_rejects_calculation_when_rounding_makes_required_price_zero() -> None:
    """Fails if a positive sub-cent price escapes the result model as $0.00."""
    with pytest.raises(ValidationError):
        calculate_pricing(
            product(
                purchase_cost_cny="0.004",
                fx_cny_per_usd="1",
                inbound_shipping_usd="0",
                fba_fee_usd="0",
                commission_rate="0",
                ad_rate="0",
                return_loss_rate="0",
            ),
            fee_rule(),
        )


def test_result_model_rechecks_positive_prices_after_rounding() -> None:
    """Fails if direct result construction bypasses post-normalization price constraints."""
    with pytest.raises(ValidationError):
        PricingResult(
            purchase_cost_usd="0.004",
            fixed_cost="0.004",
            variable_rate="0",
            profit=None,
            margin=None,
            break_even_price="1",
            target_price="1",
            assumptions={"purchase_cost_cny": Decimal("0.004")},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("purchase_cost_cny", "-0.01"),
        ("inbound_shipping_usd", "-0.01"),
        ("commission_rate", "-0.01"),
    ],
)
def test_product_contract_rejects_negative_pricing_inputs(field: str, value: str) -> None:
    """Fails if negative commercial inputs bypass the locked ProductInput contract."""
    with pytest.raises(ValidationError):
        product(**{field: value})
