"""End to end against Poland's real KSeF TEST environment.

Skipped by default (see pyproject.toml); run with:

    pytest -m integration

Each run makes a fresh random identity and a random buyer, because TEST data
is shared between integrators. TEST is down for maintenance 16:00-18:00
Warsaw time, and a failure then says nothing about this code.
"""

import datetime as dt
import hashlib
import json
import secrets
from pathlib import Path

import httpx
import pytest
from lxml import etree

from eu_einvoice_bridge.fa3 import to_fa3
from eu_einvoice_bridge.ksef import (
    TEST_BASE_URL,
    KsefClient,
    Polling,
    StateStore,
    create_test_identity,
    random_test_nip,
    submit,
)
from eu_einvoice_bridge.model import Invoice

pytestmark = pytest.mark.integration

EXAMPLE = Path(__file__).parent.parent / "examples" / "invoice-fa3.json"
UPO_NS = "http://upo.schematy.mf.gov.pl/KSeF/v4-3"


def test_an_invoice_is_accepted_and_its_upo_retrieved(tmp_path):
    identity = create_test_identity()
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["number"] = f"LIVE/{secrets.token_hex(4)}"
    data["seller"]["vat_id"] = f"PL{identity.nip}"
    data["buyer"]["vat_id"] = f"PL{random_test_nip()}"
    invoice = Invoice.model_validate(data)
    xml = to_fa3(invoice, generated_at=dt.datetime.now(dt.UTC))

    store = StateStore(tmp_path, clock=lambda: dt.datetime.now(dt.UTC))
    with httpx.Client(base_url=TEST_BASE_URL, timeout=30.0) as http:
        client = KsefClient(http)
        record = submit(
            invoice.number,
            hashlib.sha256(invoice.model_dump_json().encode()).hexdigest(),
            lambda: xml,
            client=client,
            identity=identity,
            store=store,
            polling=Polling(timeout_seconds=180),
        )
        assert record.status == "accepted", record.error
        assert record.ksef_number.startswith(identity.nip)

        upo = etree.fromstring(Path(record.upo_path).read_bytes())
        assert etree.QName(upo).namespace == UPO_NS

        # A rerun is answered from the record, with no second submission.
        again = submit(
            invoice.number,
            hashlib.sha256(invoice.model_dump_json().encode()).hexdigest(),
            lambda: pytest.fail("a finished submission must not be rendered again"),
            client=client,
            identity=identity,
            store=store,
        )
        assert again == record
