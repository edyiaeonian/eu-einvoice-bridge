"""An invoice in one currency whose VAT is reported in another.

The same requirement takes two unrelated shapes. EN16931 records only the
converted total (BT-111) and never the rate; FA(3) records the rate on each
line (KursWaluty) and the converted tax per rate slot (P_14_xW). The rate lives
in the neutral model and both are derived from it -- converted once per VAT
group, so the two documents cannot state different PLN tax.
"""

from decimal import Decimal

import pytest
from lxml import etree
from pydantic import ValidationError

from eu_einvoice_bridge.fa3 import fa3_issues, to_fa3
from eu_einvoice_bridge.model import money
from eu_einvoice_bridge.ubl import to_ubl
from eu_einvoice_bridge.validate import Severity, has_errors, validate_fa3, validate_ubl

from .test_fa3 import GENERATED_AT, a_line, an_invoice

RATE = Decimal("4.2567")
FA3 = {"f": "http://crd.gov.pl/wzor/2025/06/25/13775/"}
UBL = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def an_eur_invoice(**overrides):
    """100 EUR at 23% and 40 EUR at 8%, VAT reported in PLN."""
    return an_invoice(
        **{
            "currency": "EUR",
            "vat_accounting_currency": "PLN",
            "exchange_rate": RATE,
            "lines": [a_line("1", net="100.00", rate="0.23"), a_line("2", net="40.00", rate="0.08")],
            **overrides,
        }
    )


class TestTheModel:
    def test_accounting_currency_and_rate_are_accepted_together(self):
        invoice = an_eur_invoice()
        assert invoice.vat_accounting_currency == "PLN"
        assert invoice.exchange_rate == RATE

    def test_an_accounting_currency_without_a_rate_is_refused(self):
        # BT-111 could not be derived, and BR-53 demands it.
        with pytest.raises(ValidationError, match="exchange_rate"):
            an_eur_invoice(exchange_rate=None)

    def test_a_rate_without_an_accounting_currency_is_refused(self):
        with pytest.raises(ValidationError, match="vat_accounting_currency"):
            an_eur_invoice(vat_accounting_currency=None)

    def test_the_accounting_currency_must_differ_from_the_invoice_currency(self):
        with pytest.raises(ValidationError):
            an_eur_invoice(vat_accounting_currency="EUR")

    @pytest.mark.parametrize("rate", [Decimal("0"), Decimal("-4.2567")])
    def test_the_rate_must_be_positive(self, rate):
        with pytest.raises(ValidationError):
            an_eur_invoice(exchange_rate=rate)

    def test_a_float_rate_is_refused(self):
        with pytest.raises(ValidationError, match="float is not accepted"):
            an_eur_invoice(exchange_rate=4.2567)


class TestConversionIsPerGroupThenSummed:
    def test_each_groups_tax_is_converted_half_up(self):
        by_rate = {e.rate: e for e in an_eur_invoice().vat_breakdown}
        assert by_rate[Decimal("0.23")].tax_amount_in_accounting_currency == Decimal("97.90")
        assert by_rate[Decimal("0.08")].tax_amount_in_accounting_currency == Decimal("13.62")

    def test_bt_111_is_the_sum_of_the_converted_groups(self):
        assert an_eur_invoice().totals.total_vat_in_accounting_currency == Decimal("111.52")

    def test_which_differs_from_converting_the_total_and_that_is_the_point(self):
        # Converting the 26.20 total directly gives 111.53. Either could be
        # defended; what cannot is the UBL and the FA(3) disagreeing, and FA(3)
        # states PLN tax per rate. So BT-111 is built from the same figures.
        invoice = an_eur_invoice()
        assert money(invoice.totals.total_vat * RATE) == Decimal("111.53")
        assert invoice.totals.total_vat_in_accounting_currency == Decimal("111.52")

    def test_nothing_is_converted_for_a_single_currency_invoice(self):
        invoice = an_invoice()
        assert invoice.totals.total_vat_in_accounting_currency is None
        assert all(e.tax_amount_in_accounting_currency is None for e in invoice.vat_breakdown)


