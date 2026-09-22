"""The FA(3) serializer.

As with UBL, the decisive check is that the official schema accepts the
output; field assertions exist to say where a failure is. Every VAT category
and every kind of buyer identification has at least one invoice run through
the schema.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from eu_einvoice_bridge.fa3 import Fa3MappingError, to_fa3
from eu_einvoice_bridge.model import (
    Address,
    AllowanceCharge,
    ExemptionBasis,
    Invoice,
    InvoiceTypeCode,
    LineItem,
    Party,
    PolishExtras,
    VatCategory,
)
from eu_einvoice_bridge.validate import validate_fa3

NS = {"f": "http://crd.gov.pl/wzor/2025/06/25/13775/"}
GENERATED_AT = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

DECLARATIONS = {
    "buyer_local_government_unit": False,
    "buyer_vat_group_member": False,
    "cash_accounting": False,
    "self_billing": False,
    "split_payment": False,
    "simplified_triangular_procedure": False,
    "margin_scheme": False,
    "intra_eu_new_means_of_transport": False,
}

REASONS = {
    VatCategory.EXEMPT: "Art. 43 ust. 1 pkt 37 ustawy o VAT",
    VatCategory.REVERSE_CHARGE: "Odwrotne obciążenie",
    VatCategory.INTRA_COMMUNITY: "Wewnątrzwspólnotowa dostawa towarów",
    VatCategory.EXPORT: "Eksport towarów",
}


def a_party(name="Acme sp. z o.o.", vat_id="PL5555555555", country="PL"):
    return Party(
        name=name,
        vat_id=vat_id,
        vat_scheme=country,
        address=Address(
            street="ul. Marszałkowska 1", city="Warszawa", postal_code="00-001", country=country
        ),
    )


def a_line(line_id="1", net="100.00", rate="0.23", category=VatCategory.STANDARD, **kw):
    fields = {
        "line_id": line_id,
        "name": "Widget",
        "quantity": Decimal("1"),
        "unit_code": "C62",
        "unit_price": Decimal(net),
        "net_amount": Decimal(net),
        "vat_category": category,
        "vat_rate": Decimal(rate),
    }
    if category.requires_exemption_reason:
        fields["exemption_reason"] = REASONS[category]
    return LineItem(**{**fields, **kw})


def an_invoice(lines=None, extras=None, **overrides):
    return Invoice(
        **{
            "number": "FV/2026/001",
            "issue_date": date(2026, 9, 21),
            "type_code": InvoiceTypeCode.COMMERCIAL_INVOICE,
            "currency": "PLN",
            "seller": a_party(),
            "buyer": a_party(name="Buyer sp. z o.o.", vat_id="PL1111111111"),
            "lines": lines if lines is not None else [a_line()],
            "extras": extras if extras is not None else PolishExtras(**DECLARATIONS),
            **overrides,
        }
    )


def fa3(invoice) -> bytes:
    return to_fa3(invoice, generated_at=GENERATED_AT)


def tree(invoice):
    return etree.fromstring(fa3(invoice))


def text(node, path):
    return [e.text for e in node.findall(path, NS)]


def zero(category, **kw):
    return a_line(rate="0", category=category, **kw)


class TestOfficialSchemaAccepts:
    """The output has to satisfy the official schema, not this file's hopes."""

    @pytest.mark.parametrize(
        "lines",
        [
            pytest.param([a_line()], id="standard-23"),
            pytest.param(
                [
                    a_line("1", rate="0.23"),
                    a_line("2", rate="0.08"),
                    a_line("3", rate="0.05"),
                    zero(VatCategory.ZERO_RATED, line_id="4"),
                ],
                id="mixed-rates",
            ),
            pytest.param([zero(VatCategory.REVERSE_CHARGE)], id="reverse-charge"),
        ],
    )
    def test_domestic_invoices(self, lines):
        assert validate_fa3(fa3(an_invoice(lines=lines))) == []

    def test_an_exempt_invoice(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.EXEMPT)],
            extras=PolishExtras(**DECLARATIONS, exemption_basis=ExemptionBasis.POLISH_ACT),
        )
        assert validate_fa3(fa3(invoice)) == []

    def test_an_intra_community_supply_to_an_eu_buyer(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.INTRA_COMMUNITY)],
            buyer=a_party(name="Käufer GmbH", vat_id="DE123456789", country="DE"),
        )
        assert validate_fa3(fa3(invoice)) == []

    def test_an_export_to_a_buyer_without_a_tax_id(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.EXPORT)],
            buyer=a_party(name="Buyer Inc.", vat_id=None, country="US"),
        )
        assert validate_fa3(fa3(invoice)) == []

    def test_a_buyer_with_a_non_eu_tax_id(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.EXPORT)],
            buyer=a_party(name="Buyer Ltd", vat_id="NO123456789MVA", country="NO"),
        )
        assert validate_fa3(fa3(invoice)) == []

    def test_description_due_date_and_prepayment(self):
        invoice = an_invoice(
            lines=[a_line(description="A useful widget")],
            due_date=date(2026, 10, 21),
            prepaid_amount=Decimal("50.00"),
        )
        assert validate_fa3(fa3(invoice)) == []


