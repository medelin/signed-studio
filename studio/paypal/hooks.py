"""The PayPal side of the campaign executors: no campaign without held money, no
activation without taken money.

``Funder`` is built once per app from ``PAYPAL_ADAPTER`` (``make``): ``None`` when PayPal
is off, so every hook is skipped and the studio behaves as before. With PayPal on:

- ``campaign.create`` (``for_create`` before any Meta call): the signed payload must
  carry a ``funding`` block whose hash is the approval's ``funding_sha256``, naming an
  order the buyer approved (state ``payer_approved``), for this item, with the quoted
  amount, and whose ``content_sha256`` is the ad's hash (the payload without the block).
  Otherwise nothing is created (``funding_required`` / ``funding_changed``). After the
  campaign exists, PAUSED, the order is **authorized** under the same approval
  (``authorize``): the money is held, not taken. If that fails, the campaign stays PAUSED
  and unfunded, and activation is refused.
- ``campaign.activate`` (``activation_block`` / ``capture``): the activation payload
  names the funding, its authorization and the amount, so the second signature covers the
  capture. The **capture** runs before Meta is asked to activate anything; no capture, no
  activation.
- ``void_lapsed``: an authorization not captured within ``HONOUR_S`` (PayPal's 3-day
  honour period) is voided; the hold is released.
- ``funding.refund``: a refund of part of a capture is its own signed approval whose
  payload is ``refund_payload`` of a closed ``refund/v1`` body (``request_refund`` /
  ``execute_refund``).

- **A campaign approved by a signed mandate** (Day 3) has no buyer present: the mandate
  path (``studio.mandates.execute``) calls ``charge``, which pays the ad from the owner's
  saved PayPal (``studio.paypal.vault``, Vault v3) within the mandate's caps: an
  ``AUTHORIZE`` order with the vault id, so the money is held before the campaign is
  created. ``for_create`` then accepts that held funding (``mandate_charges`` row
  ``done`` for this approval, same item, ad hash and amount) instead of a signed buyer
  block, and the capture still needs the owner's activation signature. A charge whose
  mandate was revoked (or whose saved PayPal was deleted) while PayPal was answering is
  voided at once; ``release`` voids the hold of a campaign that was then not created.

In mock mode (``PAYPAL_ADAPTER=mock``) a ``FakePayPal`` stands in for the API and a
``WebhookSigner`` signs its events with a test CA; ``flush`` runs them through the same
``webhooks.apply`` verification as real PayPal events, after each money step.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from typing import Any

from studio import executor, funding
from studio.approvals import Pending, Verified, ad_payload, payload_sha256, transaction
from studio.funding import Funding, FundingError, Quote, funding_sha256
from studio.paypal import PayPalConfig, webhooks
from studio.paypal.client import PayPalClient, PayPalError
from studio.paypal.mock import FakePayPal, WebhookSigner
from studio.paypal.money import Money
from studio.paypal.vault import Vault
from studio.receipts import (
    FUNDING_FIELD,
    FUNDING_REFUND,
    MANDATE_APPROVER,
    Ledger,
    canonical,
    refund_body_problem,
    refund_payload,
)

LOG = logging.getLogger(__name__)
HONOUR_S = 3 * 86400
ACTIVATION_KEY = "paypal"  # the funding named in a campaign.activate payload
DEFAULT_DAYS = 7


def days_of(payload: dict[str, Any], asked: Any = None) -> int:
    """How many days a campaign's funding covers: its schedule when it has one, else the
    ``days`` asked for (1 to ``funding.MAX_DAYS``), else ``DEFAULT_DAYS``."""
    schedule = payload.get("schedule")
    if isinstance(schedule, dict):
        import datetime as dt

        try:
            start = dt.date.fromisoformat(str(schedule["start"]))
            end = dt.date.fromisoformat(str(schedule["end"]))
            return max(1, (end - start).days + 1)
        except (KeyError, ValueError):
            pass
    if isinstance(asked, int) and not isinstance(asked, bool) and 1 <= asked <= funding.MAX_DAYS:
        return asked
    return DEFAULT_DAYS


class FundingRefused(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class Funder:
    def __init__(self, conn: sqlite3.Connection, ledger: Ledger, cfg: PayPalConfig,
                 client: PayPalClient, verifier: webhooks.Verifier, *,
                 fake: FakePayPal | None = None, signer: WebhookSigner | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.conn = conn
        self.ledger = ledger
        self.cfg = cfg
        self.fake = fake
        self.signer = signer
        self.verifier = verifier
        self.clock = clock
        self.money = Money(conn, ledger, client, after=self.flush)
        self.vault = Vault(conn, ledger, client, clock=clock)

    @property
    def mock(self) -> bool:
        return self.fake is not None

    def lookup(self, path: str) -> dict[str, Any] | None:
        """GET a PayPal resource with the studio's own credentials: what a webhook names
        is attested by PayPal's API, not by the webhook body. One fast read: a transient
        failure answers 503 and PayPal redelivers, which is cheaper than holding its 30 s
        delivery window on our own retries."""
        return self.money.client.get(path, retries=0).json

    def flush(self) -> int:
        """Mock mode: deliver the fake's webhook events, signed, through ``apply``."""
        if self.fake is None or self.signer is None:
            return 0
        n = 0
        for event in self.fake.drain():
            headers, body = self.signer.sign(event, now=self.clock())
            try:  # the money step already committed: a refused event is logged, not raised
                webhooks.apply(self.conn, self.ledger, self.verifier, headers, body,
                               lookup=self.lookup)
                n += 1
            except webhooks.WebhookRefused as exc:
                LOG.warning("mock PayPal webhook refused: %s", exc.code)
        return n

    # -- campaign.create ---------------------------------------------------------------------

    def for_create(self, v: Verified, payload: dict[str, Any]) -> Funding:
        if v.approver.startswith(MANDATE_APPROVER):
            return self._mandate_funding(v, payload)
        block = payload.get(FUNDING_FIELD)
        if not v.funding_sha256 or not isinstance(block, dict):
            raise FundingRefused("this studio funds every campaign through PayPal; the "
                                 "approval signs no funding", "funding_required")
        if funding_sha256(block) != v.funding_sha256:
            raise FundingRefused("the funding block is not the signed one", "funding_changed")
        f = funding.by_order(self.conn, str(block.get("order_id")))
        problems = []
        if f is None:
            problems.append("order")
        else:
            if f.state != "payer_approved":
                problems.append("state")
            if (f.item_id, f.content_sha256) != (v.item_id, block["content_sha256"]):
                problems.append("item")
            if (f.total_minor, f.currency) != (block["amount_minor"], block["currency"]):
                problems.append("amount")
        if block["content_sha256"] != payload_sha256(ad_payload(payload)):
            problems.append("ad")
        if problems:
            raise FundingRefused(f"the signed funding does not match ({', '.join(problems)})",
                                 "funding_changed")
        assert f is not None
        if self.conn.execute("SELECT 1 FROM mandate_charges WHERE funding_id = ?",
                             (f.id,)).fetchone():
            raise FundingRefused("a vault charge is not a buyer's order", "funding_changed")
        return f

    def _mandate_funding(self, v: Verified, payload: dict[str, Any]) -> Funding:
        """The money a mandate's charge holds for exactly this approval: the charge is
        done, its funding is authorized for this item and ad, for the declared amount."""
        from studio import mandates

        if FUNDING_FIELD in payload:
            raise FundingRefused("a mandate approval carries no buyer's funding block",
                                 "funding_changed")
        c = mandates.charge_of(self.conn, v.pending_id)
        if c is None or c["status"] != "done" or not c["funding_id"]:
            raise FundingRefused("the mandate's charge holds no money for this approval",
                                 "funding_required")
        f = funding.get(self.conn, str(c["funding_id"]))
        if (f.state != "authorized" or f.item_id != v.item_id
                or f.content_sha256 != v.content_sha256
                or [f.total_minor, f.authorized_minor, f.currency] != [
                    c["amount_minor"], c["amount_minor"], c["currency"]]):
            raise FundingRefused("the held money is not the mandate's charge for this ad",
                                 "funding_changed")
        return f

    # -- a mandate's charge of the saved PayPal (studio.mandates.Money) ---------------------

    def mandate_quote(self, payload: Mapping[str, Any]) -> Quote:
        """The amount a mandate charges for a campaign: the same quote as a buyer's order
        (daily budget × days + the studio fee), computed by code from the payload."""
        camp = payload.get("campaign")
        camp = camp if isinstance(camp, dict) else {}
        daily = camp.get("daily_budget_minor")
        if not isinstance(daily, int) or isinstance(daily, bool):
            raise FundingError("the campaign has no daily budget", "amount")
        return funding.quote(str(camp.get("currency", "")), daily, days_of(dict(payload)),
                             self.cfg.fee_pct)

    def vault_active(self, account_id: str, digest: str) -> bool:
        return self.vault.active(account_id, digest) is not None

    def charge(self, verified: Verified, payload: Mapping[str, Any], mandate: Any,
               charge: Mapping[str, Any]) -> dict[str, Any]:
        """Pay the approved ad from the saved PayPal named by the mandate: the money is
        held (``authorized``) before any campaign exists. The amount is recomputed and
        must be the declared one. ``mandate_charges`` is settled ``done`` with the
        funding, or ``refused`` on PayPal's definite refusal (an unknown outcome stays
        pending, its amount reserved; calling ``charge`` again resends the same request
        id). A mandate revoked, or a saved PayPal deleted, by the time PayPal answered:
        the hold is voided at once and ``mandate_revoked`` raised."""
        from studio import mandates

        q = self.mandate_quote(payload)
        if q.total_minor != charge["amount_minor"] or q.currency != charge["currency"]:
            raise FundingRefused("the charge is not the declared amount", "charge_changed")
        c = mandates.charge_of(self.conn, verified.pending_id)
        if c is None or c["mandate_id"] != mandate.id or c["status"] != "pending" \
                or c["amount_minor"] != q.total_minor:
            raise FundingRefused("no pending charge of this mandate for this approval",
                                 "charge_changed")
        found = self.vault.active(mandate.account_id, charge["vault_sha256"])
        if found is None:
            with transaction(self.conn):
                mandates.settle_charge(self.conn, verified.pending_id, "refused",
                                       error="vault")
            raise FundingRefused("the saved PayPal is gone", "vault")
        if c["funding_id"]:
            f = funding.get(self.conn, str(c["funding_id"]))  # a retry: same request id
        else:
            ad = ad_payload(payload)
            f = self.money.open_vault(run_id=verified.run_id, item_id=verified.item_id,
                                      content_sha256=payload_sha256(ad), q=q)
            with transaction(self.conn):
                mandates.note_funding(self.conn, verified.pending_id, f.id)
        try:
            step = self.money.charge_vault(f.id, vault_id=found[1],
                                           approval_id=verified.pending_id,
                                           mandate_id=mandate.id,
                                           vault_sha256=charge["vault_sha256"],
                                           period=charge["period"])
        except PayPalError as exc:
            if not exc.sent or (400 <= exc.status < 500 and not exc.unknown):
                with transaction(self.conn):
                    mandates.settle_charge(self.conn, verified.pending_id, "refused",
                                           funding_id=f.id, error=exc.issue or exc.name)
            raise
        with transaction(self.conn):
            mandates.settle_charge(self.conn, verified.pending_id, "done", funding_id=f.id)
        m = mandates.get(self.conn, mandate.id)
        if m is None or m.revoked_ts is not None or self.vault.active(
                mandate.account_id, charge["vault_sha256"]) is None:
            self.money.void(f.id, reason="mandate_revoked")
            raise FundingRefused("the mandate was revoked while PayPal answered",
                                 "mandate_revoked")
        held = step.funding
        return {"funding_id": f.id, "state": held.state, "order_id": held.order_id,
                "authorization_id": held.authorization_id,
                "amount_minor": held.authorized_minor, "currency": held.currency,
                "receipt_id": step.receipt.id, "receipt_hash": step.receipt.hash}

    def release(self, approval_id: str, reason: str) -> None:
        """Void the hold of a mandate's charge whose campaign was not created."""
        from studio import mandates

        c = mandates.charge_of(self.conn, approval_id)
        if c is None or not c["funding_id"]:
            return
        try:
            if funding.get(self.conn, str(c["funding_id"])).state == "authorized":
                self.money.void(str(c["funding_id"]), reason=reason)
        except Exception as exc:  # noqa: BLE001 - receipted; void_lapsed retries
            LOG.warning("release of mandate charge failed: %s", type(exc).__name__)

    def authorize(self, f: Funding, v: Verified) -> dict[str, Any]:
        if v.approver.startswith(MANDATE_APPROVER):
            held = funding.get(self.conn, f.id)  # the mandate's charge already holds it
            if held.state != "authorized":
                raise FundingRefused("the mandate's charge holds no money", "not_funded")
            return {"funding_id": f.id, "state": held.state,
                    "authorization_id": held.authorization_id,
                    "amount_minor": held.authorized_minor, "currency": f.currency,
                    "paid_by": "mandate"}
        step = self.money.authorize(f.id, approval_id=v.pending_id)
        return {"funding_id": f.id, "state": step.funding.state,
                "authorization_id": step.funding.authorization_id,
                "amount_minor": step.funding.authorized_minor, "currency": f.currency}

    # -- campaign.activate -------------------------------------------------------------------

    def activation_block(self, item_id: str) -> dict[str, Any]:
        held = [f for f in funding.of_item(self.conn, item_id) if f.state == "authorized"]
        if not held:
            raise FundingRefused("the campaign's budget is not held in PayPal", "not_funded")
        f = held[-1]
        return {"funding_id": f.id, "authorization_id": f.authorization_id,
                "amount_minor": f.authorized_minor, "currency": f.currency}

    def capture(self, v: Verified, payload: dict[str, Any],
                simulate: str | None = None) -> dict[str, Any]:
        """Take the held money under the activation approval. ``simulate``: a sandbox
        refusal to ask PayPal for (``PayPal-Mock-Response``); not part of the signed payload."""
        named = payload.get(ACTIVATION_KEY)
        if not isinstance(named, dict):
            raise FundingRefused("the activation names no PayPal funding", "not_funded")
        try:
            f = funding.get(self.conn, str(named.get("funding_id")))
        except FundingError:
            raise FundingRefused("no such funding", "not_funded") from None
        if (f.item_id != v.item_id or f.state != "authorized"
                or [f.authorization_id, f.authorized_minor, f.currency] != [
                    named.get("authorization_id"), named.get("amount_minor"),
                    named.get("currency")]):
            raise FundingRefused("the funding changed since the activation was signed",
                                 "funding_changed")
        step = self.money.capture(f.id, approval_id=v.pending_id, mock_response=simulate)
        return {"funding_id": f.id, "state": step.funding.state,
                "capture_id": step.funding.capture_id,
                "amount_minor": step.funding.captured_minor, "currency": f.currency}

    # -- lapses and refunds ------------------------------------------------------------------

    def void_lapsed(self, now: float | None = None) -> list[str]:
        """Void every authorization held longer than the honour period."""
        clock = self.clock() if now is None else now
        rows = self.conn.execute("SELECT id FROM fundings WHERE state = 'authorized' AND "
                                 "updated_ts < ?", (clock - HONOUR_S,)).fetchall()
        done = []
        for (fid,) in rows:
            try:
                self.money.void(fid, reason="lapsed")
                done.append(fid)
            except Exception:  # noqa: BLE001,S112 - receipted by Money; the next sweep retries
                continue
        return done

    def request_refund(self, funding_id: str, amount_minor: int) -> tuple[Pending,
                                                                           dict[str, Any]]:
        f = funding.get(self.conn, funding_id)
        if f.state not in ("captured", "partially_refunded") or not f.capture_id:
            raise FundingRefused("nothing captured to refund", "wrong_state")
        if not 0 < amount_minor <= f.captured_minor - f.refunded_minor:
            raise FundingRefused("the refund is more than what is left", "refund_amount")
        body = {"schema": "refund/v1", "funding_id": f.id, "capture_id": f.capture_id,
                "amount_minor": amount_minor, "currency": f.currency}
        assert refund_body_problem(body) is None
        payload = refund_payload(body)
        pending = Pending.create(FUNDING_REFUND, f.id, payload, ledger=self.ledger,
                                 run_id=f.run_id)
        with transaction(self.conn):
            pending.save(self.conn)
            self.conn.execute(
                "INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                "VALUES (?, ?, ?, ?)",
                (pending.id, f.run_id, canonical(payload).decode(), time.time()))
        return pending, payload

    def execute_refund(self, verified: Any, payload: dict[str, Any]) -> dict[str, Any]:
        v: Verified = executor.authorize(self.conn, verified, FUNDING_REFUND, payload)
        body = payload.get("refund")
        if refund_body_problem(body) is not None or not isinstance(body, dict) or \
                body["funding_id"] != v.item_id:
            executor.finish(self.conn, v, "failed", {"error": "refund_changed"})
            raise FundingRefused("the refund body is malformed", "refund_changed")
        f = funding.get(self.conn, v.item_id)
        if f.capture_id != body["capture_id"] or f.currency != body["currency"]:
            executor.finish(self.conn, v, "failed", {"error": "refund_changed"})
            raise FundingRefused("the capture changed since the refund was signed",
                                 "refund_changed")
        try:
            step = self.money.refund(f.id, body["amount_minor"], approval_id=v.pending_id)
        except Exception as exc:
            executor.finish(self.conn, v, "failed",
                            {"error": getattr(exc, "code", type(exc).__name__)})
            raise
        result = {"funding_id": f.id, "state": step.funding.state,
                  "refunded_minor": step.funding.refunded_minor, "currency": f.currency,
                  "adapter": "paypal-" + self.cfg.adapter}
        executor.finish(self.conn, v, "done", result)
        return result


