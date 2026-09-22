"""The command line interface and its report.

With no UI, this report is the whole product surface, so its content is tested
as a contract: every problem at once, each one naming a rule, a field and a
place to look.
"""

import importlib
import json
from pathlib import Path

import pytest

from eu_einvoice_bridge.cli import main
from eu_einvoice_bridge.validate import Severity, ValidationIssue, validate_fa3

# The cli package re-exports the main() function under the same name, so the
# dotted path eu_einvoice_bridge.cli.main resolves to the function, not the
# module. Patching needs the module itself.
cli_main = importlib.import_module("eu_einvoice_bridge.cli.main")

VALID_INVOICE = {
    "number": "FV/2026/001",
    "issue_date": "2026-09-21",
    "type_code": "380",
    "currency": "PLN",
    "seller": {
        "name": "Acme sp. z o.o.",
        "vat_id": "PL5555555555",
        "vat_scheme": "PL",
        "address": {
            "street": "ul. Marszalkowska 1",
            "city": "Warszawa",
            "postal_code": "00-001",
            "country": "PL",
        },
    },
    "buyer": {
        "name": "Buyer sp. z o.o.",
        "vat_id": "PL1111111111",
        "vat_scheme": "PL",
        "address": {
            "street": "ul. Prosta 2",
            "city": "Krakow",
            "postal_code": "30-001",
            "country": "PL",
        },
    },
    "lines": [
        {
            "line_id": "1",
            "name": "Widget",
            "quantity": "2",
            "unit_code": "C62",
            "unit_price": "50.00",
            "net_amount": "100.00",
            "vat_category": "S",
            "vat_rate": "0.23",
        }
    ],
}


def write(tmp_path, payload, name="invoice.json"):
    path = tmp_path / name
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return str(path)


def run(capsys, *args) -> tuple[int, str]:
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


class TestValidInput:
    def test_reports_success_and_exits_zero(self, tmp_path, capsys):
        code, output = run(capsys, "validate", write(tmp_path, VALID_INVOICE))
        assert code == 0
        assert "valid" in output.lower()

    def test_summarises_what_was_checked(self, tmp_path, capsys):
        _, output = run(capsys, "validate", write(tmp_path, VALID_INVOICE))
        assert "EN16931" in output

    def test_convert_writes_ubl(self, tmp_path, capsys):
        out = tmp_path / "invoice.xml"
        code, _ = run(
            capsys, "convert", write(tmp_path, VALID_INVOICE), "-o", str(out)
        )
        assert code == 0
        assert out.read_bytes().startswith(b"<?xml")
        assert b"urn:cen.eu:en16931:2017" in out.read_bytes()

    def test_convert_refuses_to_write_an_invalid_invoice(self, tmp_path, capsys):
        broken = {**VALID_INVOICE, "seller": {**VALID_INVOICE["seller"], "vat_id": None}}
        out = tmp_path / "invoice.xml"
        code, _ = run(capsys, "convert", write(tmp_path, broken), "-o", str(out))
        assert code == 1
        assert not out.exists()


class TestModelErrors:
    def test_every_problem_is_reported_at_once(self, tmp_path, capsys):
        payload = json.loads(json.dumps(VALID_INVOICE))
        payload["lines"][0]["vat_rate"] = "0"  # BR-S-05: S needs a positive rate
        payload["buyer"]["address"]["country"] = "Poland"  # not a 2-letter code
        payload["number"] = ""  # BT-1 must not be empty

        code, output = run(capsys, "validate", write(tmp_path, payload))
        assert code == 1
        assert "vat_rate" in output
        assert "country" in output
        assert "number" in output

    def test_the_count_is_stated(self, tmp_path, capsys):
        payload = json.loads(json.dumps(VALID_INVOICE))
        payload["buyer"]["address"]["country"] = "Poland"
        _, output = run(capsys, "validate", write(tmp_path, payload))
        assert "1 problem" in output

    def test_a_field_path_is_shown(self, tmp_path, capsys):
        payload = json.loads(json.dumps(VALID_INVOICE))
        payload["lines"][0]["unit_code"] = ""
        _, output = run(capsys, "validate", write(tmp_path, payload))
        assert "lines.0.unit_code" in output


