"""einvoice submit: the checks it runs before anything leaves the machine.

KSeF itself is replaced by a fake submit(); the flow behind it is tested in
test_ksef_submit.py, and against the real sandbox in test_ksef_live.py.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eu_einvoice_bridge import ksef
from eu_einvoice_bridge.cli import main
from eu_einvoice_bridge.ksef import (
    Submission,
    SubmissionUncertain,
    create_test_identity,
    save_identity,
)

EXAMPLE = Path(__file__).parent.parent / "examples" / "invoice-fa3.json"


@pytest.fixture(scope="module")
def identity():
    return create_test_identity()


@pytest.fixture
def workdir(tmp_path, identity):
    save_identity(identity, tmp_path / "certs")
    return tmp_path


@pytest.fixture
def sent(monkeypatch):
    """Records what would have gone to KSeF and answers with `result`."""
    calls = []
    result = {"value": None}

    def fake_submit(number, source_hash, render, **kwargs):
        calls.append({"number": number, "source_hash": source_hash, "xml": render(), **kwargs})
        if isinstance(result["value"], Exception):
            raise result["value"]
        return result["value"]

    monkeypatch.setattr(ksef, "submit", fake_submit)
    return calls, result


def invoice_file(tmp_path, **changes):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    for key, value in changes.items():
        data[key] = value
    path = tmp_path / "invoice.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def argv(workdir, path, *extra):
    return [
        "submit", str(path),
        "--state-dir", str(workdir / "state"),
        "--identity-dir", str(workdir / "certs"),
        *extra,
    ]


def accepted(number="FV/2026/002"):
    return Submission(
        invoice_number=number, source_hash="s", invoice_hash="h", status="accepted",
        ksef_number="1234567890-20260921-ABCDEF000000-01", upo_path="state/FV_2026_002.upo.xml",
    )


def test_a_seller_other_than_the_identity_is_refused(workdir, sent, capsys):
    calls, _ = sent
    assert main(argv(workdir, invoice_file(workdir))) == 1
    assert "--test-seller" in capsys.readouterr().err
    assert calls == []


def test_test_seller_substitutes_the_identity_nip(workdir, sent, identity, capsys):
    calls, result = sent
    result["value"] = accepted()
    assert main(argv(workdir, invoice_file(workdir), "--test-seller")) == 0
    assert f"<NIP>{identity.nip}</NIP>".encode() in calls[0]["xml"]
    out = capsys.readouterr().out
    assert "accepted by KSeF TEST" in out
    assert "1234567890-20260921-ABCDEF000000-01" in out


def test_an_invalid_invoice_is_never_submitted(workdir, sent, capsys):
    calls, _ = sent
    path = invoice_file(workdir, currency="XYZ")
    assert main(argv(workdir, path, "--test-seller")) == 1
    assert calls == []


def test_an_fa3_mismatch_is_never_submitted(workdir, sent, capsys):
    calls, _ = sent
    # Valid in the neutral model, but FA(3) needs the Polish declarations.
    path = invoice_file(workdir, extras=None)
    assert main(argv(workdir, path, "--test-seller")) == 1
    assert "FA3-NO-DECLARATIONS" in capsys.readouterr().out
    assert calls == []


def test_a_missing_identity_is_created_and_announced(tmp_path, sent, capsys):
    calls, result = sent
    result["value"] = accepted()
    assert main(argv(tmp_path, invoice_file(tmp_path), "--test-seller")) == 0
    assert (tmp_path / "certs" / "test-identity.key.pem").exists()
    assert "created a self-signed KSeF TEST identity" in capsys.readouterr().err


def test_the_same_input_gives_the_same_source_hash(workdir, sent):
    calls, result = sent
    result["value"] = accepted()
    path = invoice_file(workdir)
    main(argv(workdir, path, "--test-seller"))
    main(argv(workdir, path, "--test-seller"))
    assert calls[0]["source_hash"] == calls[1]["source_hash"]


def test_a_rejection_exits_1_with_the_reason(workdir, sent, capsys):
    _, result = sent
    result["value"] = Submission(
        invoice_number="FV/2026/002", source_hash="s", invoice_hash="h",
        status="rejected", error="450 Błąd weryfikacji semantyki",
    )
    assert main(argv(workdir, invoice_file(workdir), "--test-seller")) == 1
    assert "450" in capsys.readouterr().out


def test_still_processing_exits_3(workdir, sent, capsys):
    _, result = sent
    result["value"] = Submission(
        invoice_number="FV/2026/002", source_hash="s", invoice_hash="h",
        status="sent", session_reference="SESSION",
    )
    assert main(argv(workdir, invoice_file(workdir), "--test-seller")) == 3
    assert "run the same command again" in capsys.readouterr().err


def test_a_failed_submission_exits_4(workdir, sent, capsys):
    _, result = sent
    result["value"] = SubmissionUncertain("sent without an answer; it has not been resent")
    assert main(argv(workdir, invoice_file(workdir), "--test-seller")) == 4
    assert "not been resent" in capsys.readouterr().err


def test_an_expired_identity_exits_4_and_says_so(tmp_path, sent, capsys, monkeypatch):
    save_identity(create_test_identity(), tmp_path / "certs")
    real_load = ksef.load_identity
    later = datetime.now(UTC) + timedelta(days=400)
    monkeypatch.setattr(ksef, "load_identity", lambda directory: real_load(directory, now=later))
    calls, _ = sent
    assert main(argv(tmp_path, invoice_file(tmp_path), "--test-seller")) == 4
    assert "expired on" in capsys.readouterr().err
    assert not calls


def test_an_unwritable_state_dir_exits_4_without_a_traceback(workdir, sent, capsys):
    _, result = sent
    result["value"] = PermissionError(13, "Permission denied", str(workdir / "state"))
    assert main(argv(workdir, invoice_file(workdir), "--test-seller")) == 4
    err = capsys.readouterr().err
    assert "Permission denied" in err and "state" in err


def test_the_source_hash_does_not_depend_on_the_test_identity(tmp_path, sent):
    # --test-seller rewrites the seller; a new identity must not make the same
    # invoice look like different content.
    calls, result = sent
    result["value"] = accepted()
    path = invoice_file(tmp_path)
    for name in ("first", "second"):
        main(["submit", str(path), "--state-dir", str(tmp_path / "state"),
              "--identity-dir", str(tmp_path / name), "--test-seller"])
    assert calls[0]["xml"] != calls[1]["xml"]  # different seller NIP in the XML
    assert calls[0]["source_hash"] == calls[1]["source_hash"]


def test_accepted_without_a_upo_exits_3_not_4(workdir, sent, capsys):
    _, result = sent
    result["value"] = Submission(
        invoice_number="FV/2026/002", source_hash="s", invoice_hash="h", status="sent",
        session_reference="SESSION", ksef_number="1234567890-20260921-ABCDEF000000-01",
        error="GET /upo: HTTP 503",
    )
    assert main(argv(workdir, invoice_file(workdir), "--test-seller")) == 3
    err = capsys.readouterr().err
    assert "1234567890-20260921-ABCDEF000000-01" in err
    assert "fetch it" in err
