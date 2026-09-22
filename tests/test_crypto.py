"""KSeF 2.0 encryption, checked offline against published vectors.

AES has fixed test vectors (NIST SP 800-38A). RSA-OAEP does not -- it is
randomized by design -- so it is checked by round trip, and by showing that the
wrong OAEP hash cannot unwrap it.
"""

import base64
import hashlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from eu_einvoice_bridge.crypto import (
    IV_BYTES,
    KEY_BYTES,
    SessionKey,
    decrypt,
    encrypt,
    new_session_key,
    public_key_from_certificate,
    sha256_b64,
    unwrap_key,
    wrap_key,
)

# NIST SP 800-38A, F.2.5 CBC-AES256.Encrypt, first block.
NIST_KEY = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
NIST_IV = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
NIST_PLAINTEXT = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
NIST_CIPHERTEXT = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd6")


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class TestAes:
    def test_matches_the_nist_vector(self):
        # CBC's first block depends only on the IV and the first plaintext
        # block, so the vector holds even though PKCS#7 appends a padding block.
        ciphertext = encrypt(NIST_PLAINTEXT, SessionKey(NIST_KEY, NIST_IV))
        assert ciphertext[:16] == NIST_CIPHERTEXT

    def test_pkcs7_adds_a_full_block_to_block_aligned_input(self):
        assert len(encrypt(NIST_PLAINTEXT, SessionKey(NIST_KEY, NIST_IV))) == 32

    def test_round_trips_an_invoice_sized_payload(self):
        session = new_session_key()
        invoice = "<Faktura>zażółć gęślą jaźń</Faktura>".encode() * 50
        assert decrypt(encrypt(invoice, session), session) == invoice

    def test_the_wrong_key_does_not_decrypt(self):
        ciphertext = encrypt(b"<Faktura/>", new_session_key())
        with pytest.raises(ValueError):
            decrypt(ciphertext, new_session_key())


class TestSessionKey:
    def test_sizes_are_those_ksef_requires(self):
        session = new_session_key()
        assert (len(session.key), len(session.iv)) == (KEY_BYTES, IV_BYTES) == (32, 16)

    def test_each_session_gets_a_fresh_key(self):
        assert new_session_key().key != new_session_key().key

    def test_wrong_sizes_are_refused(self):
        with pytest.raises(ValueError):
            SessionKey(b"short", NIST_IV)

    def test_the_key_never_appears_in_its_repr(self):
        session = SessionKey(NIST_KEY, NIST_IV)
        assert NIST_KEY.hex() not in repr(session)
        assert "redacted" in repr(session)


class TestRsaOaep:
    def test_round_trips(self, rsa_key):
        key = new_session_key().key
        assert unwrap_key(wrap_key(key, rsa_key.public_key()), rsa_key) == key

    def test_output_is_one_rsa_block(self, rsa_key):
        assert len(wrap_key(new_session_key().key, rsa_key.public_key())) == 256

    def test_is_randomized(self, rsa_key):
        key = new_session_key().key
        assert wrap_key(key, rsa_key.public_key()) != wrap_key(key, rsa_key.public_key())

    def test_sha1_oaep_cannot_unwrap_it(self, rsa_key):
        # Proves SHA-256 is really in use, not the library default of SHA-1.
        wrapped = wrap_key(new_session_key().key, rsa_key.public_key())
        sha1 = padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None)
        with pytest.raises(ValueError):
            rsa_key.decrypt(wrapped, sha1)

    def test_public_key_is_read_from_a_base64_der_certificate(self, rsa_key):
        import datetime as dt

        from cryptography.x509.oid import NameOID

        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
        now = dt.datetime.now(dt.UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(rsa_key.public_key())
            .serial_number(1)
            .not_valid_before(now)
            .not_valid_after(now + dt.timedelta(days=1))
            .sign(rsa_key, hashes.SHA256())
        )
        der_b64 = base64.b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()
        key = public_key_from_certificate(der_b64)
        assert key.public_numbers() == rsa_key.public_key().public_numbers()


class TestDigest:
    def test_is_base64_sha256(self):
        expected = base64.b64encode(hashlib.sha256(b"abc").digest()).decode()
        assert sha256_b64(b"abc") == expected == "ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0="