class TestUbl:
    def test_the_official_rules_accept_it(self):
        # BR-53 is fatal without BT-111; this used to be refused outright.
        assert validate_ubl(to_ubl(an_eur_invoice())) == []

    def test_bt_6_follows_the_document_currency(self):
        root = etree.fromstring(to_ubl(an_eur_invoice()))
        assert root.findtext("cbc:TaxCurrencyCode", namespaces=UBL) == "PLN"

    def test_bt_111_is_a_second_tax_total_without_subtotals(self):
        root = etree.fromstring(to_ubl(an_eur_invoice()))
        first, second = root.findall("cac:TaxTotal", UBL)
        assert first.find("cbc:TaxAmount", UBL).get("currencyID") == "EUR"
        amount = second.find("cbc:TaxAmount", UBL)
        assert (amount.text, amount.get("currencyID")) == ("111.52", "PLN")
        assert second.findall("cac:TaxSubtotal", UBL) == []

    def test_the_rate_itself_is_not_emitted_because_en16931_has_no_place_for_it(self):
        root = etree.fromstring(to_ubl(an_eur_invoice()))
        assert root.find("cac:TaxExchangeRate", UBL) is None


class TestFa3:
    def fa3(self, invoice):
        return etree.fromstring(to_fa3(invoice, generated_at=GENERATED_AT))

    def test_the_official_schema_accepts_it(self):
        assert validate_fa3(to_fa3(an_eur_invoice(), generated_at=GENERATED_AT)) == []

    def test_amounts_stay_in_the_invoice_currency(self):
        fa = self.fa3(an_eur_invoice()).find("f:Fa", FA3)
        assert fa.findtext("f:KodWaluty", namespaces=FA3) == "EUR"
        assert fa.findtext("f:P_14_1", namespaces=FA3) == "23.00"

    def test_tax_is_also_stated_in_pln_per_rate_slot(self):
        fa = self.fa3(an_eur_invoice()).find("f:Fa", FA3)
        assert fa.findtext("f:P_14_1W", namespaces=FA3) == "97.90"
        assert fa.findtext("f:P_14_2W", namespaces=FA3) == "13.62"

    def test_each_line_carries_the_rate(self):
        rates = [e.text for e in self.fa3(an_eur_invoice()).iterfind(".//f:FaWiersz/f:KursWaluty", FA3)]
        assert rates == ["4.2567", "4.2567"]

    def test_both_documents_state_the_same_pln_tax(self):
        invoice = an_eur_invoice()
        ubl = etree.fromstring(to_ubl(invoice))
        bt_111 = Decimal(ubl.findall("cac:TaxTotal", UBL)[1].findtext("cbc:TaxAmount", namespaces=UBL))
        fa = self.fa3(invoice).find("f:Fa", FA3)
        pln_per_slot = sum(Decimal(e.text) for e in fa if etree.QName(e).localname.endswith("W"))
        assert bt_111 == pln_per_slot == Decimal("111.52")

    def test_a_pln_invoice_carries_no_conversion(self):
        fa = self.fa3(an_invoice()).find("f:Fa", FA3)
        assert fa.find("f:P_14_1W", FA3) is None
        assert fa.find(".//f:KursWaluty", FA3) is None


class TestFa3Checks:
    def test_a_foreign_currency_invoice_without_a_pln_rate_is_blocked(self):
        # Art. 106e(11): VAT on a foreign-currency invoice is stated in PLN.
        invoice = an_invoice(currency="EUR")
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-EXCHANGE-RATE")
        assert issue.severity is Severity.ERROR

    def test_a_rate_to_some_other_currency_does_not_help(self):
        invoice = an_eur_invoice(vat_accounting_currency="CZK", exchange_rate=Decimal("24.95"))
        assert "FA3-EXCHANGE-RATE" in {i.rule_id for i in fa3_issues(invoice)}

    def test_a_pln_invoice_reporting_vat_in_another_currency_warns(self):
        # FA(3) already states tax in PLN; BT-111 in EUR has nowhere to go and
        # changes no PLN amount.
        invoice = an_invoice(vat_accounting_currency="EUR", exchange_rate=Decimal("0.2349"))
        issues = fa3_issues(invoice)
        assert not has_errors(issues)
        assert "FA3-DROP-BT111" in {i.rule_id for i in issues}
