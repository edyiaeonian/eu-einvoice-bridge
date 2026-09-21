"""Command line entry point.

Exit codes distinguish the two things a caller cares about: 1 means the invoice
was read but is not acceptable, 2 means it could not be read at all. Conflating
them would make the difference between "fix your invoice" and "fix your file"
invisible to a script.
"""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from ..model import Invoice
from ..ubl import to_ubl
from ..validate import ValidationIssue, validate_ubl
from .report import format_issues, format_success, issues_from_pydantic

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNREADABLE = 2


class InputError(Exception):
    """The file could not be read or parsed — distinct from being invalid."""


def _load(path: Path) -> Invoice:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"{path}: cannot read file ({exc.strerror})") from exc

    try:
        return Invoice.model_validate_json(text)
    except ValidationError as exc:
        # Pydantic reports a syntax error and an invalid invoice the same way,
        # but they are different failures: one means the file is unreadable,
        # the other that its contents are wrong.
        if any(entry["type"] == "json_invalid" for entry in exc.errors()):
            detail = exc.errors()[0].get("msg", "invalid JSON")
            raise InputError(f"{path}: not valid JSON ({detail})") from exc
        raise


def _check(path: Path) -> tuple[Invoice | None, list[ValidationIssue]]:
    """Run the chain, stopping at the first layer that has something to say."""
    try:
        invoice = _load(path)
    except ValidationError as exc:
        # The model rejected it, so there is nothing to serialize and the later
        # layers have nothing to look at.
        return None, issues_from_pydantic(exc)

    return invoice, validate_ubl(to_ubl(invoice))


def _validate_command(args) -> int:
    path = Path(args.input)
    invoice, issues = _check(path)

    if issues:
        print(format_issues(path.name, issues), end="")
        return EXIT_INVALID

    print(format_success(path.name, invoice), end="")
    return EXIT_OK


def _convert_command(args) -> int:
    path = Path(args.input)
    invoice, issues = _check(path)

    if issues:
        print(format_issues(path.name, issues), end="")
        # Writing a file known to be invalid only moves the problem downstream.
        print("no output written", file=sys.stderr)
        return EXIT_INVALID

    xml = to_ubl(invoice)
    if args.output:
        Path(args.output).write_bytes(xml)
        print(f"{path.name}: wrote {args.output}")
    else:
        sys.stdout.buffer.write(xml)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="einvoice",
        description="Convert a neutral invoice to EN16931 UBL 2.1 and validate it.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser(
        "validate", help="report every problem with an invoice"
    )
    check.add_argument("input", help="invoice JSON file")
    check.set_defaults(handler=_validate_command)

    convert = subcommands.add_parser(
        "convert", help="write EN16931 UBL 2.1 XML, if the invoice is valid"
    )
    convert.add_argument("input", help="invoice JSON file")
    convert.add_argument("-o", "--output", help="write here instead of stdout")
    convert.set_defaults(handler=_convert_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except InputError as exc:
        # A stack trace here would describe this program, not the user's file.
        print(exc, file=sys.stderr)
        return EXIT_UNREADABLE


if __name__ == "__main__":
    raise SystemExit(main())
