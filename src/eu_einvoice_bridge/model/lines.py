from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import VatCategory
from .numeric import ExactDecimal


def _check_exemption_reason(
    category: VatCategory,
    reason: str | None,
    reason_code: str | None,
) -> None:
    has_reason = reason is not None or reason_code is not None

    if category.requires_exemption_reason and not has_reason:
        raise ValueError(
            f"VAT category {category.value} requires an exemption reason: "
            f"give exemption_reason (BT-120) or exemption_reason_code (BT-121)"
        )
    if not category.requires_exemption_reason and has_reason:
        raise ValueError(
            f"VAT category {category.value} must not carry an exemption reason "
            f"(BT-120/BT-121)"
        )


class LineItem(BaseModel):
    """An invoice line (BG-25)."""

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    quantity: ExactDecimal = Field(ge=0)
    unit_code: str = Field(min_length=1)
    unit_price: ExactDecimal = Field(ge=0)
    net_amount: ExactDecimal = Field(ge=0)
    vat_category: VatCategory
    vat_rate: ExactDecimal = Field(ge=0)
    # Held on the line so the VAT breakdown can be derived rather than supplied.
    exemption_reason: str | None = None
    exemption_reason_code: str | None = None

    @model_validator(mode="after")
    def _exemption_reason_matches_category(self) -> Self:
        _check_exemption_reason(
            self.vat_category, self.exemption_reason, self.exemption_reason_code
        )
        return self


class AllowanceCharge(BaseModel):
    """A document-level allowance (BG-20) or charge (BG-21)."""

    model_config = ConfigDict(extra="forbid")

    is_charge: bool
    # Always positive: direction is carried by is_charge, never by the sign, so
    # that a sign error cannot quietly turn a discount into a surcharge.
    amount: ExactDecimal = Field(ge=0)
    vat_category: VatCategory
    vat_rate: ExactDecimal = Field(ge=0)
    reason: str | None = None
