from datetime import date
from decimal import Decimal
from functools import cached_property
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from .enums import InvoiceTypeCode, VatCategory
from .lines import AllowanceCharge, LineItem, VatRate
from .numeric import MAX_DIGITS, Amount, ExactDecimal, money
from .parties import Party
from .polish import PolishExtras

CurrencyCode = Annotated[
    str, StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
]


class VatBreakdownEntry(BaseModel):
    """One row of the VAT breakdown (BG-23). Derived, never supplied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: VatCategory
    rate: VatRate
    taxable_amount: Amount
    tax_amount: Amount
    exemption_reason: str | None = None
    exemption_reason_code: str | None = None
    # Not an EN16931 business term. The group's tax converted to the VAT
    # accounting currency, so BT-111 and FA(3)'s per-rate PLN amounts are built
    # from the same rounded figures and cannot disagree.
    tax_amount_in_accounting_currency: Amount | None = None


class Totals(BaseModel):
    """Document totals (BG-22). Derived, never supplied."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sum_line_net: Amount  # BT-106
    allowances_total: Amount  # BT-107
    charges_total: Amount  # BT-108
    total_without_vat: Amount  # BT-109
    total_vat: Amount  # BT-110
    total_with_vat: Amount  # BT-112
    prepaid_amount: Amount  # BT-113
    amount_due: Amount  # BT-115
    total_vat_in_accounting_currency: Amount | None = None  # BT-111


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
    model_config = ConfigDict(extra="forbid", frozen=True)

    number: str = Field(min_length=1)  # BT-1
    issue_date: date  # BT-2
    type_code: InvoiceTypeCode  # BT-3
    currency: CurrencyCode  # BT-5
    vat_accounting_currency: CurrencyCode | None = None  # BT-6
    # Units of BT-6 per unit of BT-5. EN16931 has no field for it -- it records
    # only the converted total, BT-111 -- but both outputs need it to derive
    # their amounts in the accounting currency.
    exchange_rate: ExactDecimal | None = Field(default=None, gt=0, max_digits=MAX_DIGITS)
    due_date: date | None = None  # BT-9
    seller: Party
    buyer: Party
    lines: tuple[LineItem, ...] = Field(min_length=1)
    allowance_charges: tuple[AllowanceCharge, ...] = ()
    prepaid_amount: Amount = Field(
        default=Decimal("0.00"), ge=0, max_digits=MAX_DIGITS
    )  # BT-113, BR-DEC-16
    # Needed only for FA(3); whether its absence blocks that output is decided
    # by the FA(3) mapping, not here.
    extras: PolishExtras | None = None

    @model_validator(mode="after")
    def _accounting_currency_needs_a_rate(self) -> Self:
        """BT-6 and the exchange rate only make sense together.

        BT-6 without a rate leaves BT-111 underivable, which BR-53 makes fatal; a
        rate without BT-6 converts into nothing. And a VAT accounting currency
        equal to the invoice currency would be a conversion to itself.
        """
        if self.vat_accounting_currency is not None and self.exchange_rate is None:
            raise ValueError(
                "exchange_rate is required when vat_accounting_currency (BT-6) is "
                "given: BT-111 is derived from it"
            )
        if self.exchange_rate is not None and self.vat_accounting_currency is None:
            raise ValueError(
                "vat_accounting_currency (BT-6) is required when exchange_rate is "
                "given: it names the currency the rate converts into"
            )
        if self.vat_accounting_currency == self.currency:
            raise ValueError(
                "vat_accounting_currency (BT-6) must differ from the invoice "
                "currency (BT-5)"
            )
        return self

    @computed_field
    @cached_property
    def vat_breakdown(self) -> tuple[VatBreakdownEntry, ...]:
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
            tax = money(taxable * rate)
            entries.append(
                VatBreakdownEntry(
                    category=category,
                    rate=rate,
                    taxable_amount=taxable,
                    tax_amount=tax,
                    exemption_reason=reason,
                    exemption_reason_code=reason_code,
                    tax_amount_in_accounting_currency=(
                        money(tax * self.exchange_rate)
                        if self.exchange_rate is not None
                        else None
                    ),
                )
            )
        return tuple(entries)

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
            # Summed from the per-group conversions rather than converting the
            # total: the two can differ by a cent, and FA(3) states PLN tax per
            # rate, so this keeps both documents stating the same figure.
            total_vat_in_accounting_currency=(
                sum(
                    (e.tax_amount_in_accounting_currency for e in self.vat_breakdown),
                    Decimal("0.00"),
                )
                if self.exchange_rate is not None
                else None
            ),
        )

    def model_post_init(self, __context) -> None:
        # Force derivation so inconsistent input fails at construction rather
        # than at first access, somewhere far from the cause.
        _ = self.totals

    def model_copy(self, *, update=None, deep: bool = False) -> Self:
        """Copy, rebuilding through validation whenever anything changes.

        frozen=True stops assignment, but Pydantic's model_copy goes around it:
        it copies __dict__ -- the cached breakdown and totals included -- and
        applies `update` without validating it. A copy with a new prepaid amount
        would keep the old amount due, and an update of 0.005 would slip past
        the two-decimal rule. An unchanged copy keeps a correct cache, so only
        updates take the slower path.
        """
        if not update:
            return super().model_copy(deep=deep)
        data = self.model_dump(exclude={"vat_breakdown", "totals"})
        return type(self).model_validate({**data, **update})
