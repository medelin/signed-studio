"""Billing with Stripe: a monthly subscription, Stripe's invoices, mails about payments.

- **Configured only by environment**: ``STRIPE_SECRET_KEY`` (a test key ``sk_test_`` or a
  restricted ``rk_test_``; a live key only with ``STUDIO_BILLING_LIVE=1``),
  ``STRIPE_WEBHOOK_SECRET`` (``whsec_``) and ``STRIPE_PRICE_ID`` (the monthly price). Any
  missing = billing off and no billing UI.
- **Checkout** (``POST /account/billing/checkout``): a Stripe Checkout Session in
  subscription mode for the signed-in account (``client_reference_id`` and metadata carry
  the account id; an existing customer is reused). **Portal** (``POST
  /account/billing/portal``): Stripe's customer portal, where the owner changes the card,
  cancels and downloads every invoice. Stripe issues the invoices (the seller's invoice
  settings: name, address, footer); the studio stores only ids, status and dates.
- **Webhook** (``POST /webhooks/stripe``): the ``Stripe-Signature`` header is checked here
  (HMAC-SHA256 over ``"{t}.{body}"``, any ``v1``, at most ``TOLERANCE_S`` old) before the
  body is parsed; each event id is applied once. Handled: ``checkout.session.completed``,
  ``customer.subscription.created|updated|deleted``, ``invoice.paid``,
  ``invoice.payment_failed``. A ``stripe.webhook`` receipt records the event type and id
  (no address, no amount).
- **Tier**: with a *live* key an active (or trialing / past-due) subscription lifts the
  account to ``full`` and its end puts it back on ``trial``. With a test key the status is
  shown and mailed but the tier never changes: a test card must never unlock the operator's
  live ad account.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from studio.approvals import transaction

API = "https://api.stripe.com/v1"
TIMEOUT_S = 15.0
TOLERANCE_S = 300
RUN_ID = "billing"
LIFTING = frozenset({"active", "trialing", "past_due"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS billing (
    account_id TEXT PRIMARY KEY REFERENCES accounts (id) ON DELETE CASCADE,
    customer_id TEXT NOT NULL DEFAULT '',
    subscription_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',        -- Stripe's subscription status
    period_end REAL NOT NULL DEFAULT 0,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    last_invoice_url TEXT NOT NULL DEFAULT '',  -- Stripe's hosted invoice page
    livemode INTEGER NOT NULL DEFAULT 0,
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stripe_events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    received_ts REAL NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@dataclass(frozen=True)
class BillingConfig:
    secret_key: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    price_id: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.secret_key and self.webhook_secret and self.price_id)

    @property
    def live(self) -> bool:
        return self.secret_key.startswith(("sk_live_", "rk_live_"))

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> BillingConfig:
        """Raises ``ValueError`` naming the variable."""
        key = e.get("STRIPE_SECRET_KEY", "").strip()
        hook = e.get("STRIPE_WEBHOOK_SECRET", "").strip()
        price = e.get("STRIPE_PRICE_ID", "").strip()
        if not (key or hook or price):
            return cls()
        if not key.startswith(("sk_test_", "rk_test_", "sk_live_", "rk_live_")):
            raise ValueError("STRIPE_SECRET_KEY must be a Stripe secret or restricted key")
        if key.startswith(("sk_live_", "rk_live_")) and e.get("STUDIO_BILLING_LIVE") != "1":
            raise ValueError("STRIPE_SECRET_KEY is a live key: set STUDIO_BILLING_LIVE=1 "
                             "to take real payments")
        if not hook.startswith("whsec_"):
            raise ValueError("STRIPE_WEBHOOK_SECRET must be the endpoint's whsec_ secret")
        if not price.startswith("price_"):
            raise ValueError("STRIPE_PRICE_ID must be a price id (price_…)")
        return cls(key, hook, price)


class BillingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _form(params: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's bracketed form encoding (``a[b][0][c]=…``)."""
    out: list[tuple[str, str]] = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, Mapping):
            out += _form(value, name)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                out += _form(item, f"{name}[{i}]") if isinstance(item, Mapping) \
                    else [(f"{name}[{i}]", str(item))]
        elif value is not None and value != "":
            out.append((name, "true" if value is True else "false" if value is False
                        else str(value)))
    return out


class StripeApi:
    def __init__(self, cfg: BillingConfig, transport: httpx.BaseTransport | None = None
                 ) -> None:
        self._cfg = cfg
        self._transport = transport

    def post(self, path: str, params: Mapping[str, Any], *, idempotency: str = ""
             ) -> dict[str, Any]:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT_S,
                              trust_env=False) as c:
                r = c.post(API + path, content=urlencode(_form(params)),
                           auth=(self._cfg.secret_key, ""), headers=headers)
        except httpx.HTTPError:
            raise BillingError("stripe_unreachable") from None
        if r.status_code != 200:
            code = ""
            try:
                code = str(r.json().get("error", {}).get("code") or "")
            except ValueError:
                pass
            raise BillingError(f"stripe_{r.status_code}{'_' + code if code else ''}")
        return dict(r.json())

    def checkout(self, *, account_id: str, email: str, customer_id: str, success_url: str,
                 cancel_url: str, lang: str) -> str:
        params: dict[str, Any] = {
            "mode": "subscription", "client_reference_id": account_id,
            "line_items": [{"price": self._cfg.price_id, "quantity": 1}],
            "success_url": success_url, "cancel_url": cancel_url,
            "metadata": {"account_id": account_id},
            "subscription_data": {"metadata": {"account_id": account_id}},
            "locale": "pl" if lang == "pl" else "en",
        }
        if customer_id:
            params["customer"] = customer_id
        elif email:
            params["customer_email"] = email
        session = self.post("/checkout/sessions", params)
        return str(session["url"])

    def portal(self, *, customer_id: str, return_url: str) -> str:
        return str(self.post("/billing_portal/sessions",
                             {"customer": customer_id, "return_url": return_url})["url"])


