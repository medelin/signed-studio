"""A deterministic fake PayPal sandbox, as an ``httpx`` transport: no network, no
credentials. Tests, ``PAYPAL_ADAPTER=mock`` and replay use it in place of
``api-m.sandbox.paypal.com``.

It keeps the same state machine as the real Orders v2 / Payments v2 API, as far as the
studio uses it:

- ``POST /v1/oauth2/token`` (HTTP Basic) → a bearer token with ``expires_in``.
- Orders: ``CREATED`` → (buyer approval, ``FakePayPal.approve``) ``APPROVED`` →
  ``authorize`` → ``COMPLETED`` with one authorization.
- Authorizations: ``CREATED`` → ``capture`` (``CAPTURED``, or ``PARTIALLY_CAPTURED`` when
  not final) / ``void`` (``VOIDED``) / ``reauthorize`` (a new authorization).
- Captures: ``COMPLETED`` → ``refund`` (``PARTIALLY_REFUNDED`` / ``REFUNDED``).
- Every POST must carry ``PayPal-Request-Id``; a repeated key returns the first answer
  byte for byte and changes nothing, as PayPal does.
- PayPal's error shapes (``name``, ``details[].issue``, ``debug_id``) for the refusals the
  studio relies on: not approved, already authorized/captured/voided, capture above the
  authorization, refund above the capture, bad token.

Ids are made deterministically from a counter (17 capital letters and digits, like
PayPal's). ``fail_next`` injects one HTTP error for retry tests.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import httpx

from studio.funding import parse_amount

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"
EXPIRES_IN = 32400


def _pid(prefix: str, n: int) -> str:
    """A 17-character PayPal-like id: ``prefix`` letter, then letters and digits."""
    letters = "".join(_ALPHABET[(n * 7 + i * 3) % len(_ALPHABET)] for i in range(4))
    return f"{prefix}{n:06d}{letters}{(n * 7919) % 1_000_000:06d}"[:17].ljust(17, "X")


def _money(amount: dict[str, Any]) -> tuple[int, str]:
    return parse_amount(amount["value"], amount["currency_code"]), amount["currency_code"]


def _fmt(minor: int, currency: str) -> dict[str, str]:
    from studio.funding import format_amount

    return {"currency_code": currency, "value": format_amount(minor, currency)}


@dataclass
class _Order:
    id: str
    body: dict[str, Any]
    status: str = "CREATED"
    authorization_id: str = ""


@dataclass
class _Auth:
    id: str
    order_id: str
    amount_minor: int
    currency: str
    custom_id: str
    invoice_id: str
    status: str = "CREATED"
    captured_minor: int = 0
    capture_ids: list[str] = field(default_factory=list)


@dataclass
class _Capture:
    id: str
    authorization_id: str
    amount_minor: int
    currency: str
    custom_id: str
    invoice_id: str
    status: str = "COMPLETED"
    refunded_minor: int = 0


class FakePayPal:
    """The fake API. Use ``transport()`` for an ``httpx`` client."""

    def __init__(self, client_id: str = "mock-client",
                 client_secret: str = "mock-secret",  # noqa: S107 - the fake's own value
                 *, approve_on_create: bool = False) -> None:
        self._basic = "Basic " + base64.b64encode(
            f"{client_id}:{client_secret}".encode()).decode()
        self._lock = threading.Lock()
        self._n = 0
        self.tokens: set[str] = set()
        self.orders: dict[str, _Order] = {}
        self.auths: dict[str, _Auth] = {}
        self.captures: dict[str, _Capture] = {}
        self.refunds: dict[str, dict[str, Any]] = {}
        self.idempotent: dict[str, tuple[str, int, bytes]] = {}  # key -> (path, status, body)
        self.calls: list[tuple[str, str]] = []  # (method, path) of every request
        self.approve_on_create = approve_on_create
        self._fail: list[int] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def fail_next(self, *statuses: int) -> None:
        """The next requests (after the token) answer these HTTP statuses, in order."""
        self._fail.extend(statuses)

    def approve(self, order_id: str) -> None:
        """What the buyer's tap in the PayPal popup does."""
        with self._lock:
            order = self.orders[order_id]
            if order.status == "CREATED":
                order.status = "APPROVED"

    def expire_tokens(self) -> None:
        self.tokens.clear()

    # -- dispatch ----------------------------------------------------------------------

    def _next(self, prefix: str) -> str:
        self._n += 1
        return _pid(prefix, self._n)

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            path = request.url.path
            self.calls.append((request.method, path))
            if path == "/v1/oauth2/token":
                return self._token(request)
            if request.headers.get("Authorization", "")[7:] not in self.tokens:
                return _err(401, "AUTHENTICATION_FAILURE", "INVALID_TOKEN")
            if self._fail:
                return _err(self._fail.pop(0), "INTERNAL_SERVER_ERROR", "")
            if request.method == "GET":
                return self._get(path)
            key = request.headers.get("PayPal-Request-Id", "")
            if not key:
                return _err(400, "INVALID_REQUEST", "PAYPAL_REQUEST_ID_REQUIRED")
            seen = self.idempotent.get(key)
            if seen is not None:
                if seen[0] != path:
                    return _err(422, "UNPROCESSABLE_ENTITY", "DUPLICATE_REQUEST_ID")
                return httpx.Response(seen[1], content=seen[2],
                                      headers={"Content-Type": "application/json"})
            body = json.loads(request.content) if request.content else {}
            resp = self._post(path, body)
            if resp.status_code < 500:
                self.idempotent[key] = (path, resp.status_code, resp.content)
            return resp

    def _token(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != self._basic:
            # PayPal's OAuth error shape (not a studio API error code)
            oauth_error = dict(error="invalid_client",
                               error_description="Client Authentication failed")
            return httpx.Response(401, json=oauth_error)
        token = f"A21AA{self._next('T')}"
        self.tokens.add(token)
        return httpx.Response(200, json={"scope": "https://uri.paypal.com/services/payments",
                                         "access_token": token, "token_type": "Bearer",
                                         "app_id": "APP-80W284485P519543T",
                                         "expires_in": EXPIRES_IN, "nonce": "mock"})

    def _get(self, path: str) -> httpx.Response:
        m = re.fullmatch(r"/v2/checkout/orders/([A-Z0-9]+)", path)
        if m and m[1] in self.orders:
            return httpx.Response(200, json=self._order_json(self.orders[m[1]]))
        m = re.fullmatch(r"/v2/payments/(authorizations|captures)/([A-Z0-9]+)", path)
        if m and m[1] == "authorizations" and m[2] in self.auths:
            return httpx.Response(200, json=self._auth_json(self.auths[m[2]]))
        if m and m[1] == "captures" and m[2] in self.captures:
            return httpx.Response(200, json=self._capture_json(self.captures[m[2]]))
        return _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")

    def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        if path == "/v2/checkout/orders":
            return self._create(body)
        m = re.fullmatch(r"/v2/checkout/orders/([A-Z0-9]+)/authorize", path)
        if m:
            return self._authorize(m[1])
        m = re.fullmatch(r"/v2/payments/authorizations/([A-Z0-9]+)/(capture|void|reauthorize)",
                         path)
        if m:
            auth = self.auths.get(m[1])
            if auth is None:
                return _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            return {"capture": self._capture, "void": self._void,
                    "reauthorize": self._reauthorize}[m[2]](auth, body)
        m = re.fullmatch(r"/v2/payments/captures/([A-Z0-9]+)/refund", path)
        if m:
            cap = self.captures.get(m[1])
            if cap is None:
                return _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
            return self._refund(cap, body)
        return _err(404, "RESOURCE_NOT_FOUND", "")

    # -- orders --------------------------------------------------------------------------

    def _create(self, body: dict[str, Any]) -> httpx.Response:
        units = body.get("purchase_units") or []
        if body.get("intent") not in ("AUTHORIZE", "CAPTURE") or len(units) != 1:
            return _err(400, "INVALID_REQUEST", "MALFORMED_REQUEST_JSON")
        unit = units[0]
        total, currency = _money(unit["amount"])
        items = sum(_money(i["unit_amount"])[0] * int(i["quantity"])
                    for i in unit.get("items") or [])
        if items != _money(unit["amount"]["breakdown"]["item_total"])[0] or items != total:
            return _err(422, "UNPROCESSABLE_ENTITY", "ITEM_TOTAL_MISMATCH")
        if len(unit.get("custom_id", "")) > 127:
            return _err(400, "INVALID_REQUEST", "INVALID_STRING_LENGTH")
        order = _Order(self._next("5"), body,
                       "APPROVED" if self.approve_on_create else "CREATED")
        self.orders[order.id] = order
        del currency
        return httpx.Response(201, json=self._order_json(order))

    def _authorize(self, order_id: str) -> httpx.Response:
        order = self.orders.get(order_id)
        if order is None:
            return _err(404, "RESOURCE_NOT_FOUND", "INVALID_RESOURCE_ID")
        if order.status == "CREATED":
            return _err(422, "UNPROCESSABLE_ENTITY", "ORDER_NOT_APPROVED")
        if order.status == "COMPLETED":
            return _err(422, "UNPROCESSABLE_ENTITY", "ORDER_ALREADY_AUTHORIZED")
        unit = order.body["purchase_units"][0]
        minor, currency = _money(unit["amount"])
        auth = _Auth(self._next("0"), order.id, minor, currency, unit.get("custom_id", ""),
                     unit.get("invoice_id", ""))
        self.auths[auth.id] = auth
        order.status, order.authorization_id = "COMPLETED", auth.id
        return httpx.Response(201, json=self._order_json(order))

    # -- authorizations ------------------------------------------------------------------

    def _capture(self, auth: _Auth, body: dict[str, Any]) -> httpx.Response:
        if auth.status == "VOIDED":
            return _err(422, "UNPROCESSABLE_ENTITY", "AUTHORIZATION_VOIDED")
        if auth.status == "CAPTURED":
            return _err(422, "UNPROCESSABLE_ENTITY", "AUTHORIZATION_ALREADY_CAPTURED")
        minor, currency = (_money(body["amount"]) if body.get("amount")
                           else (auth.amount_minor - auth.captured_minor, auth.currency))
        if currency != auth.currency or minor + auth.captured_minor > auth.amount_minor:
            return _err(422, "UNPROCESSABLE_ENTITY", "MAX_CAPTURE_AMOUNT_EXCEEDED")
        cap = _Capture(self._next("3"), auth.id, minor, currency, auth.custom_id,
                       body.get("invoice_id") or auth.invoice_id)
        self.captures[cap.id] = cap
        auth.captured_minor += minor
        auth.capture_ids.append(cap.id)
        final = body.get("final_capture", False) or auth.captured_minor == auth.amount_minor
        auth.status = "CAPTURED" if final else "PARTIALLY_CAPTURED"
        return httpx.Response(201, json=self._capture_json(cap))

    def _void(self, auth: _Auth, body: dict[str, Any]) -> httpx.Response:
        if auth.status == "VOIDED":
            return _err(422, "UNPROCESSABLE_ENTITY", "PREVIOUSLY_VOIDED")
        if auth.status != "CREATED":
            return _err(422, "UNPROCESSABLE_ENTITY", "CANNOT_BE_VOIDED")
        auth.status = "VOIDED"
        return httpx.Response(200, json=self._auth_json(auth))

    def _reauthorize(self, auth: _Auth, body: dict[str, Any]) -> httpx.Response:
        if auth.status != "CREATED":
            return _err(422, "UNPROCESSABLE_ENTITY", "AUTHORIZATION_VOIDED")
        minor, currency = _money(body["amount"])
        if currency != auth.currency or minor > auth.amount_minor:
            return _err(422, "UNPROCESSABLE_ENTITY", "MAX_CAPTURE_AMOUNT_EXCEEDED")
        new = _Auth(self._next("0"), auth.order_id, minor, currency, auth.custom_id,
                    auth.invoice_id)
        self.auths[new.id] = new
        return httpx.Response(201, json=self._auth_json(new))

    # -- captures ------------------------------------------------------------------------

    def _refund(self, cap: _Capture, body: dict[str, Any]) -> httpx.Response:
        if cap.status == "REFUNDED":
            return _err(422, "UNPROCESSABLE_ENTITY", "CAPTURE_FULLY_REFUNDED")
        minor, currency = (_money(body["amount"]) if body.get("amount")
                           else (cap.amount_minor - cap.refunded_minor, cap.currency))
        if currency != cap.currency or minor + cap.refunded_minor > cap.amount_minor:
            return _err(422, "UNPROCESSABLE_ENTITY", "REFUND_AMOUNT_EXCEEDED")
        cap.refunded_minor += minor
        cap.status = ("REFUNDED" if cap.refunded_minor == cap.amount_minor
                      else "PARTIALLY_REFUNDED")
        rid = self._next("1")
        refund = {"id": rid, "status": "COMPLETED", "amount": _fmt(minor, currency),
                  "invoice_id": body.get("invoice_id", ""), "custom_id": cap.custom_id,
                  "links": [{"href": f"https://api.sandbox.paypal.com/v2/payments/refunds/{rid}",
                             "rel": "self", "method": "GET"}]}
        self.refunds[rid] = refund
        return httpx.Response(201, json=refund)

    # -- representations -----------------------------------------------------------------

    def _order_json(self, order: _Order) -> dict[str, Any]:
        unit = dict(order.body["purchase_units"][0])
        unit.setdefault("reference_id", "default")
        if order.authorization_id:
            unit["payments"] = {"authorizations": [self._auth_json(
                self.auths[order.authorization_id])]}
        links = [{"href": f"https://api.sandbox.paypal.com/v2/checkout/orders/{order.id}",
                  "rel": "self", "method": "GET"}]
        if order.status == "CREATED":
            links.append({"href": f"https://www.sandbox.paypal.com/checkoutnow?token={order.id}",
                          "rel": "payer-action", "method": "GET"})
        out: dict[str, Any] = {"id": order.id, "intent": order.body["intent"],
                               "status": order.status, "purchase_units": [unit],
                               "links": links}
        if order.status != "CREATED":
            out["payer"] = {"payer_id": "MOCKPAYER0000", "email_address":
                            "sb-buyer@personal.example.com", "name": {"given_name": "Sandbox",
                                                                      "surname": "Buyer"}}
        return out

    @staticmethod
    def _auth_json(auth: _Auth) -> dict[str, Any]:
        return {"id": auth.id, "status": auth.status,
                "amount": _fmt(auth.amount_minor, auth.currency),
                "custom_id": auth.custom_id, "invoice_id": auth.invoice_id,
                "expiration_time": "2026-12-02T12:00:00Z",
                "links": [{"href": "https://api.sandbox.paypal.com/v2/payments/"
                                   f"authorizations/{auth.id}", "rel": "self",
                           "method": "GET"}]}

    @staticmethod
    def _capture_json(cap: _Capture) -> dict[str, Any]:
        return {"id": cap.id, "status": cap.status, "final_capture": True,
                "amount": _fmt(cap.amount_minor, cap.currency),
                "custom_id": cap.custom_id, "invoice_id": cap.invoice_id,
                "seller_receivable_breakdown": {
                    "gross_amount": _fmt(cap.amount_minor, cap.currency),
                    "paypal_fee": _fmt(cap.amount_minor * 29 // 1000 + 30, cap.currency)},
                "links": [{"href": "https://api.sandbox.paypal.com/v2/payments/"
                                   f"captures/{cap.id}", "rel": "self", "method": "GET"}]}


def _err(status: int, name: str, issue: str) -> httpx.Response:
    body: dict[str, Any] = {"name": name, "message": name.replace("_", " ").lower(),
                            "debug_id": "f00dfeed0001"}
    if issue:
        body["details"] = [{"issue": issue, "description": issue.replace("_", " ").lower()}]
    return httpx.Response(status, json=body)
