"""The contract between the model and the official validator.

The model is the first line of defence and Schematron the authority. The
contract between them is one sentence: anything the model accepts, the
serializer must render faithfully, and the official rules must not reject as
fatal. Each class below is a place where that contract was found broken.

Helpers are copied from test_model_derivation.py; if a third file needs them,
move them to conftest.py.
"""

from datetime import date
from decimal import Decimal

import pytest
from lxml import etree
from pydantic import ValidationError

from eu_einvoice_bridge.model import (
    Address,
    AllowanceCharge,
    Invoice,
    InvoiceTypeCode,
    LineItem,
    Party,
    VatCategory,
)
from eu_einvoice_bridge.ubl import to_ubl
from eu_einvoice_bridge.validate import validate_ubl
from eu_einvoice_bridge.validate.issues import Severity

CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

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
        fields["exemption_reason"] = "Exempt under article 43"
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


def fatal(issues):
    return [i for i in issues if i.severity is Severity.ERROR]


def describe(issues):
    """Readable assertion output: rule id and message, one per line."""
    return "\n".join(f"{i.rule_id or i.source}: {i.message}" for i in issues)


# --------------------------------------------------------------------------
# Bug 1: every failed assertion is reported as an error.
#
# Each assertion in the official Schematron carries flag="fatal" or
# flag="warning"; the vendored EN16931-UBL-validation.xslt has 281 of the
# former and 698 of the latter, the whole UBL-CR-* series included. Reading
# every failed-assert as an error makes a valid invoice exit 1 and makes
# `convert` refuse to write it.
# --------------------------------------------------------------------------


def _with_line_exemption_reason(xml: bytes) -> bytes:
    """Add BT-120 to the first line's ClassifiedTaxCategory.

    The serializer deliberately never does this, so the element is injected
    to reach a document that breaks exactly one warning-level rule:
    UBL-CR-601, whose official wording is "should not include".
    XSD order inside TaxCategory puts TaxExemptionReason just before
    TaxScheme.
    """
    root = etree.fromstring(xml)
    category = root.find(
        f".//{{{CAC}}}InvoiceLine/{{{CAC}}}Item/{{{CAC}}}ClassifiedTaxCategory"
    )
    reason = etree.Element(f"{{{CBC}}}TaxExemptionReason")
    reason.text = "Exempt under article 43"
    category.find(f"{{{CAC}}}TaxScheme").addprevious(reason)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


class TestSchematronSeverityFollowsTheOfficialFlag:
    def exempt_invoice(self):
        return an_invoice(
            lines=[
                a_line(net="100.00", rate=Decimal("0"), category=VatCategory.EXEMPT)
            ]
        )

    def test_precondition_the_base_invoice_is_clean(self):
        # If this fails, the tests below would be measuring something else.
        issues = validate_ubl(to_ubl(self.exempt_invoice()))
        assert issues == [], describe(issues)

    def test_a_warning_level_rule_is_reported_as_a_warning(self):
        xml = _with_line_exemption_reason(to_ubl(self.exempt_invoice()))
        issues = validate_ubl(xml)

        cr601 = [i for i in issues if i.rule_id == "UBL-CR-601"]
        assert cr601, "precondition: the injected element should trip UBL-CR-601"
        assert cr601[0].severity is Severity.WARNING

    def test_warnings_alone_do_not_make_the_invoice_invalid(self):
        xml = _with_line_exemption_reason(to_ubl(self.exempt_invoice()))
        issues = validate_ubl(xml)
        assert fatal(issues) == [], describe(fatal(issues))


# --------------------------------------------------------------------------
# Bug 2: the serializer reintroduces banker's rounding.
#
# money() rounds half-up on purpose, but _amount() renders with
# f"{value:.2f}", and Decimal's format uses the context default,
# ROUND_HALF_EVEN: 10.005 -> "10.00", 0.125 -> "0.12". Any amount the model
# has not already rounded goes through it.
# --------------------------------------------------------------------------


class TestUnitPriceKeepsItsPrecision:
    """BT-146 has no decimal limit, so nothing downstream would notice a loss."""

    def an_eighth_priced_invoice(self):
        return an_invoice(
            lines=[
                a_line(
                    net="1.00",
                    quantity=Decimal("8"),
                    unit_price=Decimal("0.125"),
                )
            ]
        )

    def test_price_amount_is_rendered_exactly(self):
        root = etree.fromstring(to_ubl(self.an_eighth_priced_invoice()))
        price = root.findtext(f".//{{{CAC}}}InvoiceLine/{{{CAC}}}Price/{{{CBC}}}PriceAmount")
        assert Decimal(price) == Decimal("0.125")

    def test_the_invoice_is_officially_valid(self):
        issues = validate_ubl(to_ubl(self.an_eighth_priced_invoice()))
        assert fatal(issues) == [], describe(fatal(issues))


class TestAmountsWithMoreThanTwoDecimalsAreRejectedByTheModel:
    """BR-DEC-23 (BT-131) and BR-DEC-01 (BT-92) allow two decimals, as fatal.

    Rejecting at the model beats rounding in either place: rounding in the
    model hides the input error, and rounding in the serializer is what
    produced the half-even mismatch against the half-up breakdown.
    """

    def test_line_net_amount(self):
        with pytest.raises(ValidationError) as exc:
            a_line(net_amount=Decimal("10.005"))
        assert exc.value.errors()[0]["loc"] == ("net_amount",)

    def test_allowance_amount(self):
        with pytest.raises(ValidationError) as exc:
            AllowanceCharge(
                is_charge=False,
                amount=Decimal("10.005"),
                vat_category=VatCategory.STANDARD,
                vat_rate=STANDARD_RATE,
            )
        assert exc.value.errors()[0]["loc"] == ("amount",)

    def test_trailing_zeros_are_not_extra_decimals(self):
        # 10.500 has three digits after the point but only two significant
        # ones. Whether to accept it is a choice; this pins it down as yes.
        line = a_line(net_amount=Decimal("10.500"), unit_price=Decimal("10.5"))
        assert line.net_amount == Decimal("10.5")


# --------------------------------------------------------------------------
# Bug 3: BT-6 is accepted but cannot be rendered validly.
#
# BR-53 (fatal): if the VAT accounting currency (BT-6) is present, the total
# VAT in that currency (BT-111) must be too. The model has no BT-111 and no
# exchange rate, so every invoice that sets BT-6 fails.
#
# The test states the contract rather than the fix: rejecting BT-6 until
# phase 2 and supporting BT-111 both make it pass.
# --------------------------------------------------------------------------


class TestVatAccountingCurrency:
    def test_is_either_rejected_or_fully_supported(self):
        try:
            invoice = an_invoice(currency="EUR", vat_accounting_currency="PLN")
        except ValidationError:
            return

        issues = validate_ubl(to_ubl(invoice))
        assert fatal(issues) == [], describe(fatal(issues))
