"""The encryption KSeF 2.0 requires of every invoice, as pure functions.

No network here, deliberately: encryption can then be checked offline against
known vectors, and an encryption bug can never masquerade as a connection one.

KSeF's scheme: a fresh AES-256 key and 128-bit IV per session; each invoice
encrypted with AES-256-CBC and PKCS#7 padding; the key itself wrapped with the
Ministry's RSA public key using OAEP with SHA-256 and MGF1-SHA-256.
"""

import base64
import hashlib
import secrets
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import padding as block_padding
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

KEY_BYTES = 32  # AES-256
IV_BYTES = 16  # one AES block
_BLOCK_BITS = 128


@dataclass(frozen=True, slots=True, repr=False)
class SessionKey:
    """The symmetric key and IV for one KSeF session.

    repr is suppressed so the key cannot end up in a log or a traceback.
    """

    key: bytes
    iv: bytes

    def __post_init__(self):
        if len(self.key) != KEY_BYTES or len(self.iv) != IV_BYTES:
            raise ValueError("a session key is 32 bytes of key and 16 bytes of IV")

    def __repr__(self) -> str:
        return "SessionKey(<redacted>)"


def new_session_key() -> SessionKey:
    """A fresh key per session, from the OS's cryptographic random source."""
    return SessionKey(secrets.token_bytes(KEY_BYTES), secrets.token_bytes(IV_BYTES))


def encrypt(plaintext: bytes, session: SessionKey) -> bytes:
    padder = block_padding.PKCS7(_BLOCK_BITS).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(session.key), modes.CBC(session.iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt(ciphertext: bytes, session: SessionKey) -> bytes:
    decryptor = Cipher(algorithms.AES(session.key), modes.CBC(session.iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = block_padding.PKCS7(_BLOCK_BITS).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


_OAEP_SHA256 = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA256()),
    algorithm=hashes.SHA256(),
    label=None,
)


def wrap_key(key: bytes, public_key: rsa.RSAPublicKey) -> bytes:
    """RSA-OAEP with SHA-256 for both the hash and MGF1, as KSeF specifies."""
    return public_key.encrypt(key, _OAEP_SHA256)


def unwrap_key(wrapped: bytes, private_key: rsa.RSAPrivateKey) -> bytes:
    return private_key.decrypt(wrapped, _OAEP_SHA256)


def public_key_from_certificate(certificate_b64: str) -> rsa.RSAPublicKey:
    """The Ministry publishes its keys as base64 DER certificates."""
    certificate = x509.load_der_x509_certificate(base64.b64decode(certificate_b64))
    key = certificate.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        raise ValueError("expected an RSA public key for symmetric key encryption")
    return key


def sha256_b64(data: bytes) -> str:
    """The digest format KSeF uses for invoiceHash and encryptedInvoiceHash."""
    return base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
