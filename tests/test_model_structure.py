"""Structural rules of the neutral model.

The exemption-reason constraints are enforced here rather than left to
Schematron so that a meaningless combination cannot be constructed at all. The
rule IDs they mirror are cited per case; Schematron remains authoritative.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from eu_einvoice_bridge.model import (
    Address,
    AllowanceCharge,
    InvoiceTypeCode,
    LineItem,
    Party,
    VatCategory,
)


def an_address(**overrides):
    return Address(
        **{
            "street": "ul. Marszałkowska 1",
            "city": "Warszawa",
            "postal_code": "00-001",
            "country": "PL",
            **overrides,
        }
    )


def a_line(**overrides):
    return LineItem(
        **{
            "line_id": "1",
            "name": "Widget",
            "quantity": Decimal("2"),
            "unit_code": "C62",
            "unit_price": Decimal("10.00"),
            "net_amount": Decimal("20.00"),
            "vat_category": VatCategory.STANDARD,
            "vat_rate": Decimal("0.23"),
            **overrides,
        }
    )


class TestVatCategory:
    def test_supported_codes_match_untdid_5305(self):
        # O is excluded deliberately: BR-O-05 wants the rate absent rather than
        # zero, and BR-O-11..14 forbid mixing it with other categories.
        assert {c.value for c in VatCategory} == {"S", "Z", "E", "AE", "K", "G"}

    @pytest.mark.parametrize(
        "category",
        [
            VatCategory.EXEMPT,
            VatCategory.REVERSE_CHARGE,
            VatCategory.EXPORT,
            VatCategory.INTRA_COMMUNITY,
        ],
    )
    def test_categories_that_require_an_exemption_reason(self, category):
        assert category.requires_exemption_reason

    @pytest.mark.parametrize("category", [VatCategory.STANDARD, VatCategory.ZERO_RATED])
    def test_categories_that_forbid_an_exemption_reason(self, category):
        assert not category.requires_exemption_reason


class TestAddress:
    def test_accepts_a_complete_address(self):
        assert an_address().country == "PL"

    def test_rejects_a_missing_city(self):
        with pytest.raises(ValidationError):
            an_address(city=None)

    def test_rejects_a_country_that_is_not_two_letters(self):
        with pytest.raises(ValidationError):
            an_address(country="Poland")


class TestParty:
    def test_vat_id_and_legal_registration_id_are_distinct_fields(self):
        # BT-30 and BT-31 are different identifiers and must not share a field.
        party = Party(
            name="Acme sp. z o.o.",
            legal_registration_id="0000123456",
            vat_id="PL5555555555",
            vat_scheme="PL",
            address=an_address(),
        )
        assert party.legal_registration_id != party.vat_id

    def test_allows_a_party_with_neither_identifier(self):
        # Buyers are not always VAT registered; the model should not force one.
        party = Party(name="Jan Kowalski", vat_scheme="PL", address=an_address())
        assert party.vat_id is None


class TestLineItemExemptionReason:
    @pytest.mark.parametrize(
        "category",
        [
            VatCategory.EXEMPT,
            VatCategory.REVERSE_CHARGE,
            VatCategory.EXPORT,
            VatCategory.INTRA_COMMUNITY,
        ],
    )
    def test_rejected_when_a_required_reason_is_missing(self, category):
        # BR-E-10 / BR-AE-10 / BR-G-10 / BR-IC-10
        with pytest.raises(ValidationError, match="exemption_reason: .* requires one"):
            a_line(vat_category=category, vat_rate=Decimal("0"))

    def test_reason_text_alone_is_enough(self):
        line = a_line(
            vat_category=VatCategory.EXEMPT,
            vat_rate=Decimal("0"),
            exemption_reason="Exempt under article 43",
        )
        assert line.exemption_reason_code is None

    def test_reason_code_alone_is_enough(self):
        line = a_line(
            vat_category=VatCategory.EXEMPT,
            vat_rate=Decimal("0"),
            exemption_reason_code="VATEX-EU-132",
        )
        assert line.exemption_reason is None

    @pytest.mark.parametrize(
        ("category", "rate"),
        [
            # A valid rate for each category, so the failure can only come from
            # the reason itself and not from the category/rate rule.
            (VatCategory.STANDARD, Decimal("0.23")),
            (VatCategory.ZERO_RATED, Decimal("0")),
        ],
    )
    def test_rejected_when_a_forbidden_reason_is_present(self, category, rate):
        # BR-S-10 / BR-Z-10 — the constraint runs in both directions.
        with pytest.raises(ValidationError, match="exemption_reason: .* must not carry one"):
            a_line(
                vat_category=category,
                vat_rate=rate,
                exemption_reason="should not be here",
            )

    def test_zero_rated_is_distinct_from_exempt_despite_both_being_zero(self):
        zero = a_line(vat_category=VatCategory.ZERO_RATED, vat_rate=Decimal("0"))
        exempt = a_line(
            vat_category=VatCategory.EXEMPT,
            vat_rate=Decimal("0"),
            exemption_reason="Exempt under article 43",
        )
        assert zero.vat_rate == exempt.vat_rate
        assert zero.vat_category != exempt.vat_category


class TestLineItem:
    def test_rejects_a_missing_name(self):
        # BT-153 is mandatory; BT-154 description is not.
        with pytest.raises(ValidationError):
            a_line(name=None)

    def test_description_is_optional(self):
        assert a_line().description is None

    def test_rejects_a_negative_quantity(self):
        with pytest.raises(ValidationError):
            a_line(quantity=Decimal("-1"))

class TestFloatIsRefusedAtTheBoundary:
    """Pydantic would coerce float to Decimal and look correct.

    That is exactly the danger: the precision was lost upstream, before the
    value arrived, and coercion would make it permanent and silent.
    """

    @pytest.mark.parametrize(
        "field", ["quantity", "unit_price", "net_amount", "vat_rate"]
    )
    def test_float_is_rejected_on_every_numeric_line_field(self, field):
        with pytest.raises(ValidationError, match="float is not accepted"):
            a_line(**{field: 0.1})

    def test_float_is_rejected_on_an_allowance(self):
        with pytest.raises(ValidationError, match="float is not accepted"):
            AllowanceCharge(
                is_charge=False,
                amount=19.99,
                vat_category=VatCategory.STANDARD,
                vat_rate=Decimal("0.23"),
            )

    def test_the_error_it_prevents(self):
        # Without the guard this would be accepted and carried forward.
        contaminated = 0.1 + 0.2
        assert contaminated != 0.3
        with pytest.raises(ValidationError, match="float is not accepted"):
            a_line(net_amount=contaminated)

    def test_decimal_and_str_remain_acceptable(self):
        line = a_line(unit_price=Decimal("0.1"), net_amount=Decimal("0.20"))
        assert line.unit_price == Decimal("0.1")
        assert a_line(unit_price="0.1", net_amount="0.20").unit_price == Decimal("0.1")


class TestAllowanceCharge:
    def test_distinguishes_an_allowance_from_a_charge(self):
        allowance = AllowanceCharge(
            is_charge=False,
            amount=Decimal("5.00"),
            vat_category=VatCategory.STANDARD,
            vat_rate=Decimal("0.23"),
            reason="Volume discount",
        )
        charge = AllowanceCharge(
            is_charge=True,
            amount=Decimal("5.00"),
            vat_category=VatCategory.STANDARD,
            vat_rate=Decimal("0.23"),
        )
        assert allowance.is_charge is False
        assert charge.is_charge is True

    def test_rejects_a_negative_amount(self):
        # Direction is carried by is_charge, never by the sign of the amount.
        with pytest.raises(ValidationError):
            AllowanceCharge(
                is_charge=False,
                amount=Decimal("-5.00"),
                vat_category=VatCategory.STANDARD,
                vat_rate=Decimal("0.23"),
            )


class TestInvoiceTypeCode:
    def test_only_the_commercial_invoice_is_supported(self):
        assert [c.value for c in InvoiceTypeCode] == ["380"]

    def test_a_credit_note_code_is_refused(self):
        # 381 belongs to UBL's CreditNote document; inside an Invoice it fails
        # BR-CL-01, so accepting it would guarantee an invalid output.
        with pytest.raises(ValueError):
            InvoiceTypeCode("381")


class TestLineNetAmountMatchesQuantityTimesPrice:
    """EN16931 core does not relate BT-131 to BT-129 x BT-146; Peppol does.

    Peppol BIS Billing 3.0's PEPPOL-EN16931-R120 (fatal) checks the line net
    amount against the unrounded quantity x price, with a slack of 0.02 either
    side, inclusive. The same tolerance is used here: the check exists to catch
    a wrong figure, and a one-cent gap is a rounding convention, not a mistake.
    """

    def test_a_wrong_figure_is_rejected(self):
        with pytest.raises(ValidationError, match="net_amount"):
            a_line(
                quantity=Decimal("2"),
                unit_price=Decimal("10.00"),
                net_amount=Decimal("25.00"),
            )

    @pytest.mark.parametrize("net", ["0.13", "0.12"])
    def test_either_rounding_convention_is_accepted(self, net):
        # 1 x 0.125 is 0.13 half-up and 0.12 half-even. R120 accepts both, and
        # refusing the half-even answer would be stricter than the rule that
        # actually exists.
        line = a_line(
            quantity=Decimal("1"),
            unit_price=Decimal("0.125"),
            net_amount=Decimal(net),
        )
        assert line.net_amount == Decimal(net)

    def test_a_difference_of_exactly_the_slack_is_accepted(self):
        # R120's slack is inclusive: exp - 0.02 <= val <= exp + 0.02.
        line = a_line(
            quantity=Decimal("1"),
            unit_price=Decimal("10.00"),
            net_amount=Decimal("10.02"),
        )
        assert line.net_amount == Decimal("10.02")

    def test_a_difference_beyond_the_slack_is_rejected(self):
        with pytest.raises(ValidationError, match="net_amount"):
            a_line(
                quantity=Decimal("1"),
                unit_price=Decimal("10.00"),
                net_amount=Decimal("10.03"),
            )


class TestHugeNumbersFailValidationRatherThanCrash:
    """quantize() raises decimal.InvalidOperation past 28 digits of precision.

    That is an ArithmeticError, not a ValueError, so Pydantic does not turn it
    into a ValidationError: it escaped as a traceback. Bounding the digits at
    the boundary stops it before any arithmetic runs.
    """

    @pytest.mark.parametrize("field", ["quantity", "unit_price", "net_amount"])
    @pytest.mark.parametrize("value", ["1E+30", "1E+999999"])
    def test_line_fields(self, field, value):
        with pytest.raises(ValidationError) as exc:
            a_line(**{field: Decimal(value)})
        assert exc.value.errors()[0]["loc"] == (field,)

    def test_allowance_amount(self):
        with pytest.raises(ValidationError):
            AllowanceCharge(
                is_charge=False,
                amount=Decimal("1E+30"),
                vat_category=VatCategory.STANDARD,
                vat_rate=Decimal("0.23"),
            )