class TestBusinessRuleErrors:
    """A model-valid invoice can still break a business rule.

    The model refuses states that are meaningless on their own; Schematron knows
    rules that span the whole document. BR-S-02 is one: a standard-rated line
    obliges the seller to have a VAT identifier, which no single field can tell.
    """

    def test_a_missing_seller_vat_id_is_caught_by_schematron(self, tmp_path, capsys):
        payload = json.loads(json.dumps(VALID_INVOICE))
        del payload["seller"]["vat_id"]

        code, output = run(capsys, "validate", write(tmp_path, payload))
        assert code == 1
        assert "BR-S-02" in output

    def test_the_report_names_the_rule_the_terms_and_the_place(
        self, tmp_path, capsys
    ):
        payload = json.loads(json.dumps(VALID_INVOICE))
        del payload["seller"]["vat_id"]
        _, output = run(capsys, "validate", write(tmp_path, payload))

        assert "BR-S-02" in output  # rule id
        assert "BT-" in output  # business terms
        assert "/Invoice" in output  # location
        assert "Seller VAT identifier" in output  # readable message


class TestInputProblems:
    def test_a_missing_file_is_reported_without_a_traceback(self, tmp_path, capsys):
        code, output = run(capsys, "validate", str(tmp_path / "nope.json"))
        assert code == 2
        assert "nope.json" in output
        assert "Traceback" not in output

    def test_malformed_json_is_reported_clearly(self, tmp_path, capsys):
        code, output = run(capsys, "validate", write(tmp_path, "{not json"))
        assert code == 2
        assert "json" in output.lower()
        assert "Traceback" not in output


class TestExitCodes:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (VALID_INVOICE, 0),
            ({**VALID_INVOICE, "number": ""}, 1),
        ],
    )
    def test_zero_for_valid_one_for_invalid(
        self, tmp_path, capsys, payload, expected
    ):
        code, _ = run(capsys, "validate", write(tmp_path, payload))
        assert code == expected


class TestWarningsDoNotFail:
    """Only fatal rules decide the outcome; warnings are shown, not enforced.

    The serializer never produces a warning on its own, so the validator is
    replaced with one that reports a single warning-level rule.
    """

    WARNING = ValidationIssue(
        source="schematron",
        severity=Severity.WARNING,
        rule_id="UBL-CR-601",
        location="/Invoice/cac:InvoiceLine/cac:Item/cac:ClassifiedTaxCategory",
        message="A UBL invoice should not include the InvoiceLine Item "
        "ClassifiedTaxCategory TaxExemptionReason",
    )

    @pytest.fixture(autouse=True)
    def only_a_warning(self, monkeypatch):
        monkeypatch.setattr(cli_main, "validate_ubl", lambda xml: [self.WARNING])

    def test_validate_exits_zero_and_shows_the_warning(self, tmp_path, capsys):
        code, output = run(capsys, "validate", write(tmp_path, VALID_INVOICE))
        assert code == 0
        assert "valid, 1 warning" in output
        assert "UBL-CR-601" in output

    def test_convert_still_writes_the_file(self, tmp_path, capsys):
        out = tmp_path / "invoice.xml"
        code, output = run(
            capsys, "convert", write(tmp_path, VALID_INVOICE), "-o", str(out)
        )
        assert code == 0
        assert out.exists()
        assert "UBL-CR-601" in output


class TestHugeNumbers:
    """Past Decimal's 28-digit precision, quantize() raised an ArithmeticError
    that Pydantic does not convert, so it surfaced as a traceback -- with exit
    code 1, indistinguishable to a script from an invalid invoice."""

    @pytest.mark.parametrize("field", ["quantity", "unit_price", "net_amount"])
    def test_reported_as_a_model_error_not_a_crash(self, tmp_path, capsys, field):
        payload = json.loads(json.dumps(VALID_INVOICE))
        payload["lines"][0][field] = "1E+30"
        code, output = run(capsys, "validate", write(tmp_path, payload))
        assert code == 1
        assert "Traceback" not in output
        assert f"lines.0.{field}" in output


