"""A thin client for the KSeF 2.0 endpoints this project uses.

The one rule it enforces: only idempotent operations are retried. Asking for a
status or downloading a UPO twice is harmless; sending an invoice twice is not,
and opening a session or redeeming a one-time token twice either wastes a
resource or fails. Those raise on a transient failure and leave the decision to
the caller -- which, for a send, is to query rather than resend.

Endpoint paths are those of the published OpenAPI document, whose server is
https://api-test.ksef.mf.gov.pl/v2.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

TEST_BASE_URL = "https://api-test.ksef.mf.gov.pl/v2"

# FA(3) as KSeF names it when a session is opened; matches KodFormularza.
FA3_FORM_CODE = {"systemCode": "FA (3)", "schemaVersion": "1-0E", "value": "FA"}


class KsefError(Exception):
    """KSeF refused the request, or it failed in a way retrying will not fix."""

    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TransientError(KsefError):
    """A timeout, a dropped connection, 429 or a 5xx: it may work later.

    retry_after is the server's Retry-After in seconds, when it gave one.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        body: str = "",
        retry_after: float | None = None,
    ):
        super().__init__(message, status_code, body)
        self.retry_after = retry_after


# The longest Retry-After this waits out. A longer one gives up instead: the
# caller's state lets a later run carry on, which beats a silent long pause.
MAX_RETRY_AFTER_SECONDS = 60.0


def _is_transient(response: httpx.Response) -> bool:
    return response.status_code == 429 or response.status_code >= 500


def _retry_after(response: httpx.Response) -> float | None:
    """Retry-After in its delay-seconds form.

    The HTTP-date form is not read: it depends on agreeing clocks, and falling
    back to the usual backoff is harmless.
    """
    value = response.headers.get("Retry-After", "").strip()
    return float(value) if value.isdigit() else None


class KsefClient:
    def __init__(
        self,
        http: httpx.Client,
        *,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = http
        self._retries = retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise TransientError(f"{method} {path}: {exc.__class__.__name__}") from exc
        if _is_transient(response):
            raise TransientError(
                f"{method} {path}: HTTP {response.status_code}",
                response.status_code,
                response.text,
                retry_after=_retry_after(response),
            )
        if response.status_code >= 400:
            raise KsefError(
                f"{method} {path}: HTTP {response.status_code}",
                response.status_code,
                response.text,
            )
        return response

    def _idempotent(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Retried with exponential backoff; only for requests safe to repeat.

        A Retry-After from the server (sent with 429) replaces the backoff when
        it is longer, up to MAX_RETRY_AFTER_SECONDS.
        """
        for attempt in range(self._retries + 1):
            try:
                return self._send(method, path, **kwargs)
            except TransientError as exc:
                if attempt == self._retries:
                    raise
                delay = self._backoff * 2**attempt
                if exc.retry_after is not None:
                    if exc.retry_after > MAX_RETRY_AFTER_SECONDS:
                        raise
                    delay = max(delay, exc.retry_after)
                self._sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # -- authentication -------------------------------------------------------

    def challenge(self) -> str:
        # A new challenge each time, with nothing else changed: safe to repeat.
        return self._idempotent("POST", "/auth/challenge").json()["challenge"]

    def submit_xades(self, signed_request: bytes) -> tuple[str, str]:
        """Returns (reference number, temporary authentication token)."""
        body = self._send(
            "POST",
            "/auth/xades-signature",
            content=signed_request,
            headers={"Content-Type": "application/xml"},
        ).json()
        return body["referenceNumber"], body["authenticationToken"]["token"]

    def auth_status(self, reference: str, auth_token: str) -> dict[str, Any]:
        response = self._idempotent("GET", f"/auth/{reference}", headers=self._bearer(auth_token))
        return response.json()["status"]

    def redeem(self, auth_token: str) -> str:
        """One-time: a second call with the same token is refused, so no retry."""
        body = self._send("POST", "/auth/token/redeem", headers=self._bearer(auth_token)).json()
        return body["accessToken"]["token"]

    # -- encryption keys ------------------------------------------------------

    def symmetric_key_certificate(self) -> dict[str, Any]:
        certificates = self._idempotent("GET", "/security/public-key-certificates").json()
        for certificate in certificates:
            if "SymmetricKeyEncryption" in certificate.get("usage", []):
                return certificate
        raise KsefError("KSeF published no certificate for symmetric key encryption")

    # -- interactive session --------------------------------------------------

    def open_online_session(
        self, access_token: str, encrypted_key_b64: str, iv_b64: str, public_key_id: str | None
    ) -> str:
        encryption = {"encryptedSymmetricKey": encrypted_key_b64, "initializationVector": iv_b64}
        if public_key_id:
            encryption["publicKeyId"] = public_key_id
        body = self._send(
            "POST",
            "/sessions/online",
            json={"formCode": FA3_FORM_CODE, "encryption": encryption},
            headers=self._bearer(access_token),
        ).json()
        return body["referenceNumber"]

    def send_invoice(self, session: str, access_token: str, payload: dict[str, Any]) -> str:
        """Never retried: an unanswered send may still have arrived."""
        body = self._send(
            "POST",
            f"/sessions/online/{session}/invoices",
            json=payload,
            headers=self._bearer(access_token),
        ).json()
        return body["referenceNumber"]

    def invoice_status(self, session: str, invoice: str, access_token: str) -> dict[str, Any]:
        return self._idempotent(
            "GET", f"/sessions/{session}/invoices/{invoice}", headers=self._bearer(access_token)
        ).json()

    def session_invoices(self, session: str, access_token: str) -> list[dict[str, Any]]:
        """Every invoice in the session, following continuation tokens.

        Used to recover after an unanswered send, so stopping at the first page
        could miss the very invoice being looked for.
        """
        invoices: list[dict[str, Any]] = []
        continuation: str | None = None
        while True:
            headers = self._bearer(access_token)
            if continuation:
                headers["x-continuation-token"] = continuation
            body = self._idempotent(
                "GET", f"/sessions/{session}/invoices", headers=headers
            ).json()
            invoices.extend(body["invoices"])
            continuation = body.get("continuationToken")
            if not continuation:
                return invoices

    def download_upo(self, session: str, invoice: str, access_token: str) -> bytes:
        return self._idempotent(
            "GET", f"/sessions/{session}/invoices/{invoice}/upo", headers=self._bearer(access_token)
        ).content

    def close_session(self, session: str, access_token: str) -> None:
        self._send("POST", f"/sessions/online/{session}/close", headers=self._bearer(access_token))
