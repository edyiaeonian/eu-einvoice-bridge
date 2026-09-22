"""The submission flow against a mocked KSeF, offline.

What matters most is what happens when a step fails: which calls are
retried, what the state file says at each point, and that an invoice is
never sent twice.
"""

import base64
import datetime as dt
import json

import httpx
import pytest
import respx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from eu_einvoice_bridge import crypto
from eu_einvoice_bridge.ksef import (
    TEST_BASE_URL,
    KsefClient,
    KsefError,
    Polling,
    StateStore,
    SubmissionConflict,
    SubmissionError,
    SubmissionUncertain,
    TransientError,
    create_test_identity,
    submit,
)

BASE = TEST_BASE_URL
SESSION = "20260921-SO-SESSION-01"
INVOICE_REF = "20260921-EE-INVOICE-01"
KSEF_NUMBER = "5265877635-20260921-0100001AF629-AF"
NUMBER = "FV/2026/001"
XML = b"<Faktura>test</Faktura>"
UPO = b"<Potwierdzenie>upo</Potwierdzenie>"
SOURCE = "source-hash-1"


@pytest.fixture(scope="module")
def identity():
    return create_test_identity()


@pytest.fixture(scope="module")
def ksef_key():
    """Stands in for the key KSeF publishes for symmetric key encryption."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KSeF TEST")])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    der = base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()
    return key, der


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state", clock=lambda: dt.datetime(2026, 9, 21, 12, 0))


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    return KsefClient(httpx.Client(base_url=BASE), backoff_seconds=1.0, sleep=sleeps.append)


def status(code, description="", **extra):
    return {"status": {"code": code, "description": description}, **extra}


@pytest.fixture
def ksef(ksef_key):
    """A KSeF that authenticates, accepts the invoice and issues a UPO."""
    _, der = ksef_key
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.post("/auth/challenge").respond(json={"challenge": "20260921-CR-X"})
        mock.post("/auth/xades-signature").respond(
            202, json={"referenceNumber": "AUTH-1", "authenticationToken": {"token": "auth-token"}}
        )
        mock.get("/auth/AUTH-1").respond(json=status(200, "ok"))
        mock.post("/auth/token/redeem").respond(json={"accessToken": {"token": "access-token"}})
        mock.get("/security/public-key-certificates").respond(json=[
            {"certificate": "unused", "usage": ["KsefTokenEncryption"]},
            {"certificate": der, "usage": ["SymmetricKeyEncryption"], "publicKeyId": "key-1"},
        ])
        mock.post("/sessions/online", name="open").respond(201, json={"referenceNumber": SESSION})
        mock.post(f"/sessions/online/{SESSION}/invoices", name="send").respond(
            202, json={"referenceNumber": INVOICE_REF}
        )
        mock.get(f"/sessions/{SESSION}/invoices/{INVOICE_REF}", name="status").mock(side_effect=[
            httpx.Response(200, json=status(150, "Trwa przetwarzanie")),
            httpx.Response(200, json=status(200, "Sukces", ksefNumber=KSEF_NUMBER)),
        ])
        mock.get(f"/sessions/{SESSION}/invoices/{INVOICE_REF}/upo", name="upo").respond(
            200, content=UPO, headers={"Content-Type": "application/xml"}
        )
        mock.post(f"/sessions/online/{SESSION}/close", name="close").respond(204)
        mock.get(f"/sessions/{SESSION}/invoices", name="list").respond(json={"invoices": []})
        yield mock


def run(client, identity, store, sleeps, *, source=SOURCE, render=lambda: XML, polling=None):
    return submit(
        NUMBER, source, render,
        client=client, identity=identity, store=store, sleep=sleeps.append,
        polling=polling or Polling(timeout_seconds=10, interval_seconds=2),
    )


class TestHappyPath:
    def test_is_accepted_with_a_ksef_number_and_a_stored_upo(self, ksef, client, identity, store, sleeps):
        result = run(client, identity, store, sleeps)
        assert result.status == "accepted"
        assert result.ksef_number == KSEF_NUMBER
        assert store.upo_path(NUMBER).read_bytes() == UPO
        assert store.load(NUMBER) == result
        assert ksef["close"].called

    def test_the_payload_is_what_ksef_can_decrypt_and_check(self, ksef, ksef_key, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        key, _ = ksef_key
        opened = json.loads(ksef["open"].calls.last.request.content)
        assert opened["formCode"] == {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"}
        assert opened["encryption"]["publicKeyId"] == "key-1"
        session_key = crypto.SessionKey(
            crypto.unwrap_key(base64.b64decode(opened["encryption"]["encryptedSymmetricKey"]), key),
            base64.b64decode(opened["encryption"]["initializationVector"]),
        )
        sent = json.loads(ksef["send"].calls.last.request.content)
        ciphertext = base64.b64decode(sent["encryptedInvoiceContent"])
        assert crypto.decrypt(ciphertext, session_key) == XML
        assert sent["invoiceHash"] == crypto.sha256_b64(XML)
        assert sent["invoiceSize"] == len(XML)
        assert sent["encryptedInvoiceHash"] == crypto.sha256_b64(ciphertext)
        assert sent["encryptedInvoiceSize"] == len(ciphertext)

    def test_every_session_call_carries_the_access_token(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        for name in ("open", "send", "status", "upo", "close"):
            assert ksef[name].calls.last.request.headers["Authorization"] == "Bearer access-token"

    def test_tokens_never_reach_the_state_file(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        text = store.path(NUMBER).read_text()
        assert "access-token" not in text and "auth-token" not in text

    def test_the_file_name_is_safe_for_a_number_with_slashes(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        assert store.path(NUMBER).name == "FV_2026_001.json"

    def test_the_exact_bytes_sent_are_kept(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        assert store.xml_path(NUMBER).read_bytes() == XML


class TestTheStateIsWrittenBeforeSending:
    def test_the_record_says_sending_when_the_send_goes_out(self, ksef, client, identity, store, sleeps):
        seen = {}

        def capture(request):
            seen["record"] = store.load(NUMBER)
            return httpx.Response(202, json={"referenceNumber": INVOICE_REF})

        ksef["send"].mock(side_effect=capture)
        run(client, identity, store, sleeps)
        assert seen["record"].status == "sending"
        assert seen["record"].session_reference == SESSION
        assert seen["record"].invoice_reference is None


class TestAnUnansweredSend:
    def test_is_never_retried(self, ksef, client, identity, store, sleeps):
        ksef["send"].mock(side_effect=httpx.ReadTimeout("no answer"))
        with pytest.raises(SubmissionError, match="will not be resent"):
            run(client, identity, store, sleeps)
        assert ksef["send"].call_count == 1
        assert store.load(NUMBER).status == "sending"

    def test_a_rerun_finds_it_by_hash_and_does_not_resend(self, ksef, client, identity, store, sleeps):
        ksef["send"].mock(side_effect=httpx.ReadTimeout("no answer"))
        with pytest.raises(SubmissionError):
            run(client, identity, store, sleeps)

        ksef["list"].respond(json={"invoices": [
            {"referenceNumber": "OTHER", "invoiceHash": "someone-else"},
            {"referenceNumber": INVOICE_REF, "invoiceHash": crypto.sha256_b64(XML)},
        ]})
        result = run(client, identity, store, sleeps, render=pytest.fail)
        assert result.status == "accepted"
        assert result.invoice_reference == INVOICE_REF
        assert ksef["send"].call_count == 1
        assert ksef["open"].call_count == 1

    def test_the_lookup_follows_continuation_pages(self, ksef, client, identity, store, sleeps):
        ksef["send"].mock(side_effect=httpx.ReadTimeout("no answer"))
        with pytest.raises(SubmissionError):
            run(client, identity, store, sleeps)

        ksef["list"].mock(side_effect=[
            httpx.Response(200, json={"invoices": [], "continuationToken": "page-2"}),
            httpx.Response(200, json={"invoices": [
                {"referenceNumber": INVOICE_REF, "invoiceHash": crypto.sha256_b64(XML)},
            ]}),
        ])
        assert run(client, identity, store, sleeps).status == "accepted"
        assert ksef["list"].calls.last.request.headers["x-continuation-token"] == "page-2"

    def test_if_ksef_has_no_trace_of_it_the_run_stops_uncertain(self, ksef, client, identity, store, sleeps):
        ksef["send"].mock(side_effect=httpx.ReadTimeout("no answer"))
        with pytest.raises(SubmissionError):
            run(client, identity, store, sleeps)

        with pytest.raises(SubmissionUncertain, match="has not been resent"):
            run(client, identity, store, sleeps)
        assert ksef["send"].call_count == 1
        assert store.load(NUMBER).status == "sending"

    def test_a_5xx_on_send_is_treated_the_same(self, ksef, client, identity, store, sleeps):
        ksef["send"].respond(503)
        with pytest.raises(SubmissionError, match="will not be resent"):
            run(client, identity, store, sleeps)
        assert ksef["send"].call_count == 1


class TestRefusals:
    def test_a_4xx_on_send_is_a_refused_request_not_a_rejected_invoice(
        self, ksef, client, identity, store, sleeps
    ):
        # KSeF never looked at the invoice: an expired token or a malformed
        # request says nothing about its content.
        ksef["send"].respond(400, json={"exception": "bad"})
        with pytest.raises(SubmissionError, match="refused"):
            run(client, identity, store, sleeps)
        assert store.load(NUMBER).status == "refused"

    def test_a_refused_send_may_be_sent_again(self, ksef, client, identity, store, sleeps):
        ksef["send"].mock(side_effect=[
            httpx.Response(401),
            httpx.Response(202, json={"referenceNumber": INVOICE_REF}),
        ])
        with pytest.raises(SubmissionError):
            run(client, identity, store, sleeps)
        assert run(client, identity, store, sleeps).status == "accepted"
        assert ksef["send"].call_count == 2

    def test_a_rejected_invoice_keeps_the_reason(self, ksef, client, identity, store, sleeps):
        ksef["status"].mock(side_effect=None, return_value=httpx.Response(200, json={"status": {
            "code": 450, "description": "Błąd weryfikacji semantyki dokumentu faktury",
            "details": ["P_13_1 does not match"],
        }}))
        result = run(client, identity, store, sleeps)
        assert result.status == "rejected"
        assert "450" in result.error and "P_13_1" in result.error
        assert not ksef["upo"].called
        assert ksef["close"].called

    def test_refused_authentication_stops_before_any_session(self, ksef, client, identity, store, sleeps):
        ksef.get("/auth/AUTH-1").respond(json=status(450, "Uwierzytelnianie zakończone niepowodzeniem"))
        with pytest.raises(SubmissionError, match="authentication refused"):
            run(client, identity, store, sleeps)
        assert not ksef["open"].called
        assert store.load(NUMBER) is None

    def test_a_failure_before_the_session_opens_leaves_no_record(self, ksef, client, identity, store, sleeps):
        ksef["open"].respond(400)
        with pytest.raises(KsefError):
            run(client, identity, store, sleeps)
        assert store.load(NUMBER) is None


class TestReruns:
    def test_an_accepted_invoice_makes_no_calls(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        calls = len(ksef.calls)
        assert run(client, identity, store, sleeps).status == "accepted"
        assert len(ksef.calls) == calls

    def test_different_content_under_the_same_number_is_a_conflict(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        with pytest.raises(SubmissionConflict):
            run(client, identity, store, sleeps, source="source-hash-2")

    def test_a_poll_timeout_leaves_it_sent_and_a_rerun_resumes_polling(self, ksef, client, identity, store, sleeps):
        ksef["status"].mock(side_effect=None, return_value=httpx.Response(200, json=status(150)))
        result = run(client, identity, store, sleeps, polling=Polling(timeout_seconds=4, interval_seconds=2))
        assert result.status == "sent"
        assert sleeps == [2, 2]
        assert not ksef["close"].called

        ksef["status"].mock(return_value=httpx.Response(200, json=status(200, ksefNumber=KSEF_NUMBER)))
        result = run(client, identity, store, sleeps, render=pytest.fail)
        assert result.status == "accepted"
        assert ksef["send"].call_count == 1


class TestRetries:
    def test_status_polls_are_retried_with_backoff(self, ksef, client, identity, store, sleeps):
        ksef["status"].mock(side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(503),
            httpx.Response(200, json=status(200, ksefNumber=KSEF_NUMBER)),
        ])
        assert run(client, identity, store, sleeps).status == "accepted"
        assert sleeps == [1.0, 2.0]

    def test_retries_give_up_after_the_limit(self, ksef, client, identity, store, sleeps):
        ksef["upo"].respond(503)
        ksef["status"].mock(
            side_effect=None,
            return_value=httpx.Response(200, json=status(200, ksefNumber=KSEF_NUMBER)),
        )
        result = run(client, identity, store, sleeps)
        assert ksef["upo"].call_count == 4  # the first try and three retries
        # Accepted, but without its receipt: still "sent", so a rerun only has
        # to fetch the UPO -- and the KSeF number is already kept.
        assert result.status == "sent"
        assert result.ksef_number == KSEF_NUMBER
        assert "503" in result.error
        assert store.load(NUMBER) == result

    def test_a_4xx_is_not_retried(self, ksef, client, identity, store, sleeps):
        ksef["status"].mock(side_effect=None, return_value=httpx.Response(404))
        with pytest.raises(KsefError) as raised:
            run(client, identity, store, sleeps)
        assert not isinstance(raised.value, TransientError)
        assert ksef["status"].call_count == 1

    def test_a_failed_close_does_not_fail_an_accepted_invoice(self, ksef, client, identity, store, sleeps):
        ksef["close"].respond(400)
        assert run(client, identity, store, sleeps).status == "accepted"


class TestAfterAFailedUpoDownload:
    def test_a_rerun_fetches_the_upo_without_resending(self, ksef, client, identity, store, sleeps):
        ksef["upo"].respond(503)
        ksef["status"].mock(
            side_effect=None,
            return_value=httpx.Response(200, json=status(200, ksefNumber=KSEF_NUMBER)),
        )
        run(client, identity, store, sleeps)

        ksef["upo"].respond(200, content=UPO)
        result = run(client, identity, store, sleeps, render=pytest.fail)
        assert result.status == "accepted"
        assert result.error is None
        assert store.upo_path(NUMBER).read_bytes() == UPO
        assert ksef["send"].call_count == 1


class TestCorrectingARejectedInvoice:
    """A rejected invoice holds no KSeF number, so its number is free again."""

    @pytest.fixture
    def rejected_once(self, ksef):
        ksef["status"].mock(side_effect=[
            httpx.Response(200, json=status(450, "Błąd weryfikacji semantyki")),
            httpx.Response(200, json=status(200, ksefNumber=KSEF_NUMBER)),
        ])
        return ksef

    def test_the_corrected_invoice_is_sent_under_the_same_number(
        self, rejected_once, client, identity, store, sleeps
    ):
        assert run(client, identity, store, sleeps).status == "rejected"
        fixed = b"<Faktura>fixed</Faktura>"
        result = run(client, identity, store, sleeps, source="source-hash-2", render=lambda: fixed)
        assert result.status == "accepted"
        assert result.invoice_hash == crypto.sha256_b64(fixed)
        assert store.xml_path(NUMBER).read_bytes() == fixed
        assert rejected_once["send"].call_count == 2

    def test_the_same_rejected_content_is_not_sent_again(
        self, rejected_once, client, identity, store, sleeps
    ):
        run(client, identity, store, sleeps)
        calls = len(rejected_once.calls)
        assert run(client, identity, store, sleeps).status == "rejected"
        assert len(rejected_once.calls) == calls

    def test_changing_an_invoice_still_in_flight_is_a_conflict(self, ksef, client, identity, store, sleeps):
        ksef["status"].mock(side_effect=None, return_value=httpx.Response(200, json=status(150)))
        run(client, identity, store, sleeps, polling=Polling(timeout_seconds=0))
        assert store.load(NUMBER).status == "sent"
        with pytest.raises(SubmissionConflict):
            run(client, identity, store, sleeps, source="source-hash-2")


class TestTheIdentityThatSent:
    def test_is_recorded(self, ksef, client, identity, store, sleeps):
        assert run(client, identity, store, sleeps).seller_nip == identity.nip

    def test_a_different_identity_cannot_follow_up_and_says_why(
        self, ksef, client, identity, store, sleeps
    ):
        ksef["status"].mock(side_effect=None, return_value=httpx.Response(200, json=status(150)))
        run(client, identity, store, sleeps, polling=Polling(timeout_seconds=0))
        calls = len(ksef.calls)

        other = create_test_identity()
        with pytest.raises(SubmissionError, match=f"sent as NIP {identity.nip}") as raised:
            run(client, other, store, sleeps)
        assert not isinstance(raised.value, SubmissionConflict)
        assert len(ksef.calls) == calls  # stopped before authenticating

    def test_a_finished_invoice_needs_no_identity(self, ksef, client, identity, store, sleeps):
        run(client, identity, store, sleeps)
        assert run(client, create_test_identity(), store, sleeps).status == "accepted"
