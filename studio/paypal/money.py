"""The money steps of one funding: each PayPal call, its checks, its state change, its
money move and its receipt.

Order of every step:

1. Check the funding's state allows the step (``studio.funding.TRANSITIONS``); refuse
   before PayPal is called otherwise.
2. Call PayPal with a ``PayPal-Request-Id`` derived from the funding id, the step and
   (for steps a human authorises) the approval id, so a retry, a restart or a double
   submit cannot move money twice.
3. Check PayPal's answer against what the studio built: the ``custom_id`` must be the ad's
   ``content_sha256``, and the amount and currency must be the quoted (or requested) ones.
4. In one SQLite transaction: the ``paypal.call`` receipt in the run's chain, the funding's
   new state and amounts, and the ``funding_moves`` row that names the receipt. A
   concurrent duplicate (same idempotency key, same PayPal answer) loses the state race
   and leaves nothing behind.

A failed call (PayPal error or a mismatch) still leaves a ``paypal.call`` receipt with
``ok: false`` and the error name and issue, then raises.

Receipts hold ids, codes, integers and digests only: PayPal ids, the status, the amount
in minor units, the currency, ``content_sha256`` (= ``custom_id``), ``funding_sha256``
(when the order exists), ``idempotency_sha256`` (the request id is ``ss-`` + its first 48
hex digits), and the SHA-256 of the request and of PayPal's raw answer. No payer name,
e-mail, address or payer id is ever written: PayPal's answers are reduced to
``orders.Result`` before anything is recorded.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from studio import funding
from studio.funding import Funding, FundingError, Statement
from studio.paypal import orders
from studio.paypal.client import PayPalClient, PayPalError, request_id
from studio.receipts import PAYPAL_KIND, Ledger, Receipt, is_opaque, sha256_hex

AGENT = "treasury"
PROVIDER = "paypal"
ORDER_OPEN = frozenset({"CREATED", "PAYER_ACTION_REQUIRED", "APPROVED", "SAVED"})
CAPTURE_OK = frozenset({"COMPLETED", "PENDING"})
REFUND_OK = frozenset({"COMPLETED", "PENDING"})


def _idem(*parts: str) -> tuple[str, str]:
    rid = request_id(*parts)
    return rid, hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass(frozen=True)
class Step:
    """What one money step did: the funding after it and its receipt."""

    funding: Funding
    receipt: Receipt
    result: orders.Result | None


class Money:
    """Money steps for fundings stored in ``conn``, recorded in ``ledger``."""

    def __init__(self, conn: sqlite3.Connection, ledger: Ledger, client: PayPalClient,
                 *, payee: str = "", clock: Callable[[], float] | None = None) -> None:
        self.conn = conn
        self.ledger = ledger
        self.client = client
        self.payee = payee
        self._clock = clock

    # -- steps -----------------------------------------------------------------------------

    def open(self, *, run_id: str, item_id: str, content_sha256: str, currency: str,
             daily_budget_minor: int, days: int, fee_pct: Decimal,
             max_daily_minor: int | None = None) -> Step:
        """Quote, store and create the PayPal order (``intent: AUTHORIZE``) whose
        ``custom_id`` is the ad's ``content_sha256``. State stays ``created``."""
        q = funding.quote(currency, daily_budget_minor, days, fee_pct,
                          max_daily_minor=max_daily_minor)
        Ledger.validate_ids(run_id, AGENT)
        f = funding.create(self.conn, run_id=run_id, item_id=item_id,
                           content_sha256=content_sha256, q=q, now=self._now())
        body = orders.order_body(q, content_sha256=content_sha256, invoice_id=f.id,
                                 reference_id=item_id)
        rid, idem = _idem(f.id, "create")
        res = self._call(f, "paypal.create_order", idem, body,
                         lambda: orders.create(self.client, body, request_id=rid))
        self._expect(f, "paypal.create_order", idem, body, res,
                     status=ORDER_OPEN, amount=q.total_minor)
        return self._commit(f, "paypal.create_order", idem, body, res, to="created",
                            move="order", fields={"order_id": res.id},
                            amount=res.amount_minor)

    def sync_order(self, funding_id: str) -> Step | None:
        """Read the order; when the buyer has approved it, ``created`` → ``payer_approved``.
        ``None`` while the buyer has not approved yet (no receipt for an unchanged read)."""
        f = funding.get(self.conn, funding_id)
        funding.check_move(f, "payer_approved")
        order_id = self._need(f.order_id, "no_order")
        idem = sha256_hex(f"{f.id}\x1fget\x1f{order_id}")
        res = self._call(f, "paypal.get_order", idem, None,
                         lambda: orders.get(self.client, order_id))
        if res.status in ("CREATED", "PAYER_ACTION_REQUIRED"):
            return None
        self._expect(f, "paypal.get_order", idem, None, res, status=frozenset({"APPROVED"}),
                     amount=f.total_minor)
        return self._commit(f, "paypal.get_order", idem, None, res, to="payer_approved",
                            move="payer_approved", amount=res.amount_minor)

    def authorize(self, funding_id: str, *, approval_id: str) -> Step:
        """Hold the money of an approved order: ``payer_approved`` → ``authorized``."""
        f = funding.get(self.conn, funding_id)
        funding.check_move(f, "authorized")
        order_id = self._need(f.order_id, "no_order")
        self._approval(approval_id)
        rid, idem = _idem(f.id, "authorize", approval_id)
        res = self._call(f, "paypal.authorize", idem, None,
                         lambda: orders.authorize(self.client, order_id, request_id=rid),
                         approval_id)
        self._expect(f, "paypal.authorize", idem, None, res, status=frozenset({"CREATED"}),
                     amount=f.total_minor, approval_id=approval_id)
        return self._commit(f, "paypal.authorize", idem, None, res, to="authorized",
                            move="authorization", approval_id=approval_id,
                            fields={"authorization_id": res.id,
                                    "authorized_minor": res.amount_minor},
                            amount=res.amount_minor)

    def capture(self, funding_id: str, *, approval_id: str) -> Step:
        """Take the whole authorization (final capture): ``authorized`` → ``captured``."""
        f = funding.get(self.conn, funding_id)
        funding.check_move(f, "captured")
        auth_id = self._need(f.authorization_id, "no_authorization")
        self._approval(approval_id)
        amount = f.authorized_minor
        rid, idem = _idem(f.id, "capture", approval_id)
        body = {"amount_minor": amount, "currency": f.currency, "invoice_id": f.id}
        res = self._call(f, "paypal.capture", idem, body, lambda: orders.capture(
            self.client, auth_id, amount_minor=amount, currency=f.currency, invoice_id=f.id,
            request_id=rid), approval_id)
        self._expect(f, "paypal.capture", idem, body, res, status=CAPTURE_OK, amount=amount,
                     approval_id=approval_id, custom="if_present")
        return self._commit(f, "paypal.capture", idem, body, res, to="captured",
                            move="capture", approval_id=approval_id,
                            fields={"capture_id": res.id, "captured_minor": res.amount_minor},
                            amount=res.amount_minor)

    def void(self, funding_id: str, *, approval_id: str = "", reason: str = "lapsed") -> Step:
        """Release a hold that will not be captured: ``authorized`` → ``voided``. Without an
        approval (a lapse or a refused activation) the key names the reason instead."""
        f = funding.get(self.conn, funding_id)
        funding.check_move(f, "voided")
        auth_id = self._need(f.authorization_id, "no_authorization")
        if approval_id:
            self._approval(approval_id)
        if not is_opaque(reason):
            raise ValueError("a void reason must be a short code")
        rid, idem = _idem(f.id, "void", approval_id or f"reason:{reason}")
        res = self._call(f, "paypal.void", idem, None, lambda: orders.void(
            self.client, auth_id, request_id=rid), approval_id, reason=reason)
        self._expect(f, "paypal.void", idem, None, res, status=frozenset({"VOIDED"}),
                     amount=None, approval_id=approval_id, custom="if_present",
                     reason=reason)
        return self._commit(f, "paypal.void", idem, None, res, to="voided", move="void",
                            approval_id=approval_id, amount=0, reason=reason)

    def refund(self, funding_id: str, amount_minor: int, *, approval_id: str) -> Step:
        """Return ``amount_minor`` of the capture; at most what is left of it."""
        f = funding.get(self.conn, funding_id)
        left = f.captured_minor - f.refunded_minor
        to = "refunded" if amount_minor == left else "partially_refunded"
        funding.check_move(f, to)
        capture_id = self._need(f.capture_id, "no_capture")
        self._approval(approval_id)
        if (not isinstance(amount_minor, int) or isinstance(amount_minor, bool)
                or not 0 < amount_minor <= left):
            raise FundingError("a refund must be more than zero and at most what is left of "
                               "the capture", "refund_amount")
        n = len([m for m in funding.moves(self.conn, f.id) if m["move"] == "refund"]) + 1
        invoice = f"{f.id}-r{n}"
        rid, idem = _idem(f.id, "refund", approval_id)
        body = {"amount_minor": amount_minor, "currency": f.currency, "invoice_id": invoice}
        res = self._call(f, "paypal.refund", idem, body, lambda: orders.refund(
            self.client, capture_id, amount_minor=amount_minor, currency=f.currency,
            invoice_id=invoice, request_id=rid), approval_id)
        self._expect(f, "paypal.refund", idem, body, res, status=REFUND_OK,
                     amount=amount_minor, approval_id=approval_id, custom="if_present")
        return self._commit(f, "paypal.refund", idem, body, res, to=to, move="refund",
                            approval_id=approval_id,
                            fields={"refunded_minor": f.refunded_minor + res.amount_minor},
                            amount=res.amount_minor)

    def statement(self, funding_id: str, spend_minor: int) -> Statement:
        f = funding.get(self.conn, funding_id)
        return funding.reconcile(currency=f.currency, budget_minor=f.budget_minor,
                                 fee_minor=f.fee_minor, captured_minor=f.captured_minor,
                                 spend_minor=spend_minor, refunded_minor=f.refunded_minor,
                                 fee_pct=f.fee_pct)

    # -- internals -------------------------------------------------------------------------

    def _now(self) -> float | None:
        return self._clock() if self._clock is not None else None

    @staticmethod
    def _need(value: str | None, code: str) -> str:
        if not value:
            raise FundingError(f"the funding is missing a PayPal id ({code})", code)
        return value

    @staticmethod
    def _approval(approval_id: str) -> None:
        if not approval_id or not is_opaque(approval_id):
            raise FundingError("this money step needs an approval id", "no_approval")

    def _details(self, f: Funding, step: str, idem: str, body: Any,
                 res: orders.Result | None, **extra: Any) -> dict[str, Any]:
        d: dict[str, Any] = {"step": step, "funding_id": f.id, "item_id": f.item_id,
                             "idempotency_sha256": idem}
        if body is not None:
            d["request_sha256"] = sha256_hex(json.dumps(body, sort_keys=True,
                                                        separators=(",", ":")))
        for key in ("order_id", "authorization_id", "capture_id"):  # what the step acts on
            if getattr(f, key):
                d[key] = getattr(f, key)
        if res is not None:
            key = {"order": "order_id", "authorization": "authorization_id",
                   "capture": "capture_id", "refund": "refund_id"}[res.kind]
            d[key] = res.id
            d["status"] = res.status
            d["amount_minor"] = res.amount_minor
            d["currency"] = res.currency or f.currency
            d["body_sha256"] = res.body_sha256
            if res.custom_id:
                d["custom_id_sha256"] = res.custom_id  # PayPal's custom_id: the ad's hash
        d["content_sha256"] = f.content_sha256
        if f.order_id:
            try:
                d["funding_sha256"] = funding.funding_sha256(f.block(self.payee))
            except FundingError:
                pass  # an order id PayPal shaped unusually: the receipt still names it
        for k, v in extra.items():
            if v not in ("", None):
                d[k] = v
        return d

    def _receipt(self, f: Funding, details: dict[str, Any], *, output: str = "",
                 inputs: list[str] | None = None) -> Receipt:
        return self.ledger.append(f.run_id, agent=AGENT, kind=PAYPAL_KIND, provider=PROVIDER,
                                  conn=self.conn if self.ledger.joins(self.conn) else None,
                                  output_sha256=output, input_sha256=inputs or [],
                                  details=details)

    def _call(self, f: Funding, step: str, idem: str, body: Any,
              fn: Callable[[], orders.Result], approval_id: str = "",
              **extra: Any) -> orders.Result:
        try:
            return fn()
        except PayPalError as exc:
            self._receipt(f, self._details(
                f, step, idem, body, None, approval_id=approval_id, ok=False,
                error=exc.name or "HTTP_ERROR", issue=exc.issue, http_status=exc.status,
                outcome_unknown=exc.unknown or None, **extra))
            raise

    def _expect(self, f: Funding, step: str, idem: str, body: Any, res: orders.Result, *,
                status: frozenset[str], amount: int | None, approval_id: str = "",
                custom: str = "required", **extra: Any) -> None:
        """``custom``: ``required`` (PayPal must echo the ad's hash as ``custom_id``),
        ``if_present`` (it may leave it out, but must not name another) or ``no``."""
        problems = []
        if res.status not in status:
            problems.append("status")
        if amount is not None and (res.amount_minor != amount or res.currency != f.currency):
            problems.append("amount")
        if (custom == "required" and res.custom_id != f.content_sha256) or (
                custom == "if_present" and res.custom_id not in ("", f.content_sha256)):
            problems.append("custom_id")
        if not problems:
            return
        self._receipt(f, self._details(f, step, idem, body, res, approval_id=approval_id,
                                       ok=False, error="MISMATCH",
                                       issue="_".join(problems).upper(), **extra),
                      output=res.body_sha256)
        raise FundingError(f"PayPal's answer to {step} does not match what the studio "
                           f"built ({', '.join(problems)})", "mismatch")

    def _commit(self, f: Funding, step: str, idem: str, body: Any, res: orders.Result, *,
                to: str, move: str, amount: int, approval_id: str = "",
                fields: dict[str, Any] | None = None, **extra: Any) -> Step:
        new_fields = dict(fields or {})
        with funding.atomic(self.conn):
            if "order_id" in new_fields:  # the order exists from here on: name it
                f = Funding(**{**f.__dict__, "order_id": new_fields["order_id"]})
            details = self._details(f, step, idem, body, res, approval_id=approval_id,
                                    ok=True, **extra)
            inputs = [details["request_sha256"]] if "request_sha256" in details else []
            receipt = self._receipt(f, details, output=res.body_sha256, inputs=inputs)
            after = funding.advance(self.conn, f.id, to, expect=f.state, now=self._now(),
                                    **new_fields)
            funding.record_move(self.conn, after, move, paypal_id=res.id, status=res.status,
                                amount_minor=amount, approval_id=approval_id,
                                receipt_id=receipt.id, now=self._now())
        return Step(after, receipt, res)
