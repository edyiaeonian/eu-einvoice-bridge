"""The validation chain and the shape of what it reports.

The error objects are the CLI's only input, so their structure is tested as
carefully as the pass/fail outcome: a validator that knows something is wrong
but cannot say where is barely more useful than one that says nothing.
"""

from decimal import Decimal

import pytest
from lxml import etree

from eu_einvoice_bridge.validate import Severity, ValidationIssue, validate_ubl

from .test_ubl import a_golden_invoice, an_invoice
from eu_einvoice_bridge.ubl import to_ubl


@pytest.fixture(scope="module")
def conformant() -> bytes:
    return to_ubl(a_golden_invoice())


def corrupt(xml: bytes, element: str, value: str) -> bytes:
    """Change one element's text, leaving the document otherwise intact."""
    cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    root = etree.fromstring(xml)
    node = root.find(f".//{{{cbc}}}{element}")
    node.text = value
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


class TestConformantInput:
    def test_a_conformant_invoice_produces_no_issues(self, conformant):
        assert validate_ubl(conformant) == []

    def test_every_official_plain_en16931_invoice_example_passes(self):
        """The strongest check available: documents we did not write.

        Restricted to Invoice documents claiming plain EN16931. Credit notes
        have a different root and their own schema, and CIUS profiles carry
        extra rules this project does not implement.
        """
        from eu_einvoice_bridge.paths import EN16931_EXAMPLES

        invoice_root = "{urn:oasis:names:specification:ubl:schema:xsd:Invoice-2}Invoice"
        checked = []
        for path in sorted(EN16931_EXAMPLES.glob("*.xml")):
            xml = path.read_bytes()
            if b"urn:cen.eu:en16931:2017<" not in xml:
                continue
            if etree.fromstring(xml).tag != invoice_root:
                continue
            checked.append(path.name)
            assert validate_ubl(xml) == [], f"{path.name} reported issues"
        assert len(checked) >= 3, checked

    def test_a_credit_note_is_refused_by_the_invoice_schema(self):
        """Not a gap: this validator is for Invoice documents only."""
        from eu_einvoice_bridge.paths import EN16931_EXAMPLES

        credit_note = (EN16931_EXAMPLES / "ubl-tc434-creditnote1.xml").read_bytes()
        issues = validate_ubl(credit_note)
        assert [i.source for i in issues] == ["xsd"]


class TestSchematronFailures:
    def test_an_inconsistent_total_is_reported(self, conformant):
        broken = corrupt(conformant, "TaxInclusiveAmount", "99999.99")
        issues = validate_ubl(broken)
        assert {i.rule_id for i in issues} >= {"BR-CO-15"}

    def test_the_issue_carries_a_location_and_a_message(self, conformant):
        broken = corrupt(conformant, "TaxInclusiveAmount", "99999.99")
        issue = next(i for i in validate_ubl(broken) if i.rule_id == "BR-CO-15")
        assert issue.location and issue.location.startswith("/")
        assert len(issue.message) > 20
        assert issue.severity is Severity.ERROR

    def test_the_location_is_readable_rather_than_namespace_literal(self, conformant):
        broken = corrupt(conformant, "PayableAmount", "12345.67")
        issue = next(i for i in validate_ubl(broken) if i.rule_id == "BR-CO-16")
        assert "namespace-uri()" not in issue.location
        assert issue.location == "/Invoice/cac:LegalMonetaryTotal"

    def test_positional_indexes_survive_where_they_matter(self):
        from eu_einvoice_bridge.validate.validator import _readable_location

        raw = (
            "/*:Invoice[namespace-uri()='urn:oasis:names:specification:ubl:schema:"
            "xsd:Invoice-2'][1]/*:InvoiceLine[namespace-uri()='urn:oasis:names:"
            "specification:ubl:schema:xsd:CommonAggregateComponents-2'][3]"
        )
        assert _readable_location(raw) == "/Invoice/cac:InvoiceLine[3]"

    def test_the_message_does_not_repeat_the_rule_id(self, conformant):
        broken = corrupt(conformant, "TaxInclusiveAmount", "99999.99")
        issue = next(i for i in validate_ubl(broken) if i.rule_id == "BR-CO-15")
        assert not issue.message.startswith("[BR-CO-15]")
        assert issue.message.startswith("Invoice total amount with VAT")

    def test_business_terms_are_extracted_from_the_rule_text(self, conformant):
        # The CLI shows these, so a reader can find the field without knowing
        # the rule by heart.
        broken = corrupt(conformant, "TaxInclusiveAmount", "99999.99")
        issue = next(i for i in validate_ubl(broken) if i.rule_id == "BR-CO-15")
        assert "BT-112" in issue.business_terms

    def test_several_independent_failures_are_all_reported(self, conformant):
        broken = corrupt(conformant, "TaxInclusiveAmount", "99999.99")
        broken = corrupt(broken, "PayableAmount", "12345.67")
        rule_ids = {i.rule_id for i in validate_ubl(broken)}
        assert len(rule_ids) >= 2


class TestXsdFailures:
    """XSD catches what Schematron structurally cannot."""

    @staticmethod
    def _with_issue_date_moved_to_the_end() -> bytes:
        root = etree.fromstring(to_ubl(an_invoice()))
        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        issue_date = root.find(f"{{{cbc}}}IssueDate")
        root.remove(issue_date)
        root.append(issue_date)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    def test_element_order_is_rejected(self):
        issues = validate_ubl(self._with_issue_date_moved_to_the_end())
        assert any(i.source == "xsd" for i in issues)

    def test_schematron_alone_would_have_let_the_reordering_through(self):
        """The evidence that the two layers are not redundant.

        Schematron finds elements by XPath and does not care where they sit;
        the XSD sequence does. Without this, "we run both" would be an
        unexamined assumption rather than a reason.
        """
        from eu_einvoice_bridge.validate.validator import _schematron_issues

        reordered = self._with_issue_date_moved_to_the_end()
        assert _schematron_issues(reordered) == []
        assert any(i.source == "xsd" for i in validate_ubl(reordered))

    def test_an_unparseable_document_is_reported_rather_than_raised(self):
        issues = validate_ubl(b"<Invoice>not closed")
        assert len(issues) == 1
        assert issues[0].source == "xml"
        assert issues[0].severity is Severity.ERROR


class TestIssueOrdering:
    def test_xsd_issues_come_before_schematron_issues(self, conformant):
        # Structural problems are the likelier root cause, so they are shown
        # first rather than buried under consequent semantic complaints.
        broken = corrupt(conformant, "TaxInclusiveAmount", "not-a-number")
        issues = validate_ubl(broken)
        sources = [i.source for i in issues]
        assert sources == sorted(sources, key=lambda s: {"xml": 0, "xsd": 1}.get(s, 2))


class TestValidationIssue:
    def test_renders_as_one_readable_line(self):
        issue = ValidationIssue(
            source="schematron",
            severity=Severity.ERROR,
            rule_id="BR-CO-15",
            business_terms=("BT-112", "BT-109", "BT-110"),
            location="/Invoice[1]/cac:LegalMonetaryTotal[1]",
            message="Invoice total amount with VAT must equal the sum.",
        )
        rendered = str(issue)
        assert "BR-CO-15" in rendered
        assert "BT-112" in rendered
