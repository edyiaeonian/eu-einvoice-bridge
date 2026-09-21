from datetime import date
from decimal import Decimal
from functools import cached_property
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from .enums import InvoiceTypeCode, VatCategory
from .lines import AllowanceCharge, LineItem, VatRate
from .numeric import ExactDecimal, money
from .parties import Party

CurrencyCode = Annotated[
    str, StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
]


class VatBreakdownEntry(BaseModel):
    """One row of the VAT breakdown (BG-23). Derived, never supplied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: VatCategory
    rate: VatRate
    taxable_amount: ExactDecimal
    tax_amount: ExactDecimal
    exemption_reason: str | None = None
    exemption_reason_code: str | None = None


class Totals(BaseModel):
    """Document totals (BG-22). Derived, never supplied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sum_line_net: ExactDecimal  # BT-106
    allowances_total: ExactDecimal  # BT-107
    charges_total: ExactDecimal  # BT-108
    total_without_vat: ExactDecimal  # BT-109
    total_vat: ExactDecimal  # BT-110
    total_with_vat: ExactDecimal  # BT-112
    prepaid_amount: ExactDecimal  # BT-113
    amount_due: ExactDecimal  # BT-115


def _reason_for_group(lines: list[LineItem]) -> tuple[str | None, str | None]:
    """The exemption reason shared by a group's lines.

    A breakdown entry holds one reason, so the lines feeding it must agree.
    Choosing one arbitrarily would discard the other without saying so.
    """
    reasons = {(line.exemption_reason, line.exemption_reason_code) for line in lines}
    if len(reasons) > 1:
        raise ValueError(
            f"lines in the same VAT group carry conflicting exemption reasons: "
            f"{sorted(str(r) for r in reasons)}"
        )
    return reasons.pop()


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=1)  # BT-1
    issue_date: date  # BT-2
    type_code: InvoiceTypeCode  # BT-3
    currency: CurrencyCode  # BT-5
    vat_accounting_currency: CurrencyCode | None = None  # BT-6
    due_date: date | None = None  # BT-9
    seller: Party
    buyer: Party
    lines: list[LineItem] = Field(min_length=1)
    allowance_charges: list[AllowanceCharge] = Field(default_factory=list)
    prepaid_amount: ExactDecimal = Field(default=Decimal("0.00"), ge=0)  # BT-113

    @computed_field
    @cached_property
    def vat_breakdown(self) -> list[VatBreakdownEntry]:
        """BG-23, grouped per BR-S-08 and taxed per BR-CO-17.

        Tax is computed once on the group total, not per line and summed: the
        two differ by cents, and BR-CO-17 checks the group.
        """
        keys: list[tuple[VatCategory, Decimal]] = []
        lines_by_key: dict[tuple[VatCategory, Decimal], list[LineItem]] = {}
        adjustments: dict[tuple[VatCategory, Decimal], Decimal] = {}

        for line in self.lines:
            key = (line.vat_category, line.vat_rate)
            if key not in lines_by_key:
                keys.append(key)
                lines_by_key[key] = []
            lines_by_key[key].append(line)

        for item in self.allowance_charges:
            key = (item.vat_category, item.vat_rate)
            if key not in lines_by_key:
                keys.append(key)
                lines_by_key.setdefault(key, [])
            signed = item.amount if item.is_charge else -item.amount
            adjustments[key] = adjustments.get(key, Decimal("0")) + signed

        entries = []
        for key in keys:
            category, rate = key
            group = lines_by_key[key]
            taxable = money(
                sum((money(line.net_amount) for line in group), Decimal("0"))
                + adjustments.get(key, Decimal("0"))
            )
            reason, reason_code = _reason_for_group(group) if group else (None, None)
            entries.append(
                VatBreakdownEntry(
                    category=category,
                    rate=rate,
                    taxable_amount=taxable,
                    tax_amount=money(taxable * rate),
                    exemption_reason=reason,
                    exemption_reason_code=reason_code,
                )
            )
        return entries

    @computed_field
    @cached_property
    def totals(self) -> Totals:
        """BG-22, following BR-CO-10 through BR-CO-16.

        BT-114 (rounding amount) is out of scope, so BR-CO-16 reduces to
        BT-112 - BT-113.
        """
        sum_line_net = money(
            sum((money(line.net_amount) for line in self.lines), Decimal("0"))
        )
        allowances = money(
            sum(
                (i.amount for i in self.allowance_charges if not i.is_charge),
                Decimal("0"),
            )
        )
        charges = money(
            sum((i.amount for i in self.allowance_charges if i.is_charge), Decimal("0"))
        )
        without_vat = money(sum_line_net - allowances + charges)
        total_vat = money(
            sum((e.tax_amount for e in self.vat_breakdown), Decimal("0"))
        )
        with_vat = money(without_vat + total_vat)

        return Totals(
            sum_line_net=sum_line_net,
            allowances_total=allowances,
            charges_total=charges,
            total_without_vat=without_vat,
            total_vat=total_vat,
            total_with_vat=with_vat,
            prepaid_amount=money(self.prepaid_amount),
            amount_due=money(with_vat - self.prepaid_amount),
        )

    def model_post_init(self, __context) -> None:
        # Force derivation so inconsistent input fails at construction rather
        # than at first access, somewhere far from the cause.
        _ = self.totals
