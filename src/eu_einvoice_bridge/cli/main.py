"""Command line entry point.

Exit codes distinguish the two things a caller cares about: 1 means the invoice
was read but is not acceptable, 2 means a file could not be read at all -- or
the output could not be written. Conflating them would make the difference
between "fix your invoice" and "fix your file" invisible to a script.

submit adds two more. 3 means the invoice is in KSeF and only following it up
is unfinished -- still processing, or accepted with the UPO not yet downloaded;
running the same command again picks it up. 4 means the submission itself
failed: authentication, a refused request, or a previous send that cannot be
accounted for.
"""

import argparse
import sys
from datetime import UTC, datetime
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
EXIT_PENDING = 3
EXIT_SUBMISSION_FAILED = 4


class InputError(Exception):
    """The file could not be read or parsed — distinct from being invalid."""


def _describe(exc: OSError) -> str:
    """An OSError as a line for the user, where a traceback would be noise."""
    if exc.filename is None:
        return str(exc)
    return f"{exc.filename}: {exc.strerror}"


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
    xml = to_fa3(invoice, generated_at=datetime.now(UTC))
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


def _validate_command(args: argparse.Namespace) -> int:
    path = Path(args.input)
    invoice, _, issues = _check(path, args.format)

    if has_errors(issues):
        print(format_issues(path.name, issues), end="")
        return EXIT_INVALID

    # Whatever remains is warnings: shown, but they do not make it invalid.
    assert invoice is not None  # only a model error leaves no invoice
    print(format_success(path.name, invoice, issues, args.format), end="")
    return EXIT_OK


def _convert_command(args: argparse.Namespace) -> int:
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

    assert xml is not None  # only a blocking error leaves no XML
    if args.output:
        try:
            Path(args.output).write_bytes(xml)
        except OSError as exc:
            print(f"cannot write the output: {_describe(exc)}", file=sys.stderr)
            return EXIT_UNREADABLE
        print(f"{path.name}: wrote {args.output}")
    else:
        sys.stdout.buffer.write(xml)
    return EXIT_OK


def _seller_for(invoice: Invoice, nip: str, substitute: bool) -> Invoice | None:
    """KSeF TEST accepts an invoice only from the NIP that authenticated."""
    wanted = f"PL{nip}"
    if invoice.seller.vat_id == wanted:
        return invoice
    if not substitute:
        return None
    seller = invoice.seller.model_copy(update={"vat_id": wanted})
    return invoice.model_copy(update={"seller": seller})


