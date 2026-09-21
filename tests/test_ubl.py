"""EN16931 UBL 2.1 serialization.

The decisive test is not that the output matches what this file expects, but
that the official Schematron accepts it. Structural assertions exist to say
*where* a failure is when that happens.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree
from saxonche import PySaxonProcessor

from eu_einvoice_bridge.model import (
    Address,
    AllowanceCharge,
    Invoice,
    InvoiceTypeCode,
    LineItem,
    Party,
    VatCategory,
)
from eu_einvoice_bridge.paths import EN16931_XSLT
from eu_einvoice_bridge.ubl import to_ubl

CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS = {"cbc": CBC, "cac": CAC}

STANDARD_RATE = Decimal("0.23")


def a_party(**overrides):
    return Party(
        **{
            "name": "Acme sp. z o.o.",
            "legal_registration_id": "0000123456",
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
        fields["exemption_reason"] = "Reverse charge"
    return LineItem(**{**fields, **overrides})


def an_invoice(**overrides):
    return Invoice(
        **{
            "number": "FV/2026/001",
            "issue_date": date(2026, 9, 21),
            "due_date": date(2026, 10, 21),
            "type_code": InvoiceTypeCode.COMMERCIAL_INVOICE,
            "currency": "PLN",
            "seller": a_party(),
            "buyer": a_party(name="Buyer sp. z o.o.", vat_id="PL1111111111"),
            "lines": [a_line()],
            **overrides,
        }
    )


def tree(invoice):
    return etree.fromstring(to_ubl(invoice))


def text(node, path):
    found = node.findall(path, NS)
    return [e.text for e in found]


@pytest.fixture(scope="module")
def schematron():
    with PySaxonProcessor(license=False) as proc:
        yield proc.new_xslt30_processor().compile_stylesheet(
            stylesheet_file=str(EN16931_XSLT)
        )


def failures(schematron, invoice, tmp_path):
    """Every failed assertion, warnings included.

    Deliberately stricter than validity, which only fatal rules decide: this
    serializer's own output should not trip even a warning-level rule.
    """
    path = tmp_path / "invoice.xml"
    path.write_bytes(to_ubl(invoice))
    svrl = etree.fromstring(
        schematron.transform_to_string(source_file=str(path)).encode()
    )
    svrl_ns = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
    return [
        (f.get("id"), (f.findtext("svrl:text", namespaces=svrl_ns) or "").strip())
        for f in svrl.findall(".//svrl:failed-assert", svrl_ns)
    ]


class TestOfficialSchematronAccepts:
    """The output has to satisfy the rules, not merely this file's expectations."""

    def test_a_standard_rated_invoice(self, schematron, tmp_path):
        assert failures(schematron, an_invoice(), tmp_path) == []

    def test_multiple_rates(self, schematron, tmp_path):
        invoice = an_invoice(
            lines=[
                a_line("1", net="100.00", rate=STANDARD_RATE),
                a_line("2", net="50.00", rate=Decimal("0.08")),
            ]
        )
        assert failures(schematron, invoice, tmp_path) == []

    def test_reverse_charge_with_an_exemption_reason(self, schematron, tmp_path):
        invoice = an_invoice(
            lines=[
                a_line(
                    "1",
                    net="100.00",
                    rate=Decimal("0"),
                    category=VatCategory.REVERSE_CHARGE,
                )
            ]
        )
        assert failures(schematron, invoice, tmp_path) == []

    def test_document_level_allowance_and_charge(self, schematron, tmp_path):
        invoice = an_invoice(
            allowance_charges=[
                AllowanceCharge(
                    is_charge=False,
                    amount=Decimal("10.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                    reason="Volume discount",
                ),
                AllowanceCharge(
                    is_charge=True,
                    amount=Decimal("5.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                    reason="Freight",
                ),
            ]
        )
        assert failures(schematron, invoice, tmp_path) == []

    def test_a_prepaid_invoice(self, schematron, tmp_path):
        invoice = an_invoice(prepaid_amount=Decimal("30.00"))
        assert failures(schematron, invoice, tmp_path) == []


class TestDocumentLevelMapping:
    def test_declares_the_en16931_customization(self):
        assert text(tree(an_invoice()), "cbc:CustomizationID") == [
            "urn:cen.eu:en16931:2017"
        ]

    def test_header_fields(self):
        t = tree(an_invoice())
        assert text(t, "cbc:ID") == ["FV/2026/001"]  # BT-1
        assert text(t, "cbc:IssueDate") == ["2026-09-21"]  # BT-2
        assert text(t, "cbc:InvoiceTypeCode") == ["380"]  # BT-3
        assert text(t, "cbc:DocumentCurrencyCode") == ["PLN"]  # BT-5
        assert text(t, "cbc:DueDate") == ["2026-10-21"]  # BT-9

    def test_due_date_is_omitted_when_absent(self):
        assert text(tree(an_invoice(due_date=None)), "cbc:DueDate") == []


class TestPartyMapping:
    def test_vat_id_and_legal_registration_id_go_to_different_elements(self):
        t = tree(an_invoice())
        party = t.find("cac:AccountingSupplierParty/cac:Party", NS)
        assert text(party, "cac:PartyTaxScheme/cbc:CompanyID") == [
            "PL5555555555"
        ]  # BT-31
        assert text(party, "cac:PartyLegalEntity/cbc:CompanyID") == [
            "0000123456"
        ]  # BT-30

    def test_a_party_without_a_vat_id_omits_the_tax_scheme(self):
        invoice = an_invoice(buyer=a_party(vat_id=None, legal_registration_id=None))
        party = tree(invoice).find("cac:AccountingCustomerParty/cac:Party", NS)
        assert party.findall("cac:PartyTaxScheme", NS) == []

    def test_address(self):
        party = tree(an_invoice()).find("cac:AccountingSupplierParty/cac:Party", NS)
        address = party.find("cac:PostalAddress", NS)
        assert text(address, "cbc:StreetName") == ["ul. Marszałkowska 1"]
        assert text(address, "cbc:CityName") == ["Warszawa"]
        assert text(address, "cbc:PostalZone") == ["00-001"]
        assert text(address, "cac:Country/cbc:IdentificationCode") == ["PL"]


class TestRateIsConvertedToPercentage:
    def test_fraction_becomes_percentage(self):
        # The model holds 0.23; BT-119 is 23. Getting this backwards is a
        # hundredfold error that still produces valid-looking XML.
        t = tree(an_invoice())
        assert text(t, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent") == [
            "23"
        ]

    def test_a_fractional_percentage_survives(self):
        invoice = an_invoice(lines=[a_line(rate=Decimal("0.075"))])
        assert text(
            tree(invoice), "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent"
        ) == ["7.5"]


class TestLineMapping:
    def test_item_name_is_bt_153(self):
        item = tree(an_invoice()).find("cac:InvoiceLine/cac:Item", NS)
        assert text(item, "cbc:Name") == ["Widget"]

    def test_description_is_omitted_when_absent(self):
        item = tree(an_invoice()).find("cac:InvoiceLine/cac:Item", NS)
        assert text(item, "cbc:Description") == []

    def test_description_precedes_name_as_ubl_requires(self):
        # Counterintuitive: the optional Description comes before the mandatory
        # Name in UBL's sequence, and the XSD enforces it.
        invoice = an_invoice(lines=[a_line(description="A useful widget")])
        item = tree(invoice).find("cac:InvoiceLine/cac:Item", NS)
        names = [etree.QName(c).localname for c in item]
        assert names.index("Description") < names.index("Name")

    def test_quantity_carries_its_unit_code(self):
        line = tree(an_invoice()).find("cac:InvoiceLine", NS)
        quantity = line.find("cbc:InvoicedQuantity", NS)
        assert quantity.get("unitCode") == "C62"  # BT-130

    def test_amounts_carry_the_currency(self):
        line = tree(an_invoice()).find("cac:InvoiceLine", NS)
        assert line.find("cbc:LineExtensionAmount", NS).get("currencyID") == "PLN"


class TestTotalsMapping:
    def test_legal_monetary_total_maps_each_bt(self):
        invoice = an_invoice(
            allowance_charges=[
                AllowanceCharge(
                    is_charge=False,
                    amount=Decimal("10.00"),
                    vat_category=VatCategory.STANDARD,
                    vat_rate=STANDARD_RATE,
                )
            ],
            prepaid_amount=Decimal("5.00"),
        )
        totals = tree(invoice).find("cac:LegalMonetaryTotal", NS)
        t = invoice.totals
        assert text(totals, "cbc:LineExtensionAmount") == [str(t.sum_line_net)]
        assert text(totals, "cbc:AllowanceTotalAmount") == [str(t.allowances_total)]
        assert text(totals, "cbc:TaxExclusiveAmount") == [str(t.total_without_vat)]
        assert text(totals, "cbc:TaxInclusiveAmount") == [str(t.total_with_vat)]
        assert text(totals, "cbc:PrepaidAmount") == [str(t.prepaid_amount)]
        assert text(totals, "cbc:PayableAmount") == [str(t.amount_due)]

    def test_one_tax_subtotal_per_breakdown_entry(self):
        invoice = an_invoice(
            lines=[
                a_line("1", net="100.00", rate=STANDARD_RATE),
                a_line("2", net="50.00", rate=Decimal("0.08")),
            ]
        )
        subtotals = tree(invoice).findall("cac:TaxTotal/cac:TaxSubtotal", NS)
        assert len(subtotals) == len(invoice.vat_breakdown) == 2

    def test_exemption_reason_reaches_the_tax_category(self):
        invoice = an_invoice(
            lines=[
                a_line(
                    "1",
                    net="100.00",
                    rate=Decimal("0"),
                    category=VatCategory.REVERSE_CHARGE,
                )
            ]
        )
        category = tree(invoice).find(
            "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory", NS
        )
        assert text(category, "cbc:TaxExemptionReason") == ["Reverse charge"]


GOLDEN = Path(__file__).parent / "golden" / "ubl_standard_invoice.xml"


def a_golden_invoice():
    """One invoice exercising mixed categories, an allowance and a prepayment."""
    address = Address(
        street="ul. Marszałkowska 1",
        city="Warszawa",
        postal_code="00-001",
        country="PL",
    )
    return Invoice(
        number="FV/2026/001",
        issue_date=date(2026, 9, 21),
        due_date=date(2026, 10, 21),
        type_code=InvoiceTypeCode.COMMERCIAL_INVOICE,
        currency="PLN",
        seller=Party(
            name="Acme sp. z o.o.",
            legal_registration_id="0000123456",
            vat_id="PL5555555555",
            vat_scheme="PL",
            address=address,
        ),
        buyer=Party(
            name="Buyer sp. z o.o.",
            vat_id="PL1111111111",
            vat_scheme="PL",
            address=address,
        ),
        lines=[
            LineItem(
                line_id="1",
                name="Widget",
                description="A useful widget",
                quantity=Decimal("2"),
                unit_code="C62",
                unit_price=Decimal("50.00"),
                net_amount=Decimal("100.00"),
                vat_category=VatCategory.STANDARD,
                vat_rate=STANDARD_RATE,
            ),
            LineItem(
                line_id="2",
                name="Manual",
                quantity=Decimal("1"),
                unit_code="C62",
                unit_price=Decimal("40.00"),
                net_amount=Decimal("40.00"),
                vat_category=VatCategory.ZERO_RATED,
                vat_rate=Decimal("0"),
            ),
            LineItem(
                line_id="3",
                name="Consulting",
                quantity=Decimal("1"),
                unit_code="HUR",
                unit_price=Decimal("200.00"),
                net_amount=Decimal("200.00"),
                vat_category=VatCategory.REVERSE_CHARGE,
                vat_rate=Decimal("0"),
                exemption_reason="Reverse charge",
            ),
        ],
        allowance_charges=[
            AllowanceCharge(
                is_charge=False,
                amount=Decimal("10.00"),
                vat_category=VatCategory.STANDARD,
                vat_rate=STANDARD_RATE,
                reason="Volume discount",
            )
        ],
        prepaid_amount=Decimal("50.00"),
    )


class TestGoldenFile:
    """Detects unintended changes to the output.

    Regenerate deliberately with:
        python -m tests.regenerate_golden
    """

    def test_output_matches_the_recorded_baseline(self):
        assert to_ubl(a_golden_invoice()) == GOLDEN.read_bytes()

    def test_the_baseline_itself_is_conformant(self, schematron, tmp_path):
        assert failures(schematron, a_golden_invoice(), tmp_path) == []

    def test_the_baseline_covers_what_it_is_meant_to(self):
        invoice = a_golden_invoice()
        assert {e.category for e in invoice.vat_breakdown} == {
            VatCategory.STANDARD,
            VatCategory.ZERO_RATED,
            VatCategory.REVERSE_CHARGE,
        }
        assert invoice.allowance_charges
        assert invoice.prepaid_amount


class TestAllowanceChargeMapping:
    def test_charge_indicator_distinguishes_the_two(self):
        invoice = an_invoice(
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
            ]
        )
        indicators = text(
            tree(invoice), "cac:AllowanceCharge/cbc:ChargeIndicator"
        )
        assert indicators == ["false", "true"]
