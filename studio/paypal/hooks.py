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

In mock mode (``PAYPAL_ADAPTER=mock``) a ``FakePayPal`` stands in for the API and a
``WebhookSigner`` signs its events with a test CA; ``flush`` runs them through the same
``webhooks.apply`` verification as real PayPal events, after each money step.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from studio import executor, funding
from studio.approvals import Pending, Verified, ad_payload, payload_sha256, transaction
from studio.funding import Funding, FundingError, funding_sha256
from studio.paypal import PayPalConfig, webhooks
from studio.paypal.client import PayPalClient
from studio.paypal.mock import FakePayPal, WebhookSigner
from studio.paypal.money import Money
from studio.receipts import (
    FUNDING_FIELD,
    FUNDING_REFUND,
    Ledger,
    canonical,
    refund_body_problem,
    refund_payload,
)

LOG = logging.getLogger(__name__)
HONOUR_S = 3 * 86400
ACTIVATION_KEY = "paypal"  # the funding named in a campaign.activate payload
DEFAULT_DAYS = 7


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

    @property
    def mock(self) -> bool:
        return self.fake is not None

    def lookup(self, path: str) -> dict[str, Any] | None:
        """GET a PayPal resource with the studio's own credentials: what a webhook names
        is attested by PayPal's API, not by the webhook body."""
        return self.money.client.get(path).json

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
        return f

    def authorize(self, f: Funding, v: Verified) -> dict[str, Any]:
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

    def capture(self, v: Verified, payload: dict[str, Any]) -> dict[str, Any]:
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
        step = self.money.capture(f.id, approval_id=v.pending_id)
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
