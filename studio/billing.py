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
  ``invoice.payment_failed`` and ``invoice.upcoming`` (a renewal reminder mail, once per
  renewal, for a 3-, 6- or 12-month plan). A ``stripe.webhook`` receipt records the event
  type and id (no address, no amount).
- **Plans** (``PLANS``): monthly (``STRIPE_PRICE_ID``), 3 / 6 / 12 months billed upfront
  (``STRIPE_PRICE_3M|6M|12M``) and a one-off month (``STRIPE_PRICE_ONCE``, Checkout in
  ``mode=payment``; the completed session sets ``access_until`` = paid + ``ONCE_DAYS``).
  An unset id hides the plan. A company buyer gets Stripe's tax id collection.
- **Ad-spend fee** (optional): ``STRIPE_SPEND_PRICE_ID`` (a metered price, 1 minor unit
  per unit, on the Billing Meter ``STRIPE_SPEND_METER_EVENT``) and ``STUDIO_SPEND_FEE_PCT``
  (default 5; 0 = none). Checkout adds the metered item to a subscription;
  ``report_spend`` (``studio billing report-usage``, daily) sends each account's fee for
  one day as one meter event. Not yet billed for the one-off month (see the docs).
- **Tier**: with a *live* key an active (or trialing / past-due) subscription lifts the
  account to ``full`` and its end puts it back on ``trial``. With a test key the status is
  shown and mailed but the tier never changes: a test card must never unlock the operator's
  live ad account.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import math
import re
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from studio.approvals import transaction

API = "https://api.stripe.com/v1"
TIMEOUT_S = 15.0
TOLERANCE_S = 300
RUN_ID = "billing"
LIFTING = frozenset({"active", "trialing", "past_due"})
DEFAULT_METER_EVENT = "muse_ad_spend"
DEFAULT_FEE_PCT = Decimal(5)
# plan → months per period (``once``: one payment for ONCE_DAYS days, no renewal)
PLANS = ("monthly", "3m", "6m", "12m", "once")
PLAN_MONTHS = {"monthly": 1, "3m": 3, "6m": 6, "12m": 12, "once": 1}
PLAN_ENV = {"3m": "STRIPE_PRICE_3M", "6m": "STRIPE_PRICE_6M", "12m": "STRIPE_PRICE_12M",
            "once": "STRIPE_PRICE_ONCE"}
