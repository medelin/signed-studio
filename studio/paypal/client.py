"""PayPal REST client over ``httpx``; no SDK. Sandbox only.

- Base URL: ``studio.paypal.SANDBOX_BASE``, a constant. The client has no base-URL
  parameter, so no setting can point it at the live API.
- Auth: OAuth 2.0 client credentials (``POST /v1/oauth2/token``, HTTP Basic with the
  client id and secret). The access token is cached until ``TOKEN_MARGIN_S`` seconds
  before its ``expires_in``. A 401 drops the cached token and the call is sent once more.
- Every POST carries ``PayPal-Request-Id`` (PayPal's idempotency key, at most 108
  characters): a retry with the same key returns the first result instead of acting
  twice. Callers derive the key from what authorises the step (``request_id``), so a
  retried step, a restarted process or a double click cannot charge twice.
- ``Prefer: return=representation`` on every call, so a write answers with the full object.
- Retries: HTTP 429/5xx and network errors are retried (``RETRIES`` times, backoff). POSTs
  are safe to retry because of the request id; a 4xx other than 401/429 is final.
- Errors: ``PayPalError`` carries the HTTP status, PayPal's error ``name``, the first issue
  code and a readable reason (``REASONS``), plus ``debug_id`` for PayPal support. It never
  carries the response body, a token, the client secret or payer data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from studio.paypal import SANDBOX_BASE

TOKEN_PATH = "/v1/oauth2/token"  # noqa: S105 - a URL path, not a secret
TOKEN_MARGIN_S = 60.0
TRANSIENT = frozenset({429, 500, 502, 503, 504})
RETRIES = 3
REQUEST_ID_MAX = 108
_PATH_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
_ISSUE = re.compile(r"[A-Z0-9_]{1,80}")

# PayPal issue codes → what went wrong in words an owner can act on.
# Sandbox negative testing (``PayPal-Mock-Response``): the codes the studio will ask PayPal
# to simulate, on a capture only. Anything else is refused before a request is made.
MOCK_CODES: frozenset[str] = frozenset({"INSTRUMENT_DECLINED", "TRANSACTION_REFUSED"})

REASONS: dict[str, str] = {
    "ORDER_NOT_APPROVED": "the buyer has not approved the order in PayPal yet",
    "ORDER_ALREADY_AUTHORIZED": "the order was already authorized",
    "ORDER_ALREADY_CAPTURED": "the order was already captured",
    "ORDER_EXPIRED": "the order expired before it was authorized",
    "INSTRUMENT_DECLINED": "PayPal declined the buyer's funding source",
    "PAYER_ACTION_REQUIRED": "the buyer must take an action in PayPal first",
    "AUTHORIZATION_ALREADY_CAPTURED": "the authorization was already captured",
    "AUTHORIZATION_VOIDED": "the authorization was voided",
    "AUTHORIZATION_EXPIRED": "the authorization expired",
    "PREVIOUSLY_VOIDED": "the authorization was already voided",
    "CANNOT_BE_VOIDED": "the authorization can no longer be voided",
    "MAX_CAPTURE_AMOUNT_EXCEEDED": "the capture is larger than the authorized amount",
    "MAX_NUMBER_OF_REFUNDS_EXCEEDED": "no more refunds are allowed on this capture",
    "REFUND_AMOUNT_EXCEEDED": "the refund is larger than what is left of the capture",
    "CAPTURE_FULLY_REFUNDED": "the capture was already fully refunded",
    "REAUTHORIZATION_TOO_SOON": "a reauthorization is allowed only after the honour period",
    "DUPLICATE_INVOICE_ID": "PayPal already has a payment with this invoice id",
    "CURRENCY_NOT_SUPPORTED": "PayPal does not support this currency here",
    "AMOUNT_MISMATCH": "the order amount does not add up to its items",
    "ITEM_TOTAL_MISMATCH": "the item total does not match the items",
    "PERMISSION_DENIED": "the PayPal app is not allowed to do this",
}


# Answers that do not say whether the request was acted on: another attempt with the same
# key is still in flight, or PayPal reports a duplicate (an earlier attempt may have won).
AMBIGUOUS_STATUS = frozenset({409})
AMBIGUOUS_ISSUES = frozenset({"DUPLICATE_REQUEST_ID", "PREVIOUS_REQUEST_IN_PROGRESS",
                              "RESOURCE_CONFLICT", "DUPLICATE_INVOICE_ID",
                              "DUPLICATE_TRANSACTION"})


class PayPalError(Exception):
    """A PayPal call failed. ``transient`` when sending it again may succeed (429, 5xx,
    network). ``sent`` when any attempt of this call may have reached PayPal; ``unknown``
    when PayPal may have acted on it: any attempt timed out after sending, answered 5xx,
    or was answered 409 / a duplicate or in-progress issue. Both are carried across the
    client's retries, so a final definite-looking 4xx after an ambiguous attempt is still
    ``unknown``. Retry only with the same request id to learn the outcome."""

    def __init__(self, status: int, name: str = "", issue: str = "", *,
                 debug_id: str = "", transient: bool = False, unknown: bool = False,
                 sent: bool = True) -> None:
        self.status = status
        self.sent = sent
        self.name = name
        self.issue = issue
        self.debug_id = debug_id
        self.transient = transient
        self.unknown = unknown
        self.reason = REASONS.get(issue, "")
        what = " ".join(p for p in (name, issue) if p) or "no PayPal error name"
        super().__init__(f"PayPal HTTP {status}: {what}"
                         + (f" ({self.reason})" if self.reason else "")
                         + (f" [debug_id {debug_id}]" if debug_id else ""))


def request_id(*parts: str) -> str:
    """A ``PayPal-Request-Id`` from what authorises a step (e.g. funding id, step and
    approval id): the same parts always give the same key, different parts never do."""
    if not parts or not all(isinstance(p, str) and p for p in parts):
        raise ValueError("a request id needs non-empty parts")
    return "ss-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:48]


def path_id(value: str) -> str:
    """A PayPal resource id safe to put in a URL path; ``ValueError`` otherwise."""
    if not isinstance(value, str) or not _PATH_ID.fullmatch(value):
        raise ValueError("not a PayPal resource id")
    return value


@dataclass(frozen=True)
class Reply:
    """A PayPal answer: the parsed JSON, the raw bytes' SHA-256 and the HTTP status."""

    status: int
    json: dict[str, Any]
    body_sha256: str


