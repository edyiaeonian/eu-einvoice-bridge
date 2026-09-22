"""Submit one FA(3) invoice to KSeF TEST and retrieve its UPO, resumably.

Sending is asynchronous and spans many round trips, any of which can be cut
off. So every step is recorded in a local state file, and the record is written
*before* the invoice is sent: an unanswered send does not mean the invoice did
not arrive, and a crash in that window must not lead to sending it twice.

On a rerun the record decides what happens:

- accepted: nothing is sent; the stored result is returned
- sending, with no reference: the session is searched for the invoice's hash,
  and only if it is there does the run continue -- it is never resent
- sent: status polling resumes where it stopped
- refused (KSeF turned the send request down) or rejected (KSeF checked the
  invoice and refused it): nothing is held by KSeF, so a corrected invoice may
  be sent under the same number

Tokens are held in memory only and never written to the record.
"""

import datetime as dt
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .. import crypto
from .client import KsefClient, KsefError, TransientError
from .identity import TestIdentity, sign_auth_request

# Status codes seen live in TEST: 100/150 while processing, 200 on success.
# Anything from 300 up is a final refusal.
_SUCCESS = 200


class SubmissionError(Exception):
    pass


class SubmissionConflict(SubmissionError):
    """A different document was already submitted under this invoice number."""


class SubmissionUncertain(SubmissionError):
    """A previous send went unanswered and KSeF shows no trace of it.

    It may still be processing, or it may never have arrived; resending could
    duplicate it, so this stops and says so instead of guessing.
    """


@dataclass(frozen=True)
class Submission:
    invoice_number: str
    source_hash: str  # of the input document, to spot a changed invoice
    invoice_hash: str  # of the exact FA(3) bytes sent, which KSeF lists
    status: str  # sending | sent | accepted | rejected | refused
    session_reference: str | None = None
    invoice_reference: str | None = None
    ksef_number: str | None = None
    upo_path: str | None = None
    error: str | None = None
    updated_at: str | None = None
    # The NIP that authenticated. KSeF ties a session to it, so only that
    # identity can look the invoice up again.
    seller_nip: str | None = None


# KSeF may hold, or come to hold, the invoice: the number is taken.
_IN_KSEF = frozenset({"sending", "sent", "accepted"})


class StateStore:
    """One JSON file per invoice number under a directory kept out of git."""

    def __init__(self, directory: Path, clock: Callable[[], dt.datetime]):
        self.directory = directory
        self._clock = clock

    def _stem(self, invoice_number: str) -> str:
        # Invoice numbers carry slashes ("FV/2026/001"); file names cannot.
        return re.sub(r"[^A-Za-z0-9._-]", "_", invoice_number)

    def path(self, invoice_number: str) -> Path:
        return self.directory / f"{self._stem(invoice_number)}.json"

    def upo_path(self, invoice_number: str) -> Path:
        return self.directory / f"{self._stem(invoice_number)}.upo.xml"

    def xml_path(self, invoice_number: str) -> Path:
        # The exact bytes sent. FA(3) carries a generation timestamp, so
        # rendering again would give a different document and a different hash.
        return self.directory / f"{self._stem(invoice_number)}.fa3.xml"

    def load(self, invoice_number: str) -> Submission | None:
        path = self.path(invoice_number)
        if not path.exists():
            return None
        return Submission(**json.loads(path.read_text(encoding="utf-8")))

    def save(self, record: Submission) -> Submission:
        record = replace(record, updated_at=self._clock().isoformat())
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path(record.invoice_number)
        # Write-then-rename so a crash mid-write cannot leave half a record.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(record), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return record


@dataclass
class Polling:
    timeout_seconds: float = 120.0
    interval_seconds: float = 2.0


def authenticate(
    client: KsefClient,
    identity: TestIdentity,
    *,
    sleep: Callable[[float], None],
    polling: Polling,
) -> str:
    """challenge -> signed AuthTokenRequest -> poll status -> redeem an access token."""
    challenge = client.challenge()
    reference, auth_token = client.submit_xades(sign_auth_request(challenge, identity))
    waited = 0.0
    while True:
        status = client.auth_status(reference, auth_token)
        if status["code"] == _SUCCESS:
            return client.redeem(auth_token)
        if status["code"] >= 300:
            raise SubmissionError(f"authentication refused: {status.get('description')}")
        if waited >= polling.timeout_seconds:
            raise SubmissionError("authentication did not complete in time")
        sleep(polling.interval_seconds)
        waited += polling.interval_seconds


def _send(
    client: KsefClient,
    store: StateStore,
    access_token: str,
    record: Submission,
    invoice_xml: bytes,
) -> Submission:
    certificate = client.symmetric_key_certificate()
    public_key = crypto.public_key_from_certificate(certificate["certificate"])
    session_key = crypto.new_session_key()
    session = client.open_online_session(
        access_token,
        crypto.b64(crypto.wrap_key(session_key.key, public_key)),
        crypto.b64(session_key.iv),
        certificate.get("publicKeyId"),
    )
    encrypted = crypto.encrypt(invoice_xml, session_key)

    # Recorded before sending: from here on, a rerun must query, never resend.
    record = store.save(replace(record, status="sending", session_reference=session))
    try:
        reference = client.send_invoice(
            session,
            access_token,
            {
                "invoiceHash": record.invoice_hash,
                "invoiceSize": len(invoice_xml),
                "encryptedInvoiceHash": crypto.sha256_b64(encrypted),
                "encryptedInvoiceSize": len(encrypted),
                "encryptedInvoiceContent": crypto.b64(encrypted),
            },
        )
    except TransientError as exc:
        # The record stays "sending": the invoice may have arrived.
        raise SubmissionError(
            f"the send went unanswered ({exc}); run again to look for it -- "
            f"it will not be resent"
        ) from exc
    except KsefError as exc:
        # A 4xx is an answer: the request was turned down and KSeF holds
        # nothing. Unlike an unanswered send, trying again is safe.
        store.save(replace(record, status="refused", error=f"{exc}: {exc.body}"))
        raise SubmissionError(f"KSeF refused the send request: {exc} {exc.body}") from exc
    return store.save(replace(record, status="sent", invoice_reference=reference))


