"""The self-signed test identity and the XAdES signature it produces.

The signature is verified here with signxml's own verifier against the
certificate -- the same check KSeF TEST performs, minus its trust store.
"""

import stat

import pytest
from cryptography import x509
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml.xades import XAdESVerifier

from eu_einvoice_bridge.ksef.identity import (
    AUTH_NS,
    create_test_identity,
    load_identity,
    nip_checksum_ok,
    random_test_nip,
    save_identity,
    sign_auth_request,
)


@pytest.fixture(scope="module")
def identity():
    return create_test_identity()


class TestNip:
    @pytest.mark.parametrize("nip", ["5260250274", "7740001454", "5270103391"])
    def test_published_company_nips_pass(self, nip):
        assert nip_checksum_ok(nip)

    @pytest.mark.parametrize("nip", ["5260250275", "123", "52602502740", "52602502a4"])
    def test_malformed_or_miscounted_nips_fail(self, nip):
        assert not nip_checksum_ok(nip)

    def test_random_nips_are_valid_and_fa3_shaped(self):
        for _ in range(200):
            nip = random_test_nip()
            assert nip_checksum_ok(nip)
            assert nip[0] != "0"
            assert nip[1:3] != "00"

    def test_an_invalid_nip_is_refused(self):
        with pytest.raises(ValueError):
            create_test_identity("5260250275")


class TestCertificate:
    def test_is_shaped_like_a_seal_for_the_nip(self, identity):
        certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
        subject = certificate.subject
        assert subject.get_attributes_for_oid(NameOID.ORGANIZATION_IDENTIFIER)[0].value == (
            f"VATPL-{identity.nip}"
        )
        assert subject.get_attributes_for_oid(NameOID.COUNTRY_NAME)[0].value == "PL"
        assert certificate.issuer == subject  # self-signed: TEST only

    def test_the_key_never_appears_in_its_repr(self, identity):
        assert "PRIVATE" not in repr(identity)
        assert identity.nip in repr(identity)


class TestStorage:
    def test_round_trips_and_reads_the_nip_back_from_the_certificate(self, identity, tmp_path):
        save_identity(identity, tmp_path)
        assert load_identity(tmp_path) == identity

    def test_the_private_key_is_readable_by_its_owner_only(self, identity, tmp_path):
        save_identity(identity, tmp_path)
        mode = stat.S_IMODE((tmp_path / "test-identity.key.pem").stat().st_mode)
        assert mode == 0o600

    def test_a_missing_identity_loads_as_none(self, tmp_path):
        assert load_identity(tmp_path) is None


class TestSignature:
    def test_verifies_against_the_certificate(self, identity):
        signed = sign_auth_request("20260921-CR-ABC", identity)
        # Three references: the request itself (enveloped), the XAdES signed
        # properties, and the key info.
        document, *_ = XAdESVerifier().verify(
            signed, x509_cert=identity.certificate_pem, expect_references=3
        )
        assert document.signed_xml.tag == f"{{{AUTH_NS}}}AuthTokenRequest"
        assert document.signed_xml.findtext(f"{{{AUTH_NS}}}Challenge") == "20260921-CR-ABC"

    def test_uses_rsa_sha256_and_sha256_digests(self, identity):
        root = etree.fromstring(sign_auth_request("c", identity))
        ds = "http://www.w3.org/2000/09/xmldsig#"
        method = root.find(f".//{{{ds}}}SignatureMethod").get("Algorithm")
        digests = {e.get("Algorithm") for e in root.iter(f"{{{ds}}}DigestMethod")}
        assert method == "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
        assert digests == {"http://www.w3.org/2001/04/xmlenc#sha256"}

    def test_names_the_nip_as_context(self, identity):
        root = etree.fromstring(sign_auth_request("c", identity))
        assert root.findtext(f"{{{AUTH_NS}}}ContextIdentifier/{{{AUTH_NS}}}Nip") == identity.nip
        assert root.findtext(f"{{{AUTH_NS}}}SubjectIdentifierType") == "certificateSubject"

    def test_a_tampered_challenge_fails_verification(self, identity):
        signed = sign_auth_request("original", identity).replace(b"original", b"tampered")
        with pytest.raises(Exception):
            XAdESVerifier().verify(signed, x509_cert=identity.certificate_pem, expect_references=3)
