from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.pricing import PricingAgent
from app.contracts import AgentName, AgentTask, ProductInput
from app.services.fee_rule_repository import FeeRule, FeeRuleNotFoundError
from app.services.pricing_calculator import PricingResult


def _task() -> AgentTask:
    return AgentTask(
        task_id="pricing-1",
        agent=AgentName.PRICING,
        objective="Calculate a viable Amazon US selling price.",
    )


def _product(**overrides: object) -> ProductInput:
    values: dict[str, object] = {
        "name": "USB-C Cable",
        "category": "consumer_electronics",
        "purchase_cost_cny": Decimal("14.00"),
        "selling_price_usd": Decimal("12.99"),
        "fx_cny_per_usd": None,
        "inbound_shipping_usd": Decimal("0.80"),
        "fba_fee_usd": None,
        "commission_rate": None,
        "ad_rate": Decimal("0.10"),
        "return_loss_rate": Decimal("0.02"),
        "target_margin": Decimal("0.20"),
        "weight_kg": Decimal("0.20"),
    }
    values.update(overrides)
    return ProductInput.model_validate(values)


class RuleRepository:
    def __init__(self, rule: FeeRule | None = None, error: Exception | None = None) -> None:
        self.rule = rule
        self.error = error

    async def get_rule(self, category: str, weight_kg: Decimal) -> FeeRule:
        if self.error is not None:
            raise self.error
        assert category == "consumer_electronics"
        assert weight_kg == Decimal("0.20")
        assert self.rule is not None
        return self.rule


class Calculator:
    def __init__(self, result: PricingResult) -> None:
        self.result = result
        self.calls: list[tuple[ProductInput, FeeRule | None]] = []

    def __call__(self, product: ProductInput, fee_rule: FeeRule | None) -> PricingResult:
        self.calls.append((product, fee_rule))
        return self.result


class FailingCalculator:
    def __call__(self, product: ProductInput, fee_rule: FeeRule | None) -> PricingResult:
        del product, fee_rule
        raise RuntimeError("internal calculator detail")


def _calculation() -> PricingResult:
    return PricingResult(
        purchase_cost_usd="2.00",
        fixed_cost="5.30",
        variable_rate="0.2700",
        profit="4.18",
        margin="0.3218",
        break_even_price="7.26",
        target_price="10.15",
        assumptions={
            "purchase_cost_cny": Decimal("14.00"),
            "fx_cny_per_usd": Decimal("7.0000"),
            "fx_source": "fee_rule",
            "inbound_shipping_usd": Decimal("0.80"),
            "fba_fee_usd": Decimal("2.50"),
            "fba_fee_source": "fee_rule",
            "commission_rate": Decimal("0.1500"),
            "commission_source": "fee_rule",
            "ad_rate": Decimal("0.1000"),
            "return_loss_rate": Decimal("0.0200"),
            "target_margin": Decimal("0.2000"),
        },
    )


@pytest.mark.anyio
async def test_pricing_agent_uses_fee_rule_and_emits_auditable_calculator_evidence() -> None:
    """Removing calculator evidence or Decimal serialization must fail this test."""
    rule = FeeRule(commission_rate="0.15", fba_fee_usd="2.50", fx_cny_per_usd="7.00")
    calculator = Calculator(_calculation())

    result = await PricingAgent(RuleRepository(rule), calculator).run(_task(), _product())

    assert result.status == "success"
    assert result.evidence[0].source_type == "calculator"
    assert result.data["target_price"] == "10.15"
    assert result.data["assumptions"]["fx_source"] == "fee_rule"
    assert calculator.calls == [(_product(), rule)]


@pytest.mark.anyio
async def test_pricing_agent_keeps_user_fee_values_over_graph_rule() -> None:
    """Replacing supplied fee values with graph values must fail this test."""
    calculator = Calculator(_calculation())
    product = _product(
        fx_cny_per_usd=Decimal("7.20"),
        fba_fee_usd=Decimal("3.10"),
        commission_rate=Decimal("0.12"),
    )

    result = await PricingAgent(RuleRepository(), calculator).run(_task(), product)

    assert result.status == "success"
    assert calculator.calls == [(product, None)]


@pytest.mark.anyio
async def test_pricing_agent_reports_all_direct_and_rule_lookup_inputs_that_are_missing() -> None:
    """Silently defaulting any formula input or weight must fail this test."""
    product = _product(
        weight_kg=None,
        inbound_shipping_usd=None,
        ad_rate=None,
        return_loss_rate=None,
        target_margin=None,
    )

    result = await PricingAgent(RuleRepository(), Calculator(_calculation())).run(_task(), product)

    assert result.status == "need_input"
    assert result.missing_fields == [
        "weight_kg",
        "inbound_shipping_usd",
        "ad_rate",
        "return_loss_rate",
        "target_margin",
    ]


@pytest.mark.anyio
async def test_pricing_agent_only_requests_missing_rule_backed_field_after_rule_is_incomplete(
) -> None:
    """Requesting already supplied FX or commission again must fail this test."""
    product = _product(
        fx_cny_per_usd=Decimal("7.20"),
        commission_rate=Decimal("0.12"),
    )
    repository = RuleRepository(
        error=FeeRuleNotFoundError(
            "internal graph details",
            ("fx_cny_per_usd", "fba_fee_usd", "commission_rate"),
        )
    )

    result = await PricingAgent(repository, Calculator(_calculation())).run(_task(), product)

    assert result.status == "need_input"
    assert result.missing_fields == ["fba_fee_usd"]


@pytest.mark.anyio
async def test_pricing_agent_ignores_non_product_rule_error_fields() -> None:
    """Treating effective_date as a ProductInput field must fail this test."""
    product = _product(
        fx_cny_per_usd=Decimal("7.20"),
        commission_rate=Decimal("0.12"),
    )
    repository = RuleRepository(
        error=FeeRuleNotFoundError(
            "internal graph details",
            ("effective_date", "fba_fee_usd"),
        )
    )

    result = await PricingAgent(repository, Calculator(_calculation())).run(_task(), product)

    assert result.status == "need_input"
    assert result.missing_fields == ["fba_fee_usd"]


@pytest.mark.anyio
async def test_pricing_agent_fails_stably_when_rule_error_has_no_user_fillable_field() -> None:
    """Requesting a non-ProductInput field or raising AttributeError must fail this test."""
    repository = RuleRepository(
        error=FeeRuleNotFoundError("internal graph details", ("effective_date",))
    )

    result = await PricingAgent(repository, Calculator(_calculation())).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["pricing_rule_unavailable"]
    assert result.missing_fields == []


@pytest.mark.anyio
async def test_pricing_agent_returns_failed_when_fee_repository_is_unavailable() -> None:
    """Leaking repository implementation details to the user must fail this test."""
    result = await PricingAgent(
        RuleRepository(error=RuntimeError("neo4j unavailable")), Calculator(_calculation())
    ).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["fee_rule_lookup_failed"]


@pytest.mark.anyio
async def test_pricing_agent_returns_stable_error_code_for_calculator_failure() -> None:
    """Leaking calculator implementation details to the user must fail this test."""
    result = await PricingAgent(RuleRepository(FeeRule(
        commission_rate="0.15", fba_fee_usd="2.50", fx_cny_per_usd="7.00"
    )), FailingCalculator()).run(_task(), _product())

    assert result.status == "failed"
    assert result.errors == ["pricing_calculation_failed"]