def _session(record: Submission) -> str:
    if record.session_reference is None:
        raise SubmissionError(f"the record for {record.invoice_number} names no session")
    return record.session_reference


def _recover(
    client: KsefClient, store: StateStore, access_token: str, record: Submission
) -> Submission:
    """A send went unanswered last time; look for it instead of resending."""
    for invoice in client.session_invoices(_session(record), access_token):
        if invoice.get("invoiceHash") == record.invoice_hash:
            return store.save(
                replace(record, status="sent", invoice_reference=invoice["referenceNumber"])
            )
    raise SubmissionUncertain(
        f"invoice {record.invoice_number} was sent in session "
        f"{record.session_reference} without an answer, and the session does not "
        f"list it; it has not been resent -- check the session before retrying"
    )


def _await_result(
    client: KsefClient,
    store: StateStore,
    access_token: str,
    record: Submission,
    *,
    sleep: Callable[[float], None],
    polling: Polling,
) -> Submission:
    session, invoice = _session(record), record.invoice_reference
    if invoice is None:
        raise SubmissionError(f"the record for {record.invoice_number} names no invoice")
    waited = 0.0
    while True:
        result = client.invoice_status(session, invoice, access_token)
        code = result["status"]["code"]
        if code == _SUCCESS:
            # Saved before the download: if that fails, the number is not lost
            # and a rerun only has to fetch the UPO.
            record = store.save(replace(record, ksef_number=result.get("ksefNumber")))
            upo = client.download_upo(session, invoice, access_token)
            upo_path = store.upo_path(record.invoice_number)
            upo_path.write_bytes(upo)
            return store.save(
                replace(record, status="accepted", upo_path=str(upo_path), error=None)
            )
        if code >= 300:
            details = "; ".join(result["status"].get("details") or [])
            reason = f"{code} {result['status'].get('description')}"
            if details:
                reason += f": {details}"
            return store.save(replace(record, status="rejected", error=reason))
        if waited >= polling.timeout_seconds:
            # Left as "sent": a rerun resumes polling rather than resending.
            return record
        sleep(polling.interval_seconds)
        waited += polling.interval_seconds


def submit(
    invoice_number: str,
    source_hash: str,
    render: Callable[[], bytes],
    *,
    client: KsefClient,
    identity: TestIdentity,
    store: StateStore,
    sleep: Callable[[float], None] = time.sleep,
    polling: Polling | None = None,
) -> Submission:
    """Send an FA(3) invoice, or continue a submission already under way.

    render() is called only for a new submission; a resumed one reuses the
    bytes stored when it was first sent.

    Once the invoice has been sent, a failure that retrying could fix does not
    raise: the record is returned still "sent", with the reason in `error`, so
    the caller knows a rerun will pick it up.
    """
    polling = polling or Polling()
    record = store.load(invoice_number)

    if record is not None and record.source_hash != source_hash:
        if record.status in _IN_KSEF:
            raise SubmissionConflict(
                f"invoice {invoice_number} was already submitted with different content; "
                f"KSeF numbers are per document, so it will not be replaced"
            )
        record = None  # rejected or refused: KSeF holds nothing, so start over
    if record is not None and record.status in ("accepted", "rejected"):
        return record
    if record is not None and record.status == "refused":
        record = None  # the request was turned down; sending again is safe
    if record is not None and record.seller_nip not in (None, identity.nip):
        raise SubmissionError(
            f"invoice {invoice_number} was sent as NIP {record.seller_nip}, but the "
            f"test identity is now NIP {identity.nip}. KSeF only shows a session to "
            f"the identity that opened it, so the original identity is needed to "
            f"follow this invoice up; restore it (--identity-dir) rather than resend"
        )

    access_token = authenticate(client, identity, sleep=sleep, polling=polling)
    opened_now = False

    if record is None:
        invoice_xml = render()
        store.directory.mkdir(parents=True, exist_ok=True)
        store.xml_path(invoice_number).write_bytes(invoice_xml)
        # Not saved until the session is open; a failure before that sent nothing.
        record = Submission(
            invoice_number=invoice_number,
            source_hash=source_hash,
            invoice_hash=crypto.sha256_b64(invoice_xml),
            status="new",
            seller_nip=identity.nip,
        )
        record = _send(client, store, access_token, record, invoice_xml)
        opened_now = True
    elif record.status == "sending":
        record = _recover(client, store, access_token, record)

    try:
        record = _await_result(client, store, access_token, record, sleep=sleep, polling=polling)
    except TransientError as exc:
        # The invoice is in KSeF; only following it up failed.
        return store.save(replace(store.load(invoice_number) or record, error=str(exc)))

    if opened_now and record.status in ("accepted", "rejected"):
        try:
            client.close_session(_session(record), access_token)
        except KsefError:
            pass  # the session expires by itself; the invoice's result is what matters
    return record
