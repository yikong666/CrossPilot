from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    category: Literal["consumer_electronics", "children_toys", "home_goods"]
    purchase_cost_cny: Decimal = Field(gt=0)
    selling_price_usd: Decimal | None = Field(default=None, gt=0)
    fx_cny_per_usd: Decimal | None = Field(default=None, gt=0)
    inbound_shipping_usd: Decimal | None = Field(default=None, ge=0)
    fba_fee_usd: Decimal | None = Field(default=None, ge=0)
    commission_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    ad_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    return_loss_rate: Decimal | None = Field(default=None, ge=0, lt=1)
    target_margin: Decimal | None = Field(default=None, ge=0, lt=1)
    weight_kg: Decimal | None = Field(default=None, gt=0)
    dimensions_cm: tuple[Decimal, Decimal, Decimal] | None = None
    material: str | None = None
    has_battery: bool | None = None
    intended_age: str | None = None
    provided_documents: list[str] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    product: ProductInput
    question: str = Field(min_length=2, max_length=2000)
    thread_id: str | None = None