ONCE_DAYS = 30
BUYERS = ("person", "company")
DESCRIPTIONS = {"monthly": "Muse Studio Lite — monthly subscription",
                "3m": "Muse Studio Lite — 3-month plan, renews every 3 months",
                "6m": "Muse Studio Lite — 6-month plan, renews every 6 months",
                "12m": "Muse Studio Lite — 12-month plan, renews every 12 months"}

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
CREATE TABLE IF NOT EXISTS renewal_reminders (  -- invoice.upcoming mails, once each
    key TEXT PRIMARY KEY,                       -- subscription id : renewal time
    sent_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spend_usage (    -- one meter event per account and day (UTC)
    account_id TEXT NOT NULL,
    day TEXT NOT NULL,                      -- YYYY-MM-DD
    spend_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    fee_minor INTEGER NOT NULL,
    sent_ts REAL NOT NULL,
    PRIMARY KEY (account_id, day)
);
"""


# columns added after the table was first created (the studio.db migration pattern)
COLUMNS = {"access_until": "REAL NOT NULL DEFAULT 0"}  # a one-off payment's access end


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(billing)")}
    for name, decl in COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE billing ADD COLUMN {name} {decl}")


@dataclass(frozen=True)
class BillingConfig:
    secret_key: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    price_id: str = ""
    # the ad-spend fee: a metered price (1 minor unit per unit) on a Stripe Billing Meter
    spend_price_id: str = ""
    meter_event: str = DEFAULT_METER_EVENT
    fee_pct: Decimal = DEFAULT_FEE_PCT
    live_allowed: bool = False  # STUDIO_BILLING_LIVE=1
    # further plans: plan → price id (STRIPE_PRICE_3M / _6M / _12M / _ONCE); unset = hidden
    plan_prices: Mapping[str, str] = field(default_factory=dict)

    @property
    def plans(self) -> dict[str, str]:
        """plan → price id, only the configured ones, in ``PLANS`` order."""
        if not self.enabled:
            return {}
        ids = {"monthly": self.price_id, **self.plan_prices}
        return {p: ids[p] for p in PLANS if ids.get(p)}

    @property
    def enabled(self) -> bool:
        return bool(self.secret_key and self.webhook_secret and self.price_id)

    @property
    def live(self) -> bool:
        return self.secret_key.startswith(("sk_live_", "rk_live_"))

    @property
    def spend_fee(self) -> bool:
        """The ad-spend fee is charged (and stated on every page): billing on, a metered
        price set and a percentage above 0."""
        return self.enabled and bool(self.spend_price_id) and self.fee_pct > 0

    @property
    def fee_pct_text(self) -> str:
        """``5`` or ``2.5`` (never ``5.0``)."""
        return format(self.fee_pct.normalize(), "f")

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> BillingConfig:
        """Raises ``ValueError`` naming the variable."""
        key = e.get("STRIPE_SECRET_KEY", "").strip()
        hook = e.get("STRIPE_WEBHOOK_SECRET", "").strip()
        price = e.get("STRIPE_PRICE_ID", "").strip()
        spend_price = e.get("STRIPE_SPEND_PRICE_ID", "").strip()
        meter = e.get("STRIPE_SPEND_METER_EVENT", "").strip() or DEFAULT_METER_EVENT
        try:
            pct = Decimal(e.get("STUDIO_SPEND_FEE_PCT", "").strip() or DEFAULT_FEE_PCT)
        except InvalidOperation:
            raise ValueError("STUDIO_SPEND_FEE_PCT must be a number (5 = 5 %)") from None
        if not pct.is_finite() or not 0 <= pct <= 50:
            raise ValueError("STUDIO_SPEND_FEE_PCT must be 0-50")
        if spend_price and not spend_price.startswith("price_"):
            raise ValueError("STRIPE_SPEND_PRICE_ID must be a price id (price_…)")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", meter):
            raise ValueError("STRIPE_SPEND_METER_EVENT must be a meter's event name")
        if not (key or hook or price):
            if spend_price:
                raise ValueError("STRIPE_SPEND_PRICE_ID needs the subscription settings "
                                 "(STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID)")
            return cls(fee_pct=pct)
        if not key.startswith(("sk_test_", "rk_test_", "sk_live_", "rk_live_")):
            raise ValueError("STRIPE_SECRET_KEY must be a Stripe secret or restricted key")
        if key.startswith(("sk_live_", "rk_live_")) and e.get("STUDIO_BILLING_LIVE") != "1":
            raise ValueError("STRIPE_SECRET_KEY is a live key: set STUDIO_BILLING_LIVE=1 "
                             "to take real payments")
        if not hook.startswith("whsec_"):
            raise ValueError("STRIPE_WEBHOOK_SECRET must be the endpoint's whsec_ secret")
        if not price.startswith("price_"):
            raise ValueError("STRIPE_PRICE_ID must be a price id (price_…)")
        plans = {plan: e.get(var, "").strip() for plan, var in PLAN_ENV.items()}
        for plan, value in plans.items():
            if value and not value.startswith("price_"):
                raise ValueError(f"{PLAN_ENV[plan]} must be a price id (price_…)")
        return cls(key, hook, price, spend_price, meter, pct,
                   live_allowed=e.get("STUDIO_BILLING_LIVE") == "1",
                   plan_prices={k: v for k, v in plans.items() if v})


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
        return self._send("POST", path, content=urlencode(_form(params)), headers=headers)

    def get(self, path: str) -> dict[str, Any]:
        return self._send("GET", path)

    def _send(self, method: str, path: str, *, content: str | None = None,
              headers: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            with httpx.Client(transport=self._transport, timeout=TIMEOUT_S,
                              trust_env=False) as c:
                r = c.request(method, API + path, content=content,
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
        try:
            data = r.json()
        except ValueError:
            raise BillingError("stripe_invalid_json") from None
        if not isinstance(data, dict):
            raise BillingError("stripe_invalid_json")
        return data

    def price(self, price_id: str = "") -> dict[str, Any]:
        """A price (default: the monthly one): ``unit_amount``, ``currency``, ``recurring``,
        ``currency_options``, …"""
        return self.get("/prices/" + quote(price_id or self._cfg.price_id, safe="") +
                        "?expand[]=currency_options")

    def checkout(self, *, account_id: str, email: str, customer_id: str, success_url: str,
                 cancel_url: str, lang: str, consent_text: str = "", plan: str = "monthly",
                 buyer: str = "person", tax_id: str = "") -> str:
        """A Checkout Session for ``plan``: a subscription (the plan's price, plus the
        metered ad-spend fee when it is charged) or, for ``once``, a single payment with an
        invoice. A company buyer gets Stripe's tax id collection, and its name and address
        go on the customer, so the invoice carries them."""
        price_id = self._cfg.plans.get(plan, "")
        if not price_id:
            raise BillingError("unknown_plan")
        meta = {"account_id": account_id, "plan": plan, "buyer": buyer,
                "buyer_tax_id": tax_id if buyer == "company" else ""}
        params: dict[str, Any] = {
            "mode": "payment" if plan == "once" else "subscription",
            "client_reference_id": account_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url, "cancel_url": cancel_url,
            "metadata": meta, "locale": "pl" if lang == "pl" else "en",
        }
        if plan == "once":
            params["invoice_creation"] = {"enabled": True, "invoice_data": {
                "description": f"Muse Studio Lite — {ONCE_DAYS} days of access, paid once",
                "metadata": meta}}
            if not customer_id:
                params["customer_creation"] = "always"
        else:
            if self._cfg.spend_fee:  # metered: Stripe refuses a quantity on it
                params["line_items"].append({"price": self._cfg.spend_price_id})
            params["subscription_data"] = {"metadata": meta,
                                           "description": DESCRIPTIONS.get(plan, "")}
        if consent_text:  # shown above Stripe's pay button (Stripe allows 1200 characters)
            params["custom_text"] = {"submit": {"message": consent_text[:1200]}}
        if buyer == "company":
            params["tax_id_collection"] = {"enabled": True}
            if customer_id:  # Stripe needs these to save the collected name and tax id
                params["customer_update"] = {"name": "auto", "address": "auto"}
            params["billing_address_collection"] = "required"
        if customer_id:
            params["customer"] = customer_id
        elif email:
            params["customer_email"] = email
        session = self.post("/checkout/sessions", params)
        return str(session["url"])

    def portal(self, *, customer_id: str, return_url: str) -> str:
        return str(self.post("/billing_portal/sessions",
                             {"customer": customer_id, "return_url": return_url})["url"])

    @property
    def config(self) -> BillingConfig:
        return self._cfg

    def meter_event(self, *, customer_id: str, value: int, identifier: str) -> None:
        """One Billing Meter event; ``identifier`` makes a resend a no-op at Stripe."""
        if self._cfg.live and not self._cfg.live_allowed:
            raise BillingError("live_key_without_STUDIO_BILLING_LIVE")
        self.post("/billing/meter_events", {
            "event_name": self._cfg.meter_event, "identifier": identifier,
            "payload": {"stripe_customer_id": customer_id, "value": value}},
            idempotency=identifier)


def _signed_campaigns(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """campaign id → (account id, ad account currency): the account whose session
    registered the key that signed the campaign's ``campaign.create`` (the latest one)."""
    rows = conn.execute(
        "SELECT c.id, s.account_id, c.currency FROM campaigns c "
        "JOIN approvals a ON a.item_id = c.id AND a.action = 'campaign.create' "
        "AND a.status = 'approved' "
        "JOIN keys k ON k.approver = a.approver "
        "JOIN account_sessions s ON s.sid_sha256 = k.session_sha256 "
        "ORDER BY a.decided_ts").fetchall()
    return {cid: (aid, cur) for cid, aid, cur in rows}


def _day_spend(conn: sqlite3.Connection, campaign_id: str, day: str) -> float | None:
    """The campaign's spend on ``day`` from its latest insights snapshot that has that day
    (``spend_by_day``, studio.meta.measurement), in account-currency units; None when no
    snapshot covers it. One value per campaign and day: never summed across snapshots."""
    rows = conn.execute("SELECT body FROM insights_snapshots WHERE campaign_id = ? "
                        "ORDER BY created_ts DESC", (campaign_id,)).fetchall()
    for (body,) in rows:
        try:
            days = json.loads(body).get("spend_by_day") or {}
        except (ValueError, AttributeError):
            continue
        if isinstance(days, Mapping) and day in days:
            try:
                value = float(days[day])
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value > 0 else 0.0
    return None


def fee_of(spend_minor: int, pct: Decimal) -> int:
    """``round(spend × pct / 100)``, halves up, in the same minor units."""
    return int((Decimal(spend_minor) * pct / 100).quantize(Decimal(1), ROUND_HALF_UP))


def report_spend(conn: sqlite3.Connection, api: StripeApi, day: str, *, ledger: Any
                 ) -> dict[str, int]:
    """Send yesterday's (``day``, UTC ``YYYY-MM-DD``) ad-spend fee to Stripe: per account
    with a Stripe customer, the spend of the campaigns it signed on that day, the fee
    (``STUDIO_SPEND_FEE_PCT``) in the ad account's minor units, and one meter event with
    the identifier ``account_id:day`` (sent once; Stripe also drops a duplicate). A
    ``billing.usage`` receipt per account and day records the numbers (no names)."""
    from studio.meta.currency import offset

    dt.date.fromisoformat(day)  # ValueError on anything else
    cfg = api.config
    if not cfg.spend_fee:
        return {"accounts": 0, "sent": 0, "skipped": 0}
    if cfg.live and not cfg.live_allowed:
        raise BillingError("live_key_without_STUDIO_BILLING_LIVE")
    with conn.lock:  # type: ignore[attr-defined]
        campaigns = _signed_campaigns(conn)
        per: dict[str, dict[str, Any]] = {}
        for cid, (aid, currency) in campaigns.items():
            spend = _day_spend(conn, cid, day)
            if spend is None:
                continue
            row = per.setdefault(aid, {"minor": {}, "campaigns": 0})
            row["minor"][currency] = row["minor"].get(currency, 0) + int(
                (Decimal(str(spend)) * offset(currency)).quantize(Decimal(1), ROUND_HALF_UP))
            row["campaigns"] += 1
        customers = {aid: tuple(conn.execute(
            "SELECT customer_id, status FROM billing WHERE account_id = ?",
            (aid,)).fetchone() or ("", "")) for aid in per}
        done = {r[0] for r in conn.execute("SELECT account_id FROM spend_usage WHERE day = ?",
                                           (day,))}
    out = {"accounts": len(per), "sent": 0, "skipped": 0}
    day_number = int(day.replace("-", ""))  # a dated string would be redacted as a phone
    for aid, row in sorted(per.items()):
        customer, sub_status = customers.get(aid, ("", ""))
        status, spend_minor, currency, fee = "sent", 0, "", 0
        if not customer:
            status = "no_customer"
        elif sub_status not in LIFTING:  # the metered item rides on a live subscription
            status = "no_subscription"
        elif aid in done:
            status = "already_sent"
        elif len(row["minor"]) != 1:
            status = "mixed_currencies"  # one meter value cannot hold two currencies
        else:
            ((currency, spend_minor),) = row["minor"].items()
            fee = fee_of(spend_minor, cfg.fee_pct)
            if fee <= 0:
                status = "zero_fee"
            else:
                api.meter_event(customer_id=customer, value=fee, identifier=f"{aid}:{day}")
                with transaction(conn) as c:
                    c.execute("INSERT OR IGNORE INTO spend_usage (account_id, day, "
                              "spend_minor, currency, fee_minor, sent_ts) VALUES "
                              "(?, ?, ?, ?, ?, ?)",
                              (aid, day, spend_minor, currency, fee, time.time()))
        out["sent" if status == "sent" else "skipped"] += 1
        ledger.append(RUN_ID, agent="billing", kind="tool_call", details={
            "tool": "billing.usage", "side_effects": "billing" if status == "sent" else "none",
            "status": status, "account_id": aid, "day": day_number,
            "campaigns": row["campaigns"], "spend_minor": spend_minor, "currency": currency,
            "fee_pct": cfg.fee_pct_text, "fee_minor": fee,
            "meter_event": cfg.meter_event if status == "sent" else ""})
    return out


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
    access_until: float = 0.0  # a one-off payment: access ends then (0 = none)


def of(conn: sqlite3.Connection, account_id: str) -> Billing | None:
    row = conn.execute("SELECT status, period_end, cancel_at_period_end, customer_id, "
                       "last_invoice_url, livemode, access_until FROM billing "
                       "WHERE account_id = ?", (account_id,)).fetchone()
    if row is None:
        return None
    return Billing(row[0], row[1], bool(row[2]), row[3], row[4], bool(row[5]),
                   float(row[6] or 0))


def _line_months(line: Mapping[str, Any], months_of: Mapping[str, int]) -> int:
    """Months per period of one invoice line: the price's ``recurring.interval_count``
    when the event carries it, else the configured plan of that price id."""
    price = line.get("price")
    if isinstance(price, Mapping):
        recurring = price.get("recurring") or {}
        if recurring.get("interval") == "month" and isinstance(
                recurring.get("interval_count"), int):
            return int(recurring["interval_count"])
        price_id = str(price.get("id") or "")
    else:  # newer API versions: lines[].pricing.price_details.price is the id
        price_id = str((((line.get("pricing") or {}).get("price_details") or {})
                        .get("price")) or price or "")
    return months_of.get(price_id, 0)


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
          now: float | None = None, months_of: Mapping[str, int] | None = None) -> Applied:
    """Record one verified event (once per id); what the caller should do next.
    ``months_of`` maps the configured price ids to their months per period (for the
    renewal reminder of a 3-, 6- or 12-month plan on ``invoice.upcoming``)."""
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
            if aid and obj.get("mode") == "payment":  # the one-off plan
                if obj.get("payment_status") == "paid":
                    old = c.execute("SELECT access_until FROM billing WHERE account_id = ?",
                                    (aid,)).fetchone()
                    paid = float(event.get("created") or now)
                    start = max(paid, float(old[0] or 0) if old else 0.0)  # adds to the rest
                    upsert(aid, customer_id=str(obj.get("customer") or ""),
                           access_until=start + ONCE_DAYS * 86400)
                out.account_id = aid
                out.email = str((obj.get("customer_details") or {}).get("email") or "")
            elif aid:
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
        elif kind == "invoice.upcoming":  # Stripe's renewal notice (days set in Stripe)
            aid = account_for(str(obj.get("customer") or ""),
                              ((obj.get("parent") or {}).get("subscription_details") or {})
                              .get("metadata") or obj.get("subscription_details",
                                                          {}).get("metadata"))
            lines = [x for x in ((obj.get("lines") or {}).get("data") or [])
                     if isinstance(x, Mapping)]
            months = max([_line_months(x, months_of or {}) for x in lines] or [0])
            sub = str(obj.get("subscription") or ((obj.get("parent") or {})
                      .get("subscription_details") or {}).get("subscription") or "")
            when = str(obj.get("next_payment_attempt") or obj.get("period_end") or "")
            if aid and months > 1:
                try:  # an upcoming invoice has no id: one mail per subscription renewal
                    c.execute("INSERT INTO renewal_reminders (key, sent_ts) VALUES (?, ?)",
                              (f"{sub or aid}:{when}", now))
                except sqlite3.IntegrityError:
                    pass
                else:
                    out.account_id = aid
                    out.mail = "renewal_reminder"
    return out


def event_of(body: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("object") == "event" else None
