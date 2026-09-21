"""VAT breakdown and totals derivation, and the rounding rules behind them.

Formulas are quoted from the vendored Schematron rather than remembered:
BR-S-08 for the category taxable amount, BR-CO-10 to BR-CO-17 for the totals.
"""

from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from pydantic import ValidationError

from eu_einvoice_bridge.model import (
    Address,
    AllowanceCharge,
    Invoice,
    InvoiceTypeCode,
    LineItem,
    Party,
    VatCategory,
    money,
)

STANDARD_RATE = Decimal("0.23")


def a_party(**overrides):
    return Party(
        **{
            "name": "Acme sp. z o.o.",
            "vat_id": "PL5555555555",
            "vat_scheme": "PL",
            "address": Address(
                street="ul. Marszałkowska 1",
                city="Warszawa",
                postal_code="00-001",
                country="PL",
            ),
            **overrides,
        }
    )


def a_line(line_id="1", net="100.00", rate=STANDARD_RATE, category=None, **overrides):
    category = category or VatCategory.STANDARD
    fields = {
        "line_id": line_id,
        "name": "Widget",
        "quantity": Decimal("1"),
        "unit_code": "C62",
        "unit_price": Decimal(net),
        "net_amount": Decimal(net),
        "vat_category": category,
        "vat_rate": rate,
    }
    if category.requires_exemption_reason:
        fields["exemption_reason"] = "Reason"
    return LineItem(**{**fields, **overrides})


def an_invoice(lines=None, **overrides):
    return Invoice(
        **{
            "number": "FV/2026/001",
            "issue_date": date(2026, 9, 21),
            "type_code": InvoiceTypeCode.COMMERCIAL_INVOICE,
            "currency": "PLN",
            "seller": a_party(),
            "buyer": a_party(name="Buyer sp. z o.o.", vat_id="PL1111111111"),
            "lines": lines if lines is not None else [a_line()],
            **overrides,
        }
    )


class TestRounding:
    def test_half_up_not_bankers_rounding(self):
        # The whole point of stating the mode: these disagree, silently.
        assert money(Decimal("0.005")) == Decimal("0.01")
        assert Decimal("0.005").quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        ) == Decimal("0.00")

    def test_half_up_at_a_second_boundary(self):
        assert money(Decimal("0.025")) == Decimal("0.03")
        assert Decimal("0.025").quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        ) == Decimal("0.02")

    def test_a_realistic_invoice_hits_the_boundary(self):
        # 0.10 at 5% is exactly 0.005 of tax.
        invoice = an_invoice(
            lines=[a_line(net="0.10", rate=Decimal("0.05"))],
        )
        assert invoice.vat_breakdown[0].tax_amount == Decimal("0.01")


class TestBreakdownGrouping:
    def test_lines_sharing_category_and_rate_collapse_into_one_entry(self):
        invoice = an_invoice(
            lines=[a_line("1", net="100.00"), a_line("2", net="50.00")]
        )
        assert len(invoice.vat_breakdown) == 1
        assert invoice.vat_breakdown[0].taxable_amount == Decimal("150.00")

    def test_different_rates_produce_separate_entries(self):
        invoice = an_invoice(
            lines=[
                a_line("1", net="100.00", rate=STANDARD_RATE),
                a_line("2", net="100.00", rate=Decimal("0.08")),
            ]
        )
        assert len(invoice.vat_breakdown) == 2

    def test_zero_rated_and_exempt_do_not_merge_despite_equal_rates(self):
        invoice = an_invoice(
            lines=[
                a_line("1", net="100.00", rate=Decimal("0"), category=VatCategory.ZERO_RATED),
                a_line("2", net="100.00", rate=Decimal("0"), category=VatCategory.EXEMPT),
            ]
        )
        assert len(invoice.vat_breakdown) == 2
        assert {e.category for e in invoice.vat_breakdown} == {
            VatCategory.ZERO_RATED,
            VatCategory.EXEMPT,
        }


class TestTaxIsComputedOnTheGroupNotPerLine:
    """BR-CO-17 checks the breakdown, so the breakdown is where tax is computed.

    Summing per-line tax gives a different answer, and the difference is exactly
    what the rule would catch.
    """

    def test_group_level_result_differs_from_per_line_and_group_level_wins(self):
        lines = [a_line(str(i), net="0.50") for i in range(1, 4)]
        invoice = an_invoice(lines=lines)

        per_line_total = sum(money(Decimal("0.50") * STANDARD_RATE) for _ in lines)
        group_total = money(Decimal("1.50") * STANDARD_RATE)

        assert per_line_total == Decimal("0.36")
        assert group_total == Decimal("0.35")
        assert invoice.vat_breakdown[0].tax_amount == group_total

    def test_satisfies_br_co_17_within_the_official_tolerance(self):
        # The Schematron allows |stated - recomputed| < 1 currency unit.
        invoice = an_invoice(
            lines=[a_line("1", net="1234.56"), a_line("2", net="99.99")]
        )
        entry = invoice.vat_breakdown[0]
        recomputed = money(entry.taxable_amount * entry.rate)
        assert abs(entry.tax_amount - recomputed) < Decimal("1")


