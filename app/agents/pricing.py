"""Deterministic pricing specialist backed by approved fee rules."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from app.contracts import AgentName, AgentResult, AgentTask, EvidenceRecord, ProductInput
from app.services.fee_rule_repository import FeeRule, FeeRuleNotFoundError
from app.services.pricing_calculator import (
    MissingPricingFieldsError,
    PricingResult,
    calculate_pricing,
)


class FeeRuleSource(Protocol):
    async def get_rule(self, category: str, weight_kg: Decimal) -> FeeRule: ...


class PricingCalculator(Protocol):
    def __call__(self, product: ProductInput, fee_rule: FeeRule | None) -> PricingResult: ...


_RULE_BACKED_FIELDS = ("fx_cny_per_usd", "fba_fee_usd", "commission_rate")
_DIRECT_FORMULA_FIELDS = (
    "inbound_shipping_usd",
    "ad_rate",
    "return_loss_rate",
    "target_margin",
)


class PricingAgent:
    """Return an auditable calculator result, never a model-generated amount."""

    def __init__(
        self,
        fee_rule_repository: FeeRuleSource,
        calculator: PricingCalculator = calculate_pricing,
    ) -> None:
        self._fee_rule_repository = fee_rule_repository
        self._calculator = calculator

    async def run(self, task: AgentTask, product: ProductInput) -> AgentResult:
        del task
        needs_rule = any(getattr(product, field) is None for field in _RULE_BACKED_FIELDS)
        direct_missing = _missing_fields(product, _DIRECT_FORMULA_FIELDS)
        fee_rule: FeeRule | None = None

        if needs_rule:
            if product.weight_kg is None:
                return _need_input(["weight_kg", *direct_missing])
            try:
                fee_rule = await self._fee_rule_repository.get_rule(
                    product.category, product.weight_kg
                )
            except FeeRuleNotFoundError as error:
                missing_rule_fields = [
                    field for field in error.fields if getattr(product, field) is None
                ]
                return _need_input([*direct_missing, *missing_rule_fields])
            except Exception:
                return AgentResult(
                    agent=AgentName.PRICING,
                    status="failed",
                    summary="Pricing could not retrieve an applicable fee rule.",
                    errors=["fee_rule_lookup_failed"],
                )

        if direct_missing:
            return _need_input(direct_missing)
        try:
            calculation = self._calculator(product, fee_rule)
        except MissingPricingFieldsError as error:
            return _need_input(list(error.fields))
        except Exception:
            return AgentResult(
                agent=AgentName.PRICING,
                status="failed",
                summary="Pricing calculation failed.",
                errors=["pricing_calculation_failed"],
            )

        payload = calculation.model_dump(mode="json")
        return AgentResult(
            agent=AgentName.PRICING,
            status="success",
            summary=(
                "Deterministic pricing calculation completed; "
                f"target price is ${payload['target_price']}."
            ),
            data=payload,
            evidence=[
                EvidenceRecord(
                    source_type="calculator",
                    summary="Decimal pricing calculation with recorded assumptions.",
                    rows=[payload],
                )
            ],
        )


def _missing_fields(product: ProductInput, fields: Sequence[str]) -> list[str]:
    return [field for field in fields if getattr(product, field) is None]


def _need_input(fields: Sequence[str]) -> AgentResult:
    unique_fields = list(dict.fromkeys(fields))
    return AgentResult(
        agent=AgentName.PRICING,
        status="need_input",
        summary="Pricing needs additional inputs before a deterministic calculation can run.",
        missing_fields=unique_fields,
    )
