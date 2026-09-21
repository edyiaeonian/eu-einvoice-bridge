"""What FA(3) cannot carry, reported before any XML is produced.

Two outcomes, decided by one question -- would losing this change the amounts?

- It would not: the field is dropped and a warning names it (category 3).
- It would, or FA(3) demands something the invoice lacks: an error, and no
  output at all (category 4, and category 3 fields that affect the tax base).

Every problem is reported at once, never one per attempt.
"""

from decimal import Decimal

import pytest

from eu_einvoice_bridge.fa3 import Fa3MappingError, fa3_issues, to_fa3
from eu_einvoice_bridge.model import AllowanceCharge, ExemptionBasis, PolishExtras, VatCategory
from eu_einvoice_bridge.validate import Severity

from .test_fa3 import DECLARATIONS, GENERATED_AT, a_line, a_party, an_invoice, zero


def errors(issues):
    return [i for i in issues if i.severity is Severity.ERROR]


def warnings(issues):
    return [i for i in issues if i.severity is Severity.WARNING]


def rule_ids(issues):
    return {i.rule_id for i in issues}


class TestACleanInvoice:
    def test_has_no_issues(self):
        assert fa3_issues(an_invoice()) == []

    def test_every_issue_comes_from_the_mapping_layer(self):
        invoice = an_invoice(extras=None).model_copy(update={"extras": None})
        assert {i.source for i in fa3_issues(invoice)} == {"mapping"}


class TestErrorsBlockTheOutput:
    def test_missing_polish_declarations(self):
        invoice = an_invoice().model_copy(update={"extras": None})
        assert rule_ids(errors(fa3_issues(invoice))) == {"FA3-NO-DECLARATIONS"}

    @pytest.mark.parametrize("vat_id", [None, "DE123456789"])
    def test_a_seller_without_a_polish_vat_id(self, vat_id):
        invoice = an_invoice(seller=a_party(vat_id=vat_id))
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-SELLER-NIP")
        assert issue.severity is Severity.ERROR
        assert issue.location == "seller.vat_id"

    def test_a_standard_rate_poland_does_not_levy(self):
        invoice = an_invoice(lines=[a_line(rate="0.19")])
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-RATE")
        assert issue.severity is Severity.ERROR
        assert "0.19" in issue.message

    def test_a_document_level_allowance_affects_the_tax_base_so_cannot_be_dropped(self):
        # Dropping it would make FA(3) state a different tax from UBL.
        allowance = AllowanceCharge(
            is_charge=False,
            amount=Decimal("10.00"),
            vat_category=VatCategory.STANDARD,
            vat_rate=Decimal("0.23"),
        )
        invoice = an_invoice(allowance_charges=[allowance])
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-DOC-ALLOWANCE")
        assert issue.severity is Severity.ERROR
        assert set(issue.business_terms) >= {"BG-20", "BG-21"}

    def test_an_exempt_invoice_without_the_kind_of_legal_basis(self):
        invoice = an_invoice(lines=[zero(VatCategory.EXEMPT)])  # no exemption_basis
        assert "FA3-EXEMPTION-BASIS" in rule_ids(errors(fa3_issues(invoice)))

    def test_an_exempt_line_with_only_a_reason_code(self):
        # FA(3) wants the provision as text; a VATEX code alone cannot fill it.
        invoice = an_invoice(
            lines=[
                zero(
                    VatCategory.EXEMPT,
                    exemption_reason=None,
                    exemption_reason_code="VATEX-EU-132",
                )
            ],
            extras=PolishExtras(**DECLARATIONS, exemption_basis=ExemptionBasis.POLISH_ACT),
        )
        assert "FA3-EXEMPTION-TEXT" in rule_ids(errors(fa3_issues(invoice)))

    @pytest.mark.parametrize(
        ("declaration", "rule"),
        [
            ("margin_scheme", "FA3-MARGIN-SCHEME"),
            ("intra_eu_new_means_of_transport", "FA3-NEW-TRANSPORT"),
        ],
    )
    def test_declared_regimes_this_serializer_does_not_support(self, declaration, rule):
        extras = PolishExtras(**{**DECLARATIONS, declaration: True})
        assert rule in rule_ids(errors(fa3_issues(an_invoice(extras=extras))))