EXAMPLES = Path(__file__).parents[1] / "examples"
FA3_EXAMPLE = EXAMPLES / "invoice-fa3.json"
UBL_ONLY_EXAMPLE = EXAMPLES / "invoice.json"


class TestFa3Output:
    """--format fa3: mapping checks, then serialization, then the FA(3) XSD."""

    def test_the_default_format_is_still_ubl(self, tmp_path, capsys):
        out = tmp_path / "out.xml"
        code, _ = run(capsys, "convert", str(FA3_EXAMPLE), "-o", str(out))
        assert code == 0
        assert b"urn:cen.eu:en16931:2017" in out.read_bytes()

    def test_convert_writes_fa3_that_passes_the_official_schema(self, tmp_path, capsys):
        out = tmp_path / "out.xml"
        code, _ = run(capsys, "convert", str(FA3_EXAMPLE), "--format", "fa3", "-o", str(out))
        assert code == 0
        assert b"http://crd.gov.pl/wzor/2025/06/25/13775/" in out.read_bytes()
        assert validate_fa3(out.read_bytes()) == []

    def test_one_input_produces_both_formats(self, tmp_path, capsys):
        # Phase 2's first acceptance criterion.
        for fmt in ("ubl", "fa3"):
            code, output = run(capsys, "validate", str(FA3_EXAMPLE), "--format", fmt)
            assert code == 0, output

    def test_validate_names_the_format_it_checked(self, capsys):
        _, output = run(capsys, "validate", str(FA3_EXAMPLE), "--format", "fa3")
        assert "valid" in output
        assert "FA(3)" in output

    def test_an_unmappable_invoice_is_refused_with_every_reason(self, tmp_path, capsys):
        # invoice.json carries a document-level allowance, no declarations, and
        # a reverse-charge service to a German buyer, which FA(3)'s oo cannot hold.
        out = tmp_path / "out.xml"
        code, output = run(
            capsys, "convert", str(UBL_ONLY_EXAMPLE), "--format", "fa3", "-o", str(out)
        )
        assert code == 1
        assert not out.exists()
        assert "FA3-NO-DECLARATIONS" in output
        assert "FA3-DOC-ALLOWANCE" in output
        assert "FA3-OO-BUYER" in output
        assert "FA(3) mapping" in output

    def test_warnings_are_shown_and_the_file_is_still_written(self, tmp_path, capsys):
        payload = json.loads(FA3_EXAMPLE.read_text(encoding="utf-8"))
        payload["lines"][0]["line_id"] = "A1"
        out = tmp_path / "out.xml"
        code, output = run(
            capsys, "convert", write(tmp_path, payload), "--format", "fa3", "-o", str(out)
        )
        assert code == 0
        assert out.exists()
        assert "FA3-DROP-LINE-ID" in output

    def test_the_example_produces_no_warnings(self, capsys):
        # It exists to show the clean path, so it should stay clean.
        _, output = run(capsys, "validate", str(FA3_EXAMPLE), "--format", "fa3")
        assert "warning" not in output


class TestMultiCurrency:
    """Phase 2's last acceptance criterion, end to end through the CLI."""

    EXAMPLE = EXAMPLES / "invoice-eur.json"

    @pytest.mark.parametrize("fmt", ["ubl", "fa3"])
    def test_the_eur_example_is_valid_in_both_formats(self, capsys, fmt):
        code, output = run(capsys, "validate", str(self.EXAMPLE), "--format", fmt)
        assert code == 0, output
        assert "warning" not in output

    def test_both_outputs_state_the_same_pln_tax(self, tmp_path, capsys):
        ubl_out, fa3_out = tmp_path / "ubl.xml", tmp_path / "fa3.xml"
        run(capsys, "convert", str(self.EXAMPLE), "-o", str(ubl_out))
        run(capsys, "convert", str(self.EXAMPLE), "--format", "fa3", "-o", str(fa3_out))
        assert b'currencyID="PLN">111.52<' in ubl_out.read_bytes()
        fa3_xml = fa3_out.read_bytes()
        assert b"<P_14_1W>97.90</P_14_1W>" in fa3_xml
        assert b"<P_14_2W>13.62</P_14_2W>" in fa3_xml