class PayPalClient:
    """One sandbox REST app. Thread-safe token cache; one ``httpx.Client``."""

    base = SANDBOX_BASE

    def __init__(self, client_id: str, client_secret: str, *,
                 transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 30.0) -> None:
        if not (client_id and client_secret):
            raise ValueError("the PayPal client needs a client id and a client secret")
        self._basic = "Basic " + base64.b64encode(
            f"{client_id}:{client_secret}".encode()).decode()
        self._token: tuple[str, float] | None = None  # (access token, expires on clock)
        self._lock = threading.Lock()
        self._clock = clock
        self._sleep = sleep
        self._http = httpx.Client(base_url=SANDBOX_BASE, timeout=timeout, transport=transport)

    def __repr__(self) -> str:
        return f"PayPalClient(base={SANDBOX_BASE!r})"

    def close(self) -> None:
        self._http.close()

    # -- OAuth ---------------------------------------------------------------------------

    def access_token(self) -> str:
        with self._lock:
            now = self._clock()
            if self._token is not None and now < self._token[1]:
                return self._token[0]
            resp = self._send("POST", TOKEN_PATH, headers={
                "Authorization": self._basic, "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"},
                content=b"grant_type=client_credentials")
            if resp.status_code != 200:
                raise _error(resp)
            data = _json(resp)
            token, ttl = data.get("access_token"), data.get("expires_in")
            if not isinstance(token, str) or not token or not isinstance(ttl, int):
                raise PayPalError(resp.status_code, "INVALID_TOKEN_RESPONSE")
            self._token = (token, now + max(0.0, ttl - TOKEN_MARGIN_S))
            return token

    def _drop_token(self, token: str) -> None:
        with self._lock:
            if self._token is not None and self._token[0] == token:
                self._token = None

    # -- calls ---------------------------------------------------------------------------

    def get(self, path: str, *, retries: int = RETRIES,
            timeout: float | None = None) -> Reply:
        """A GET; ``timeout`` (seconds) overrides the client's for this call only."""
        return self._call("GET", path, None, None, retries=retries, timeout=timeout)

    def delete(self, path: str) -> Reply:
        """A DELETE (idempotent by nature: deleting a deleted vault token is a 404)."""
        return self._call("DELETE", path, None, None)

    def post(self, path: str, body: Mapping[str, Any] | None, *, request_id: str,
             mock_response: str | None = None) -> Reply:
        """A write. ``request_id`` (``PayPal-Request-Id``) is required: without it a retry
        could act twice. ``mock_response`` (a ``MOCK_CODES`` member) asks the sandbox to
        simulate that refusal (``PayPal-Mock-Response``); any other value is a ``ValueError``
        before anything is sent."""
        if not request_id or len(request_id) > REQUEST_ID_MAX:
            raise ValueError(f"a PayPal write needs a PayPal-Request-Id of 1-{REQUEST_ID_MAX} "
                             "characters")
        if mock_response is not None and (mock_response not in MOCK_CODES
                                          or not path.endswith("/capture")):
            raise ValueError("a PayPal mock response is an allowed code on a capture only")
        return self._call("POST", path, {} if body is None else dict(body), request_id,
                          mock_response=mock_response)

    def _call(self, method: str, path: str, body: dict[str, Any] | None,
              rid: str | None, *, mock_response: str | None = None,
              retries: int = RETRIES, timeout: float | None = None) -> Reply:
        content = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        refreshed = False
        attempt = 0
        sent = ambiguous = False  # over every attempt of this call

        def fail(err: PayPalError) -> PayPalError:
            err.sent = sent or err.sent
            err.unknown = err.unknown or ambiguous
            return err

        while True:
            try:
                token = self.access_token()  # nothing of the call itself is sent yet
            except PayPalError as exc:
                raise fail(PayPalError(exc.status, exc.name, exc.issue,
                                       debug_id=exc.debug_id, transient=exc.transient,
                                       sent=False)) from None
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
                       "Prefer": "return=representation"}
            if content is not None:
                headers["Content-Type"] = "application/json"
            if rid:
                headers["PayPal-Request-Id"] = rid
            if mock_response:
                headers["PayPal-Mock-Response"] = json.dumps(
                    {"mock_application_codes": mock_response}, separators=(",", ":"))
            try:
                resp = self._send(method, path, headers=headers, content=content,
                                  timeout=timeout)
            except PayPalError as exc:
                sent = sent or exc.sent
                ambiguous = ambiguous or exc.unknown
                if attempt < retries:
                    attempt += 1
                    self._sleep(0.5 * 2 ** attempt)
                    continue
                raise fail(exc) from None
            sent = True
            if method == "POST" and (resp.status_code >= 500
                                     or resp.status_code in AMBIGUOUS_STATUS):
                ambiguous = True
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self._drop_token(token)
                continue
            if resp.status_code in TRANSIENT and attempt < retries:
                attempt += 1
                self._sleep(_retry_after(resp, 0.5 * 2 ** attempt))
                continue
            if resp.status_code >= 400:
                err = _error(resp)
                if method == "POST" and (err.status in AMBIGUOUS_STATUS
                                         or err.issue in AMBIGUOUS_ISSUES):
                    err.unknown = True
                raise fail(err)
            return Reply(resp.status_code, _json(resp),
                         hashlib.sha256(resp.content).hexdigest())

    def _send(self, method: str, path: str, *, headers: Mapping[str, str],
              content: bytes | None, timeout: float | None = None) -> httpx.Response:
        extra = {} if timeout is None else {"timeout": timeout}
        try:
            return self._http.request(method, path, headers=dict(headers), content=content,
                                      **extra)
        except httpx.TransportError as exc:
            sent = not isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
            raise PayPalError(0, "NETWORK_ERROR", transient=True,
                              unknown=sent and method == "POST", sent=sent) from None


