"""Polish statutory declarations that FA(3) requires and EN16931 has no place for.

Every declaration is mandatory once the block is given, with no defaults: a
default of "no" can produce an invoice that passes every validator and is still
legally wrong -- split payment, for one, is compulsory above a threshold for
certain goods, and nothing downstream would notice it missing.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from eu_einvoice_bridge.model import (
    Address,
    ExemptionBasis,
    Invoice,
    InvoiceTypeCode,
    LineItem,
    Party,
    PolishExtras,
    VatCategory,
)

DECLARATIONS = {
    "buyer_local_government_unit": False,  # Podmiot2/JST
    "buyer_vat_group_member": False,  # Podmiot2/GV
    "cash_accounting": False,  # Adnotacje/P_16
    "self_billing": False,  # Adnotacje/P_17
    "split_payment": False,  # Adnotacje/P_18A
    "simplified_triangular_procedure": False,  # Adnotacje/P_23
    "margin_scheme": False,  # Adnotacje/PMarzy
    "intra_eu_new_means_of_transport": False,  # Adnotacje/NoweSrodkiTransportu
}


def an_invoice(**overrides):
    address = Address(
        street="ul. Marszałkowska 1", city="Warszawa", postal_code="00-001", country="PL"
    )
    party = Party(name="Acme sp. z o.o.", vat_id="PL5555555555", vat_scheme="PL", address=address)
    line = LineItem(
        line_id="1",
        name="Widget",
        quantity=Decimal("1"),
        unit_code="C62",
        unit_price=Decimal("100.00"),
        net_amount=Decimal("100.00"),
        vat_category=VatCategory.STANDARD,
        vat_rate=Decimal("0.23"),
    )
    return Invoice(
        **{
            "number": "FV/2026/001",
            "issue_date": date(2026, 9, 21),
            "type_code": InvoiceTypeCode.COMMERCIAL_INVOICE,
            "currency": "PLN",
            "seller": party,
            "buyer": party,
            "lines": [line],
            **overrides,
        }
    )


class TestEveryDeclarationIsMandatory:
    def test_a_complete_block_is_accepted(self):
        assert PolishExtras(**DECLARATIONS).split_payment is False

    def test_an_empty_block_reports_every_missing_declaration_at_once(self):
        with pytest.raises(ValidationError) as exc:
            PolishExtras()
        missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
        assert missing == set(DECLARATIONS)

    @pytest.mark.parametrize("field", sorted(DECLARATIONS))
    def test_no_single_declaration_has_a_default(self, field):
        partial = {k: v for k, v in DECLARATIONS.items() if k != field}
        with pytest.raises(ValidationError) as exc:
            PolishExtras(**partial)
        assert [e["loc"] for e in exc.value.errors()] == [(field,)]


class TestDeclarationsAreStrictBooleans:
    """FA(3) itself spells yes/no as "1"/"2". Accepting strings invites someone to
    copy "2" meaning no, or "1" meaning yes, into a model that means neither."""

    @pytest.mark.parametrize("value", ["1", "2", 1, 0, "yes", "true", None])
    def test_anything_but_a_json_boolean_is_refused(self, value):
        with pytest.raises(ValidationError):
            PolishExtras(**{**DECLARATIONS, "split_payment": value})

    def test_true_is_accepted(self):
        assert PolishExtras(**{**DECLARATIONS, "split_payment": True}).split_payment


class TestExemptionBasis:
    """Which kind of provision the exemption rests on: P_19A, P_19B or P_19C.

    Only an invoice with exempt lines needs it, so the model leaves it optional
    and the FA(3) mapping decides whether its absence is a problem.
    """

    def test_is_optional(self):
        assert PolishExtras(**DECLARATIONS).exemption_basis is None

    @pytest.mark.parametrize(
        ("value", "member"),
        [
            ("polish_act", ExemptionBasis.POLISH_ACT),
            ("eu_directive", ExemptionBasis.EU_DIRECTIVE),
            ("other", ExemptionBasis.OTHER),
        ],
    )
    def test_accepts_the_three_kinds(self, value, member):
        extras = PolishExtras(**DECLARATIONS, exemption_basis=value)
        assert extras.exemption_basis is member

    def test_rejects_anything_else(self):
        with pytest.raises(ValidationError):
            PolishExtras(**DECLARATIONS, exemption_basis="P_19A")


class TestOnTheInvoice:
    def test_is_optional_for_an_invoice_that_only_needs_ubl(self):
        assert an_invoice().extras is None

    def test_is_carried_when_given(self):
        invoice = an_invoice(extras=PolishExtras(**DECLARATIONS))
        assert invoice.extras.cash_accounting is False

    def test_arrives_from_json(self):
        invoice = an_invoice()
        payload = invoice.model_dump(mode="json", exclude={"vat_breakdown", "totals"})
        payload["extras"] = DECLARATIONS
        parsed = Invoice.model_validate(payload)
        assert parsed.extras == PolishExtras(**DECLARATIONS)

    def test_is_immutable(self):
        extras = PolishExtras(**DECLARATIONS)
        with pytest.raises(ValidationError):
            extras.split_payment = True
