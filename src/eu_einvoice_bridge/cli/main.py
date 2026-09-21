"""Command line entry point.

Exit codes distinguish the two things a caller cares about: 1 means the invoice
was read but is not acceptable, 2 means it could not be read at all. Conflating
them would make the difference between "fix your invoice" and "fix your file"
invisible to a script.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from ..fa3 import fa3_issues, to_fa3
from ..model import Invoice
from ..ubl import to_ubl
from ..validate import ValidationIssue, has_errors, validate_fa3, validate_ubl
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


FORMATS = ("ubl", "fa3")


def _fa3_chain(invoice: Invoice) -> tuple[bytes | None, list[ValidationIssue]]:
    issues = fa3_issues(invoice)
    if has_errors(issues):
        # Something FA(3) cannot express faithfully: no XML is produced at all.
        return None, issues
    # The serializer never reads the clock, so its output is reproducible; this
    # is the edge of the program, where the real time belongs.
    xml = to_fa3(invoice, generated_at=datetime.now(timezone.utc))
    return xml, sorted(issues + validate_fa3(xml), key=lambda i: i.sort_key)


def _check(
    path: Path, fmt: str
) -> tuple[Invoice | None, bytes | None, list[ValidationIssue]]:
    """Run the chain, stopping at the first layer that has something to say."""
    try:
        invoice = _load(path)
    except ValidationError as exc:
        # The model rejected it, so there is nothing to serialize and the later
        # layers have nothing to look at.
        return None, None, issues_from_pydantic(exc)

    if fmt == "fa3":
        xml, issues = _fa3_chain(invoice)
        return invoice, xml, issues
    xml = to_ubl(invoice)
    return invoice, xml, validate_ubl(xml)


def _validate_command(args) -> int:
    path = Path(args.input)
    invoice, _, issues = _check(path, args.format)

    if has_errors(issues):
        print(format_issues(path.name, issues), end="")
        return EXIT_INVALID

    # Whatever remains is warnings: shown, but they do not make it invalid.
    print(format_success(path.name, invoice, issues, args.format), end="")
    return EXIT_OK


def _convert_command(args) -> int:
    path = Path(args.input)
    _, xml, issues = _check(path, args.format)

    if has_errors(issues):
        # Flushed so the report precedes the stderr notice even when stdout is
        # piped and block-buffered.
        print(format_issues(path.name, issues), end="", flush=True)
        # Writing a file known to be invalid only moves the problem downstream.
        print("no output written", file=sys.stderr)
        return EXIT_INVALID

    if issues:
        # stderr, so warnings never end up inside XML written to stdout.
        print(format_issues(path.name, issues), end="", file=sys.stderr)

    if args.output:
        Path(args.output).write_bytes(xml)
        print(f"{path.name}: wrote {args.output}")
    else:
        sys.stdout.buffer.write(xml)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="einvoice",
        description=(
            "Convert a neutral invoice to EN16931 UBL 2.1 or Poland's FA(3), "
            "and validate the result."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_format(sub):
        sub.add_argument(
            "--format",
            choices=FORMATS,
            default="ubl",
            help="ubl: EN16931 in UBL 2.1 (default); fa3: Poland's FA(3)",
        )

    check = subcommands.add_parser(
        "validate", help="report every problem with an invoice"
    )
    check.add_argument("input", help="invoice JSON file")
    add_format(check)
    check.set_defaults(handler=_validate_command)

    convert = subcommands.add_parser(
        "convert", help="write the XML, if the invoice is valid for that format"
    )
    convert.add_argument("input", help="invoice JSON file")
    convert.add_argument("-o", "--output", help="write here instead of stdout")
    add_format(convert)
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