def _json(resp: httpx.Response) -> dict[str, Any]:
    if not resp.content:
        return {}
    try:
        data = resp.json()
    except ValueError:
        raise PayPalError(resp.status_code, "INVALID_JSON",
                          unknown=resp.status_code < 400) from None
    if not isinstance(data, dict):
        raise PayPalError(resp.status_code, "INVALID_JSON", unknown=resp.status_code < 400)
    return data


def _retry_after(resp: httpx.Response, default: float) -> float:
    try:
        return min(30.0, max(0.0, float(resp.headers.get("Retry-After", default))))
    except ValueError:
        return default


def _error(resp: httpx.Response) -> PayPalError:
    """The error name, first issue and debug id from a PayPal error body; nothing else."""
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    name = data.get("name") or data.get("error") or ""
    name = name.upper() if isinstance(name, str) else ""
    details = data.get("details")
    issue = ""
    if isinstance(details, list) and details and isinstance(details[0], dict):
        issue = details[0].get("issue") or ""
    debug = data.get("debug_id") or ""
    return PayPalError(
        resp.status_code,
        name if _ISSUE.fullmatch(name) else "",
        issue if isinstance(issue, str) and _ISSUE.fullmatch(issue) else "",
        debug_id=debug if isinstance(debug, str) and _PATH_ID.fullmatch(debug) else "",
        transient=resp.status_code in TRANSIENT)
