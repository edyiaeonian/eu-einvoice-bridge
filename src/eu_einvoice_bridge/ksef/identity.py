"""A self-signed test identity, and XAdES authentication with it.

KSeF's TEST environment accepts self-signed certificates -- and only TEST does.
That is what lets this project authenticate with no human in the loop: no
qualified certificate, no trusted profile, no token minted in a web portal
first. The certificate is shaped like a company seal, with the NIP in
organizationIdentifier as "VATPL-<NIP>", which is how KSeF reads a seal.

TEST data is shared between integrators, so the documentation asks for random
NIPs rather than well-known ones. random_test_nip() makes them.
"""

import datetime as dt
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml.xades import XAdESSigner

AUTH_NS = "http://ksef.mf.gov.pl/auth/token/2.0"

# The NIP checksum: weighted digits mod 11, where a remainder of 10 is invalid.
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)

_KEY_FILE = "test-identity.key.pem"
_CERT_FILE = "test-identity.cert.pem"


class IdentityError(ValueError):
    """The stored test identity cannot be used."""


def nip_checksum_ok(nip: str) -> bool:
    if len(nip) != 10 or not nip.isdigit():
        return False
    digits = [int(c) for c in nip]
    remainder = sum(d * w for d, w in zip(digits, _NIP_WEIGHTS, strict=False)) % 11
    return remainder != 10 and remainder == digits[9]


def random_test_nip() -> str:
    """A random NIP with a valid checksum that FA(3)'s NIP pattern accepts."""
    while True:
        digits = [secrets.randbelow(9) + 1] + [secrets.randbelow(10) for _ in range(8)]
        if digits[1] == 0 and digits[2] == 0:
            continue  # FA(3) forbids a NIP whose second and third digits are both 0
        remainder = sum(d * w for d, w in zip(digits, _NIP_WEIGHTS, strict=False)) % 11
        if remainder != 10:
            return "".join(map(str, digits + [remainder]))


@dataclass(frozen=True, repr=False)
class TestIdentity:
    """A NIP and the self-signed seal that authenticates as it in KSeF TEST."""

    __test__ = False  # not a pytest test class, despite the name

    nip: str
    private_key_pem: bytes
    certificate_pem: bytes

    def __repr__(self) -> str:
        return f"TestIdentity(nip={self.nip!r}, <key redacted>)"


def create_test_identity(nip: str | None = None) -> TestIdentity:
    nip = nip or random_test_nip()
    if not nip_checksum_ok(nip):
        raise ValueError(f"{nip!r} is not a valid NIP")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "EU E-Invoice Bridge test seal"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "EU E-Invoice Bridge (KSeF TEST)"),
        x509.NameAttribute(NameOID.ORGANIZATION_IDENTIFIER, f"VATPL-{nip}"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
    ])
    now = dt.datetime.now(dt.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return TestIdentity(
        nip=nip,
        private_key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        certificate_pem=certificate.public_bytes(serialization.Encoding.PEM),
    )


def save_identity(identity: TestIdentity, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Created owner-only, so the key is never readable by others -- not even
    # between writing it and a chmod afterwards. fchmod covers a file that
    # already existed, whose mode O_CREAT would leave alone.
    fd = os.open(directory / _KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as key_file:
        os.fchmod(key_file.fileno(), 0o600)
        key_file.write(identity.private_key_pem)
    (directory / _CERT_FILE).write_bytes(identity.certificate_pem)


def load_identity(directory: Path, now: dt.datetime | None = None) -> TestIdentity | None:
    """The identity stored in directory, None if there is none.

    An expired certificate is refused here, by name: KSeF would only answer
    with a bare 4xx during authentication, which says nothing about why.
    """
    key_path, cert_path = directory / _KEY_FILE, directory / _CERT_FILE
    if not (key_path.exists() and cert_path.exists()):
        return None
    certificate_pem = cert_path.read_bytes()
    certificate = x509.load_pem_x509_certificate(certificate_pem)
    identifier = certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATION_IDENTIFIER)
    value = identifier[0].value if identifier else None
    if not isinstance(value, str) or not value.startswith("VATPL-"):
        raise IdentityError(f"{cert_path} has no VATPL- organizationIdentifier")
    expires = certificate.not_valid_after_utc
    if expires <= (now or dt.datetime.now(dt.UTC)):
        raise IdentityError(
            f"the test identity in {directory}/ expired on {expires:%Y-%m-%d}; move it "
            f"aside (or pass another --identity-dir) and a new one will be created. "
            f"The new one has a different NIP, so invoices still in flight under "
            f"NIP {value.removeprefix('VATPL-')} can only be followed up with the old one"
        )
    return TestIdentity(
        nip=value.removeprefix("VATPL-"),
        private_key_pem=key_path.read_bytes(),
        certificate_pem=certificate_pem,
    )


def auth_token_request(challenge: str, nip: str) -> etree._Element:
    """AuthTokenRequest: authenticate in the context of this NIP, with the
    identity read from the signing certificate's subject."""
    root = etree.Element(f"{{{AUTH_NS}}}AuthTokenRequest", nsmap={None: AUTH_NS})
    etree.SubElement(root, f"{{{AUTH_NS}}}Challenge").text = challenge
    context = etree.SubElement(root, f"{{{AUTH_NS}}}ContextIdentifier")
    etree.SubElement(context, f"{{{AUTH_NS}}}Nip").text = nip
    etree.SubElement(root, f"{{{AUTH_NS}}}SubjectIdentifierType").text = "certificateSubject"
    return root


def sign_auth_request(challenge: str, identity: TestIdentity) -> bytes:
    """An enveloped XAdES-BES signature, RSA-SHA256 over SHA-256 digests."""
    signer = XAdESSigner(signature_algorithm="rsa-sha256", digest_algorithm="sha256")
    signed = signer.sign(
        auth_token_request(challenge, identity.nip),
        key=identity.private_key_pem,
        cert=identity.certificate_pem.decode("ascii"),
    )
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8")