class TestHeader:
    def test_form_code_and_variant(self):
        root = tree(an_invoice())
        code = root.find("f:Naglowek/f:KodFormularza", NS)
        assert code.text == "FA"
        assert code.get("kodSystemowy") == "FA (3)"
        assert code.get("wersjaSchemy") == "1-0E"
        assert text(root, "f:Naglowek/f:WariantFormularza") == ["3"]

    def test_generation_time_is_supplied_not_read_from_a_clock(self):
        # Reading the clock inside the serializer would make every output
        # unique and golden files impossible.
        assert text(tree(an_invoice()), "f:Naglowek/f:DataWytworzeniaFa") == [
            "2026-09-21T10:00:00Z"
        ]

    def test_generation_time_is_normalized_to_utc(self):
        warsaw = timezone(timedelta(hours=2))
        xml = to_fa3(an_invoice(), generated_at=datetime(2026, 9, 21, 12, 0, tzinfo=warsaw))
        assert text(etree.fromstring(xml), "f:Naglowek/f:DataWytworzeniaFa") == [
            "2026-09-21T10:00:00Z"
        ]

    def test_a_naive_generation_time_is_refused(self):
        with pytest.raises(ValueError, match="timezone"):
            to_fa3(an_invoice(), generated_at=datetime(2026, 9, 21, 10, 0))

    def test_output_is_deterministic(self):
        assert fa3(an_invoice()) == fa3(an_invoice())


class TestParties:
    def test_the_seller_nip_is_the_vat_id_without_its_prefix(self):
        root = tree(an_invoice())
        assert text(root, "f:Podmiot1/f:DaneIdentyfikacyjne/f:NIP") == ["5555555555"]

    def test_a_structured_address_becomes_two_free_text_lines(self):
        address = tree(an_invoice()).find("f:Podmiot1/f:Adres", NS)
        assert text(address, "f:KodKraju") == ["PL"]
        assert text(address, "f:AdresL1") == ["ul. Marszałkowska 1"]
        assert text(address, "f:AdresL2") == ["00-001 Warszawa"]

    def test_a_polish_buyer_is_identified_by_nip(self):
        ids = tree(an_invoice()).find("f:Podmiot2/f:DaneIdentyfikacyjne", NS)
        assert text(ids, "f:NIP") == ["1111111111"]

    def test_an_eu_buyer_is_split_into_prefix_and_number(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.INTRA_COMMUNITY)],
            buyer=a_party(vat_id="DE123456789", country="DE"),
        )
        ids = tree(invoice).find("f:Podmiot2/f:DaneIdentyfikacyjne", NS)
        assert text(ids, "f:KodUE") == ["DE"]
        assert text(ids, "f:NrVatUE") == ["123456789"]

    def test_the_eu_code_comes_from_the_vat_prefix_not_the_country(self):
        # Greece's VAT prefix is EL while its ISO code is GR; FA(3) lists EL.
        invoice = an_invoice(
            lines=[zero(VatCategory.INTRA_COMMUNITY)],
            buyer=a_party(vat_id="EL123456789", country="GR"),
        )
        ids = tree(invoice).find("f:Podmiot2/f:DaneIdentyfikacyjne", NS)
        assert text(ids, "f:KodUE") == ["EL"]

    def test_a_buyer_without_a_tax_id(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.EXPORT)], buyer=a_party(vat_id=None, country="US")
        )
        ids = tree(invoice).find("f:Podmiot2/f:DaneIdentyfikacyjne", NS)
        assert text(ids, "f:BrakID") == ["1"]

    def test_jst_and_gv_come_from_the_declarations(self):
        extras = PolishExtras(**{**DECLARATIONS, "buyer_vat_group_member": True})
        buyer = tree(an_invoice(extras=extras)).find("f:Podmiot2", NS)
        assert text(buyer, "f:JST") == ["2"]
        assert text(buyer, "f:GV") == ["1"]


