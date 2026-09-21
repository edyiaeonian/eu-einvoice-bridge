from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import VatCategory
from .numeric import Amount, ExactDecimal

# Rates are fractions: 0.23 means 23%. The upper bound catches the percentage
# mistake -- 23 would otherwise compute 2300% tax without complaint.
VatRate = Annotated[ExactDecimal, Field(ge=0, le=1)]


def _check_category_rate(category: VatCategory, rate: Decimal) -> None:
    """BR-S-05 against BR-Z/E/AE/G/IC-05, and the same for allowances/charges."""
    if category.requires_positive_rate and rate <= 0:
        raise ValueError(
            f"vat_rate: VAT category {category.value} requires a rate "
            f"greater than zero"
        )
    if not category.requires_positive_rate and rate != 0:
        raise ValueError(
            f"vat_rate: VAT category {category.value} requires a rate of zero, "
            f"got {rate}"
        )


def _check_exemption_reason(
    category: VatCategory,
    reason: str | None,
    reason_code: str | None,
) -> None:
    has_reason = reason is not None or reason_code is not None

    if category.requires_exemption_reason and not has_reason:
        raise ValueError(
            f"exemption_reason: VAT category {category.value} requires one; "
            f"give exemption_reason (BT-120) or exemption_reason_code (BT-121)"
        )
    if not category.requires_exemption_reason and has_reason:
        raise ValueError(
            f"exemption_reason: VAT category {category.value} must not carry one "
            f"(BT-120/BT-121)"
        )


class LineItem(BaseModel):
    """An invoice line (BG-25)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    line_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    quantity: ExactDecimal = Field(ge=0)
    unit_code: str = Field(min_length=1)
    unit_price: ExactDecimal = Field(ge=0)
    net_amount: Amount = Field(ge=0)  # BR-DEC-23
    vat_category: VatCategory
    vat_rate: VatRate
    # Held on the line so the VAT breakdown can be derived rather than supplied.
    exemption_reason: str | None = None
    exemption_reason_code: str | None = None

    @model_validator(mode="after")
    def _category_constraints(self) -> Self:
        _check_category_rate(self.vat_category, self.vat_rate)
        _check_exemption_reason(
            self.vat_category, self.exemption_reason, self.exemption_reason_code
        )
        return self


class AllowanceCharge(BaseModel):
    """A document-level allowance (BG-20) or charge (BG-21)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    is_charge: bool
    # Always positive: direction is carried by is_charge, never by the sign, so
    # that a sign error cannot quietly turn a discount into a surcharge.
    amount: Amount = Field(ge=0)  # BR-DEC-01 / BR-DEC-05
    vat_category: VatCategory
    vat_rate: VatRate
    reason: str | None = None

    @model_validator(mode="after")
    def _category_matches_rate(self) -> Self:
        _check_category_rate(self.vat_category, self.vat_rate)
        return self
