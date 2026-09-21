from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import VatCategory
from .numeric import MAX_DIGITS, Amount, ExactDecimal

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


# PEPPOL-EN16931-R120 compares with u:slack(..., 0.02), which is inclusive.
NET_AMOUNT_SLACK = Decimal("0.02")


def _check_net_amount(quantity: Decimal, unit_price: Decimal, net: Decimal) -> None:
    """A line's net amount must match quantity x unit price.

    EN16931 core has no such rule, but Peppol BIS Billing 3.0 does:
    PEPPOL-EN16931-R120, fatal, which compares against the unrounded product
    with 0.02 of slack either side. The same test is applied here. The check
    exists to catch a wrong figure, which should be reported rather than
    overwritten with the computed value; a one-cent gap is a rounding
    convention, and refusing it would be stricter than the rule that exists.

    R120's full formula also adds line charges, subtracts line allowances and
    divides the price by a base quantity. This model supports none of those
    (BG-27/28, BT-149), which is the only reason the short form is correct:
    supporting any of them means adopting the full formula here.
    """
    expected = quantity * unit_price
    if abs(net - expected) > NET_AMOUNT_SLACK:
        raise ValueError(
            f"net_amount: expected quantity x unit_price = {expected} "
            f"({quantity} x {unit_price}) within {NET_AMOUNT_SLACK}, got {net}"
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
    quantity: ExactDecimal = Field(ge=0, max_digits=MAX_DIGITS)
    unit_code: str = Field(min_length=1)
    unit_price: ExactDecimal = Field(ge=0, max_digits=MAX_DIGITS)
    net_amount: Amount = Field(ge=0, max_digits=MAX_DIGITS)  # BR-DEC-23
    vat_category: VatCategory
    vat_rate: VatRate
    # Held on the line so the VAT breakdown can be derived rather than supplied.
    exemption_reason: str | None = None
    exemption_reason_code: str | None = None

    @model_validator(mode="after")
    def _line_constraints(self) -> Self:
        _check_category_rate(self.vat_category, self.vat_rate)
        _check_exemption_reason(
            self.vat_category, self.exemption_reason, self.exemption_reason_code
        )
        _check_net_amount(self.quantity, self.unit_price, self.net_amount)
        return self


class AllowanceCharge(BaseModel):
    """A document-level allowance (BG-20) or charge (BG-21)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    is_charge: bool
    # Always positive: direction is carried by is_charge, never by the sign, so
    # that a sign error cannot quietly turn a discount into a surcharge.
    amount: Amount = Field(ge=0, max_digits=MAX_DIGITS)  # BR-DEC-01 / -05
    vat_category: VatCategory
    vat_rate: VatRate
    reason: str | None = None

    @model_validator(mode="after")
    def _category_matches_rate(self) -> Self:
        _check_category_rate(self.vat_category, self.vat_rate)
        return self