class TestRates:
    """EN16931's category plus any rate becomes one of a closed set of codes."""

    @pytest.mark.parametrize(
        ("category", "rate", "code"),
        [
            (VatCategory.STANDARD, "0.23", "23"),
            (VatCategory.STANDARD, "0.22", "22"),
            (VatCategory.STANDARD, "0.08", "8"),
            (VatCategory.STANDARD, "0.07", "7"),
            (VatCategory.STANDARD, "0.05", "5"),
            (VatCategory.ZERO_RATED, "0", "0 KR"),
            (VatCategory.INTRA_COMMUNITY, "0", "0 WDT"),
            (VatCategory.EXPORT, "0", "0 EX"),
            (VatCategory.EXEMPT, "0", "zw"),
            (VatCategory.REVERSE_CHARGE, "0", "oo"),
        ],
    )
    def test_line_rate_code(self, category, rate, code):
        extras = PolishExtras(**DECLARATIONS, exemption_basis=ExemptionBasis.POLISH_ACT)
        # Each code with the buyer it belongs with; see FA3-*-BUYER in checks.py.
        buyer = {
            VatCategory.INTRA_COMMUNITY: a_party(vat_id="DE123456789", country="DE"),
            VatCategory.EXPORT: a_party(name="Buyer Inc.", vat_id=None, country="US"),
        }.get(category, a_party(name="Buyer sp. z o.o.", vat_id="PL1111111111"))
        invoice = an_invoice(
            lines=[a_line(rate=rate, category=category)], extras=extras, buyer=buyer
        )
        assert text(tree(invoice), "f:Fa/f:FaWiersz/f:P_12") == [code]

    def test_taxed_rates_fill_both_the_net_and_the_tax_slot(self):
        fa = tree(an_invoice()).find("f:Fa", NS)
        assert text(fa, "f:P_13_1") == ["100.00"]
        assert text(fa, "f:P_14_1") == ["23.00"]

    def test_untaxed_categories_fill_only_the_net_slot(self):
        fa = tree(an_invoice(lines=[zero(VatCategory.REVERSE_CHARGE)])).find("f:Fa", NS)
        assert text(fa, "f:P_13_10") == ["100.00"]
        assert fa.findall("f:P_14_10", NS) == []

    def test_23_and_22_share_one_slot_and_are_summed(self):
        # FA(3) documents P_13_1 as "currently 23% or 22%".
        invoice = an_invoice(lines=[a_line("1", rate="0.23"), a_line("2", rate="0.22")])
        fa = tree(invoice).find("f:Fa", NS)
        assert text(fa, "f:P_13_1") == ["200.00"]
        assert text(fa, "f:P_14_1") == ["45.00"]

    def test_the_total_is_the_gross_amount(self):
        invoice = an_invoice()
        fa = tree(invoice).find("f:Fa", NS)
        assert text(fa, "f:P_15") == [f"{invoice.totals.total_with_vat:f}"]


class TestAnnotations:
    def test_declarations_are_rendered_as_one_or_two(self):
        extras = PolishExtras(**{**DECLARATIONS, "split_payment": True})
        notes = tree(an_invoice(extras=extras)).find("f:Fa/f:Adnotacje", NS)
        assert text(notes, "f:P_16") == ["2"]
        assert text(notes, "f:P_18A") == ["1"]

    def test_reverse_charge_is_derived_from_the_lines(self):
        plain = tree(an_invoice()).find("f:Fa/f:Adnotacje", NS)
        reverse = tree(an_invoice(lines=[zero(VatCategory.REVERSE_CHARGE)])).find(
            "f:Fa/f:Adnotacje", NS
        )
        assert text(plain, "f:P_18") == ["2"]
        assert text(reverse, "f:P_18") == ["1"]

    def test_no_exempt_lines_means_the_negative_marker(self):
        notes = tree(an_invoice()).find("f:Fa/f:Adnotacje", NS)
        assert text(notes, "f:Zwolnienie/f:P_19N") == ["1"]

    @pytest.mark.parametrize(
        ("basis", "element"),
        [
            (ExemptionBasis.POLISH_ACT, "P_19A"),
            (ExemptionBasis.EU_DIRECTIVE, "P_19B"),
            (ExemptionBasis.OTHER, "P_19C"),
        ],
    )
    def test_exemption_basis_selects_the_element_carrying_the_reason(self, basis, element):
        extras = PolishExtras(**DECLARATIONS, exemption_basis=basis)
        notes = tree(an_invoice(lines=[zero(VatCategory.EXEMPT)], extras=extras)).find(
            "f:Fa/f:Adnotacje", NS
        )
        assert text(notes, "f:Zwolnienie/f:P_19") == ["1"]
        assert text(notes, f"f:Zwolnienie/f:{element}") == [REASONS[VatCategory.EXEMPT]]

    def test_invoice_kind(self):
        assert text(tree(an_invoice()), "f:Fa/f:RodzajFaktury") == ["VAT"]