class TestWarningsDoNotBlock:
    """Fields FA(3) has no place for, whose loss changes no amount."""

    def test_legal_registration_id(self):
        invoice = an_invoice(seller=a_party().model_copy(update={"legal_registration_id": "0000123456"}))
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-DROP-BT30")
        assert issue.severity is Severity.WARNING
        assert issue.location == "seller.legal_registration_id"
        assert "BT-30" in issue.business_terms

    def test_an_exemption_reason_code(self):
        invoice = an_invoice(
            lines=[zero(VatCategory.EXEMPT, exemption_reason_code="VATEX-EU-132")],
            extras=PolishExtras(**DECLARATIONS, exemption_basis=ExemptionBasis.POLISH_ACT),
        )
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-DROP-BT121")
        assert issue.severity is Severity.WARNING
        assert issue.location == "lines.0.exemption_reason_code"

    def test_a_reason_on_a_category_fa3_signals_by_code_alone(self):
        # Reverse charge travels as "oo" and P_18; its reason text has no slot.
        invoice = an_invoice(lines=[zero(VatCategory.REVERSE_CHARGE)])
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-DROP-REASON")
        assert issue.severity is Severity.WARNING
        assert issue.location == "lines.0.exemption_reason"

    def test_a_line_id_that_is_not_its_position(self):
        invoice = an_invoice(lines=[a_line("A1")])
        issue = next(i for i in fa3_issues(invoice) if i.rule_id == "FA3-DROP-LINE-ID")
        assert issue.severity is Severity.WARNING
        assert "BT-126" in issue.business_terms

    def test_a_line_id_equal_to_its_position_loses_nothing(self):
        invoice = an_invoice(lines=[a_line("1"), a_line("2")])
        assert "FA3-DROP-LINE-ID" not in rule_ids(fa3_issues(invoice))

    def test_warnings_alone_still_produce_output(self):
        invoice = an_invoice(lines=[a_line("A1")])
        assert warnings(fa3_issues(invoice))
        assert to_fa3(invoice, generated_at=GENERATED_AT).startswith(b"<?xml")


class TestEverythingAtOnce:
    def test_several_independent_problems_are_all_reported(self):
        allowance = AllowanceCharge(
            is_charge=False,
            amount=Decimal("10.00"),
            vat_category=VatCategory.STANDARD,
            vat_rate=Decimal("0.23"),
        )
        invoice = an_invoice(
            lines=[a_line("A1", rate="0.19")],
            allowance_charges=[allowance],
            seller=a_party(vat_id="DE123456789"),
        ).model_copy(update={"extras": None})
        found = rule_ids(fa3_issues(invoice))
        assert found >= {
            "FA3-NO-DECLARATIONS",
            "FA3-SELLER-NIP",
            "FA3-RATE",
            "FA3-DOC-ALLOWANCE",
            "FA3-DROP-LINE-ID",
        }

    def test_the_serializer_refuses_with_every_error_not_the_first(self):
        invoice = an_invoice(
            lines=[a_line(rate="0.19")], seller=a_party(vat_id=None)
        )
        with pytest.raises(Fa3MappingError) as exc:
            to_fa3(invoice, generated_at=GENERATED_AT)
        assert rule_ids(exc.value.issues) >= {"FA3-RATE", "FA3-SELLER-NIP"}

    def test_errors_come_before_warnings(self):
        invoice = an_invoice(lines=[a_line("A1", rate="0.19")])
        severities = [i.severity for i in fa3_issues(invoice)]
        assert severities == sorted(severities, key=lambda s: s is Severity.WARNING)