def _submit_command(args: argparse.Namespace) -> int:
    # Imported here so validate and convert never load the network stack.
    import hashlib

    import httpx

    from .. import ksef

    path = Path(args.input)
    try:
        invoice = _load(path)
    except ValidationError as exc:
        print(format_issues(path.name, issues_from_pydantic(exc)), end="")
        return EXIT_INVALID

    # Hashed before the seller is replaced: the record must follow the user's
    # invoice, not whichever test identity happens to be on disk.
    #
    # This hashes Pydantic's JSON rendering of the model, so it is only stable
    # while that rendering is. A Pydantic release that spelled a Decimal or a
    # date differently would make an invoice already in flight look changed,
    # and a rerun would stop with a conflict rather than follow it up. uv.lock
    # pins the version; an upgrade should be made with no submission pending.
    source_hash = hashlib.sha256(invoice.model_dump_json().encode()).hexdigest()

    identity_dir = Path(args.identity_dir)
    try:
        identity = ksef.load_identity(identity_dir)
        created = identity is None
        if identity is None:
            identity = ksef.create_test_identity()
            ksef.save_identity(identity, identity_dir)
    except ksef.IdentityError as exc:
        print(f"{path.name}: {exc}", file=sys.stderr)
        return EXIT_SUBMISSION_FAILED
    except OSError as exc:
        print(f"{path.name}: test identity: {_describe(exc)}", file=sys.stderr)
        return EXIT_SUBMISSION_FAILED
    if created:
        print(
            f"created a self-signed KSeF TEST identity for NIP {identity.nip} in {identity_dir}/",
            file=sys.stderr,
        )

    submitted = _seller_for(invoice, identity.nip, args.test_seller)
    if submitted is None:
        print(
            f"{path.name}: seller.vat_id is {invoice.seller.vat_id}, but the test identity "
            f"is PL{identity.nip}; KSeF only accepts invoices from the NIP that "
            f"authenticated. Pass --test-seller to substitute it.",
            file=sys.stderr,
        )
        return EXIT_INVALID
    invoice = submitted
    xml, issues = _fa3_chain(invoice)
    if has_errors(issues):
        print(format_issues(path.name, issues), end="", flush=True)
        print("not submitted", file=sys.stderr)
        return EXIT_INVALID
    assert xml is not None  # only a blocking error leaves no XML
    document = xml
    if issues:
        print(format_issues(path.name, issues), end="", file=sys.stderr)

    store = ksef.StateStore(Path(args.state_dir), clock=lambda: datetime.now(UTC))
    polling = ksef.Polling(timeout_seconds=args.timeout)
    try:
        with httpx.Client(base_url=ksef.TEST_BASE_URL, timeout=30.0) as http:
            record = ksef.submit(
                invoice.number,
                source_hash,
                lambda: document,
                client=ksef.KsefClient(http),
                identity=identity,
                store=store,
                polling=polling,
            )
    except (ksef.SubmissionError, ksef.KsefError) as exc:
        print(f"{path.name}: {exc}", file=sys.stderr)
        return EXIT_SUBMISSION_FAILED
    except OSError as exc:
        # The state directory, most likely. Whatever was recorded before the
        # failure still holds: a record saved as "sending" is looked up, not
        # resent, on the next run.
        print(f"{path.name}: {_describe(exc)}", file=sys.stderr)
        return EXIT_SUBMISSION_FAILED

    if record.status == "accepted":
        print(
            f"{path.name}: accepted by KSeF TEST\n"
            f"  KSeF number: {record.ksef_number}\n"
            f"  UPO:         {record.upo_path}"
        )
        return EXIT_OK
    if record.status == "rejected":
        print(f"{path.name}: rejected by KSeF TEST\n  {record.error}")
        return EXIT_INVALID
    if record.ksef_number:
        print(
            f"{path.name}: accepted by KSeF TEST as {record.ksef_number}, but the UPO "
            f"could not be downloaded ({record.error}); run the same command again to fetch it",
            file=sys.stderr,
        )
    elif record.error:
        print(
            f"{path.name}: sent (session {record.session_reference}), but checking its "
            f"status failed ({record.error}); run the same command again",
            file=sys.stderr,
        )
    else:
        print(
            f"{path.name}: sent, still processing after {args.timeout:g}s "
            f"(session {record.session_reference}); run the same command again to check",
            file=sys.stderr,
        )
    return EXIT_PENDING


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="einvoice",
        description=(
            "Convert a neutral invoice to EN16931 UBL 2.1 or Poland's FA(3), "
            "and validate the result."
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_format(sub: argparse.ArgumentParser) -> None:
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

    send = subcommands.add_parser(
        "submit",
        help="send the invoice as FA(3) to KSeF's TEST environment and fetch its UPO",
    )
    send.add_argument("input", help="invoice JSON file")
    send.add_argument(
        "--state-dir", default="state", help="where submission records and UPOs go (default: state)"
    )
    send.add_argument(
        "--identity-dir",
        default="certs",
        help="the self-signed test identity; created here if missing (default: certs)",
    )
    send.add_argument(
        "--test-seller",
        action="store_true",
        help="replace the seller's NIP with the test identity's",
    )
    send.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="seconds to wait for KSeF's verdict (default: 120)",
    )
    send.set_defaults(handler=_submit_command)

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