class TestLines:
    def test_line_fields(self):
        line = tree(an_invoice()).find("f:Fa/f:FaWiersz", NS)
        assert text(line, "f:P_7") == ["Widget"]
        assert text(line, "f:P_8A") == ["C62"]
        assert text(line, "f:P_8B") == ["1"]
        assert text(line, "f:P_9A") == ["100.00"]
        assert text(line, "f:P_11") == ["100.00"]

    def test_line_numbers_are_positions_because_fa3_needs_integers(self):
        # BT-126 may be "A1"; NrWierszaFa must be a positive integer.
        invoice = an_invoice(lines=[a_line("A1"), a_line("B7")])
        assert text(tree(invoice), "f:Fa/f:FaWiersz/f:NrWierszaFa") == ["1", "2"]

    def test_a_unit_price_keeps_its_precision(self):
        invoice = an_invoice(
            lines=[
                a_line(quantity=Decimal("8"), unit_price=Decimal("0.125"), net_amount=Decimal("1.00"))
            ]
        )
        assert text(tree(invoice), "f:Fa/f:FaWiersz/f:P_9A") == ["0.125"]

    def test_a_description_is_carried_as_additional_data_on_its_line(self):
        invoice = an_invoice(lines=[a_line("1"), a_line("2", description="A useful widget")])
        extra = tree(invoice).find("f:Fa/f:DodatkowyOpis", NS)
        assert text(extra, "f:NrWiersza") == ["2"]
        assert text(extra, "f:Wartosc") == ["A useful widget"]


class TestPaymentAndSettlement:
    def test_due_date(self):
        root = tree(an_invoice(due_date=date(2026, 10, 21)))
        assert text(root, "f:Fa/f:Platnosc/f:TerminPlatnosci/f:Termin") == ["2026-10-21"]

    def test_prepayment_is_deducted_from_the_payable_amount_not_the_tax_base(self):
        invoice = an_invoice(prepaid_amount=Decimal("50.00"))
        fa = tree(invoice).find("f:Fa", NS)
        assert text(fa, "f:Rozliczenie/f:Odliczenia/f:Kwota") == ["50.00"]
        assert text(fa, "f:Rozliczenie/f:DoZaplaty") == [f"{invoice.totals.amount_due:f}"]
        # The tax base is untouched: P_13_1 is still the full net amount.
        assert text(fa, "f:P_13_1") == ["100.00"]

    def test_nothing_prepaid_means_no_settlement_block(self):
        assert tree(an_invoice()).findall("f:Fa/f:Rozliczenie", NS) == []


class TestRefusals:
    """Inputs FA(3) cannot express. Step 4 turns these into collected issues."""

    def test_without_polish_declarations(self):
        with pytest.raises(Fa3MappingError):
            fa3(an_invoice().model_copy(update={"extras": None}))

    def test_a_standard_rate_poland_does_not_have(self):
        with pytest.raises(Fa3MappingError):
            fa3(an_invoice(lines=[a_line(rate="0.19")]))

    def test_a_document_level_allowance(self):
        allowance = AllowanceCharge(
            is_charge=False,
            amount=Decimal("10.00"),
            vat_category=VatCategory.STANDARD,
            vat_rate=Decimal("0.23"),
        )
        with pytest.raises(Fa3MappingError):
            fa3(an_invoice(allowance_charges=[allowance]))


GOLDEN_FA3 = Path(__file__).parent / "golden" / "fa3_invoice.xml"


def a_golden_fa3_invoice():
    """One invoice covering taxed, exempt and reverse-charge lines, a line
    description, a due date and a prepayment."""
    return an_invoice(
        lines=[
            a_line("1", net="100.00", rate="0.23", description="A useful widget"),
            a_line("2", net="40.00", rate="0.08"),
            zero(VatCategory.EXEMPT, line_id="3", net="60.00"),
            zero(VatCategory.REVERSE_CHARGE, line_id="4", net="200.00"),
        ],
        extras=PolishExtras(**DECLARATIONS, exemption_basis=ExemptionBasis.POLISH_ACT),
        due_date=date(2026, 10, 21),
        prepaid_amount=Decimal("50.00"),
    )


class TestGoldenFile:
    """Detects unintended changes to the output.

    Regenerate deliberately with:
        python -m tests.regenerate_golden
    """

    def test_output_matches_the_recorded_baseline(self):
        assert fa3(a_golden_fa3_invoice()) == GOLDEN_FA3.read_bytes()

    def test_the_baseline_itself_passes_the_official_schema(self):
        assert validate_fa3(GOLDEN_FA3.read_bytes()) == []

    def test_the_baseline_covers_what_it_is_meant_to(self):
        root = etree.fromstring(GOLDEN_FA3.read_bytes())
        codes = set(text(root, "f:Fa/f:FaWiersz/f:P_12"))
        assert codes == {"23", "8", "zw", "oo"}
        assert root.findall("f:Fa/f:DodatkowyOpis", NS)
        assert root.findall("f:Fa/f:Rozliczenie", NS)
        assert root.findall("f:Fa/f:Platnosc", NS)
