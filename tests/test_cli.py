"""The command line interface and its report.

With no UI, this report is the whole product surface, so its content is tested
as a contract: every problem at once, each one naming a rule, a field and a
place to look.
"""

import json
from decimal import Decimal

import pytest

from eu_einvoice_bridge.cli import main

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