class TestAllowancesAndCharges:
    def test_allowance_reduces_the_taxable_amount_of_its_category(self):
        # BR-S-08: taxable = Σ lines + Σ charges - Σ allowances, matched on
        # category and rate.
        invoice = an_invoice(
            lines=[a_line("1", net="100.00")],
            allowance_charges=[
                AllowanceCharge(
                    is_charge=False,
                    amount=Decimal("10.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                )
            ],
        )
        assert invoice.vat_breakdown[0].taxable_amount == Decimal("90.00")

    def test_charge_increases_the_taxable_amount(self):
        invoice = an_invoice(
            lines=[a_line("1", net="100.00")],
            allowance_charges=[
                AllowanceCharge(
                    is_charge=True,
                    amount=Decimal("10.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                )
            ],
        )
        assert invoice.vat_breakdown[0].taxable_amount == Decimal("110.00")

    def test_allowance_only_affects_its_own_rate_group(self):
        invoice = an_invoice(
            lines=[
                a_line("1", net="100.00", rate=STANDARD_RATE),
                a_line("2", net="100.00", rate=Decimal("0.08")),
            ],
            allowance_charges=[
                AllowanceCharge(
                    is_charge=False,
                    amount=Decimal("10.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                )
            ],
        )
        by_rate = {e.rate: e.taxable_amount for e in invoice.vat_breakdown}
        assert by_rate[STANDARD_RATE] == Decimal("90.00")
        assert by_rate[Decimal("0.08")] == Decimal("100.00")


class TestTotals:
    def test_totals_follow_the_br_co_formulas(self):
        invoice = an_invoice(
            lines=[a_line("1", net="100.00"), a_line("2", net="50.00")],
            allowance_charges=[
                AllowanceCharge(
                    is_charge=False,
                    amount=Decimal("10.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                ),
                AllowanceCharge(
                    is_charge=True,
                    amount=Decimal("5.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                ),
            ],
            prepaid_amount=Decimal("20.00"),
        )
        t = invoice.totals

        assert t.sum_line_net == Decimal("150.00")  # BR-CO-10
        assert t.allowances_total == Decimal("10.00")  # BR-CO-11
        assert t.charges_total == Decimal("5.00")  # BR-CO-12
        assert t.total_without_vat == Decimal("145.00")  # BR-CO-13
        assert t.total_vat == money(Decimal("145.00") * STANDARD_RATE)  # BR-CO-14
        assert t.total_with_vat == t.total_without_vat + t.total_vat  # BR-CO-15
        assert t.amount_due == t.total_with_vat - Decimal("20.00")  # BR-CO-16

    def test_total_vat_is_the_sum_of_breakdown_entries(self):
        invoice = an_invoice(
            lines=[
                a_line("1", net="100.00", rate=STANDARD_RATE),
                a_line("2", net="100.00", rate=Decimal("0.08")),
            ]
        )
        assert invoice.totals.total_vat == sum(
            e.tax_amount for e in invoice.vat_breakdown
        )

    def test_amount_due_equals_total_when_nothing_is_prepaid(self):
        invoice = an_invoice()
        assert invoice.totals.amount_due == invoice.totals.total_with_vat


class TestDerivedFieldsCannotBeSupplied:
    @pytest.mark.parametrize("field", ["vat_breakdown", "totals"])
    def test_supplying_a_derived_field_is_rejected(self, field):
        with pytest.raises(ValidationError):
            an_invoice(**{field: []})


class TestExemptionReasonConsistency:
    def test_reason_propagates_from_the_lines_to_the_breakdown_entry(self):
        invoice = an_invoice(
            lines=[
                a_line(
                    "1",
                    net="100.00",
                    rate=Decimal("0"),
                    category=VatCategory.EXEMPT,
                    exemption_reason="Exempt under article 43",
                )
            ]
        )
        assert invoice.vat_breakdown[0].exemption_reason == "Exempt under article 43"

    def test_conflicting_reasons_within_one_group_are_rejected(self):
        # One breakdown entry can carry only one reason, so the lines feeding it
        # must agree. Picking one arbitrarily would silently discard the other.
        with pytest.raises(ValidationError, match="exemption reason"):
            an_invoice(
                lines=[
                    a_line(
                        "1",
                        net="100.00",
                        rate=Decimal("0"),
                        category=VatCategory.EXEMPT,
                        exemption_reason="Article 43",
                    ),
                    a_line(
                        "2",
                        net="100.00",
                        rate=Decimal("0"),
                        category=VatCategory.EXEMPT,
                        exemption_reason="Article 113",
                    ),
                ]
            )

    def test_matching_reasons_within_one_group_are_fine(self):
        invoice = an_invoice(
            lines=[
                a_line(
                    "1",
                    net="100.00",
                    rate=Decimal("0"),
                    category=VatCategory.EXEMPT,
                    exemption_reason="Article 43",
                ),
                a_line(
                    "2",
                    net="50.00",
                    rate=Decimal("0"),
                    category=VatCategory.EXEMPT,
                    exemption_reason="Article 43",
                ),
            ]
        )
        assert len(invoice.vat_breakdown) == 1
        assert invoice.vat_breakdown[0].taxable_amount == Decimal("150.00")


class TestCategoryRateConsistency:
    def test_standard_rate_must_be_above_zero(self):
        # BR-S-05
        with pytest.raises(ValidationError, match="greater than zero"):
            a_line(net="100.00", rate=Decimal("0"), category=VatCategory.STANDARD)

    @pytest.mark.parametrize(
        "category",
        [
            VatCategory.ZERO_RATED,
            VatCategory.EXEMPT,
            VatCategory.REVERSE_CHARGE,
            VatCategory.EXPORT,
            VatCategory.INTRA_COMMUNITY,
        ],
    )
    def test_non_standard_categories_must_have_a_zero_rate(self, category):
        # BR-Z-05 / BR-E-05 / BR-AE-05 / BR-G-05 / BR-IC-05
        with pytest.raises(ValidationError, match="zero"):
            a_line(net="100.00", rate=STANDARD_RATE, category=category)

    def test_a_rate_above_one_is_rejected_as_a_percentage_mistake(self):
        # Rates are fractions here: 0.23 is 23%. Passing 23 would otherwise
        # compute 2300% tax without complaint.
        with pytest.raises(ValidationError):
            a_line(net="100.00", rate=Decimal("23"))