def verify_signature(body: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Stripe's scheme: ``t=<unix>,v1=<hex>[,v1=…]``; HMAC-SHA256 of ``"{t}.{body}"``."""
    if not header or not secret:
        return False
    stamp, sigs = "", []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            stamp = value
        elif key == "v1":
            sigs.append(value)
    if not stamp.isdigit() or not sigs:
        return False
    now = time.time() if now is None else now
    if abs(now - int(stamp)) > TOLERANCE_S:
        return False
    want = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in sigs)


@dataclass(frozen=True)
class Billing:
    status: str
    period_end: float
    cancel_at_period_end: bool
    customer_id: str
    last_invoice_url: str
    livemode: bool


def of(conn: sqlite3.Connection, account_id: str) -> Billing | None:
    row = conn.execute("SELECT status, period_end, cancel_at_period_end, customer_id, "
                       "last_invoice_url, livemode FROM billing WHERE account_id = ?",
                       (account_id,)).fetchone()
    if row is None:
        return None
    return Billing(row[0], row[1], bool(row[2]), row[3], row[4], bool(row[5]))


def _period_end(sub: Mapping[str, Any]) -> float:
    end = sub.get("current_period_end")
    if not end:  # newer API versions keep it on the subscription items
        items = (sub.get("items") or {}).get("data") or []
        end = items[0].get("current_period_end") if items else 0
    return float(end or 0)


@dataclass
class Applied:
    account_id: str = ""
    mail: str = ""  # a studio.notify event for the account, or ""
    tier: str = ""  # "full" / "trial" when the tier should change
    email: str = ""  # an address Checkout collected (kept when the profile has none)
    duplicate: bool = False


def apply(conn: sqlite3.Connection, event: Mapping[str, Any], *, live_key: bool,
          now: float | None = None) -> Applied:
    """Record one verified event (once per id); what the caller should do next."""
    now = time.time() if now is None else now
    out = Applied()
    obj = (event.get("data") or {}).get("object") or {}
    kind = str(event.get("type") or "")
    livemode = bool(event.get("livemode"))
    with transaction(conn) as c:
        try:
            c.execute("INSERT INTO stripe_events (id, type, received_ts) VALUES (?, ?, ?)",
                      (str(event.get("id") or ""), kind, now))
        except sqlite3.IntegrityError:
            out.duplicate = True
            return out

        def account_for(customer: str, meta: Mapping[str, Any] | None) -> str:
            aid = str((meta or {}).get("account_id") or "")
            if not aid and customer:
                row = c.execute("SELECT account_id FROM billing WHERE customer_id = ?",
                                (customer,)).fetchone()
                aid = row[0] if row else ""
            exists = c.execute("SELECT 1 FROM accounts WHERE id = ? AND status = 'active'",
                               (aid,)).fetchone()
            return aid if exists else ""

        def upsert(aid: str, **cols: Any) -> None:
            c.execute("INSERT INTO billing (account_id, updated_ts, livemode) VALUES (?, ?, ?) "
                      "ON CONFLICT (account_id) DO NOTHING", (aid, now, int(livemode)))
            sets = ", ".join(f"{k} = ?" for k in cols)
            c.execute(f"UPDATE billing SET {sets}, updated_ts = ?, livemode = ? "  # noqa: S608
                      "WHERE account_id = ?", (*cols.values(), now, int(livemode), aid))

        if kind == "checkout.session.completed":
            aid = account_for(str(obj.get("customer") or ""),
                              obj.get("metadata") or {"account_id":
                                                      obj.get("client_reference_id")})
            if aid:
                upsert(aid, customer_id=str(obj.get("customer") or ""),
                       subscription_id=str(obj.get("subscription") or ""))
                out.account_id = aid
                out.email = str((obj.get("customer_details") or {}).get("email") or "")
        elif kind.startswith("customer.subscription."):
            aid = account_for(str(obj.get("customer") or ""), obj.get("metadata"))
            if aid:
                status = "canceled" if kind.endswith(".deleted") else str(obj.get("status"))
                old = c.execute("SELECT status FROM billing WHERE account_id = ?",
                                (aid,)).fetchone()
                upsert(aid, customer_id=str(obj.get("customer") or ""),
                       subscription_id=str(obj.get("id") or ""), status=status,
                       period_end=_period_end(obj),
                       cancel_at_period_end=int(bool(obj.get("cancel_at_period_end"))))
                out.account_id = aid
                was = old[0] if old else ""
                if status in LIFTING and was not in LIFTING:
                    out.mail = "subscription_started"
                elif status not in LIFTING and was in LIFTING:
                    out.mail = "subscription_ended"
                if live_key and livemode:
                    out.tier = "full" if status in LIFTING else "trial"
        elif kind in ("invoice.paid", "invoice.payment_failed"):
            aid = account_for(str(obj.get("customer") or ""),
                              ((obj.get("parent") or {}).get("subscription_details") or {})
                              .get("metadata") or obj.get("subscription_details",
                                                          {}).get("metadata"))
            if aid:
                if kind == "invoice.paid":
                    upsert(aid, last_invoice_url=str(obj.get("hosted_invoice_url") or ""))
                out.account_id = aid
                out.mail = "payment_received" if kind == "invoice.paid" else "payment_failed"
    return out


def event_of(body: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("object") == "event" else None