def make(cfg: PayPalConfig, conn: sqlite3.Connection, ledger: Ledger, *,
         clock: Callable[[], float] = time.time) -> Funder | None:
    """The app's funder for ``PAYPAL_ADAPTER``; ``None`` when PayPal is off."""
    if not cfg.enabled:
        return None
    funding.ensure_schema(conn)
    webhooks.ensure_schema(conn)
    from studio import mandates
    from studio.paypal import vault

    mandates.ensure_schema(conn)
    vault.ensure_schema(conn)
    if cfg.adapter == "mock":
        fake = FakePayPal()
        signer = WebhookSigner(cfg.webhook_id or "MOCKWEBHOOK0001", now=clock())
        client = PayPalClient("mock-client", "mock-secret", transport=fake.transport(),
                              sleep=lambda s: None)
        verifier = webhooks.Verifier(conn, signer.webhook_id, fetch=signer.fetch,
                                     roots=signer.roots, clock=clock)
        return Funder(conn, ledger, cfg, client, verifier, fake=fake, signer=signer,
                      clock=clock)
    client = PayPalClient(cfg.client_id, cfg.client_secret)
    verifier = webhooks.Verifier(conn, cfg.webhook_id, clock=clock)
    return Funder(conn, ledger, cfg, client, verifier, clock=clock)
