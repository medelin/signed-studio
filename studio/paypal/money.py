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
        self._claim(approval_id, f, "authorize", f.total_minor)
        rid, idem = _idem(f.id, "authorize", approval_id)
        res = self._call(f, "paypal.authorize", idem, None,
                         lambda: orders.authorize(self.client, order_id, request_id=rid),
                         approval_id, claim=approval_id)
        self._expect(f, "paypal.authorize", idem, None, res, status=frozenset({"CREATED"}),
                     amount=f.total_minor, approval_id=approval_id)
        return self._commit(f, "paypal.authorize", idem, None, res, to="authorized",
                            move="authorization", approval_id=approval_id, claim=approval_id,
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
        self._claim(approval_id, f, "capture", amount)
        rid, idem = _idem(f.id, "capture", approval_id, str(amount))
        body = {"amount_minor": amount, "currency": f.currency, "invoice_id": f.id}
        res = self._call(f, "paypal.capture", idem, body, lambda: orders.capture(
            self.client, auth_id, amount_minor=amount, currency=f.currency, invoice_id=f.id,
            request_id=rid), approval_id, claim=approval_id)
        self._expect(f, "paypal.capture", idem, body, res, status=CAPTURE_OK, amount=amount,
                     approval_id=approval_id, custom="if_present")
        return self._commit(f, "paypal.capture", idem, body, res, to="captured",
                            move="capture", approval_id=approval_id, claim=approval_id,
                            fields={"capture_id": res.id, "captured_minor": res.amount_minor},
                            guard={"captured_minor": 0}, amount=res.amount_minor)

    def void(self, funding_id: str, *, approval_id: str = "", reason: str = "lapsed") -> Step:
        """Release a hold that will not be captured: ``authorized`` → ``voided``. Without an
        approval (a lapse or a refused activation) the claim key is the funding's single
        void key, so at most one void runs."""
        f = funding.get(self.conn, funding_id)
        funding.check_move(f, "voided")
        auth_id = self._need(f.authorization_id, "no_authorization")
        if approval_id:
            self._approval(approval_id)
        if not is_opaque(reason):
            raise ValueError("a void reason must be a short code")
        key = approval_id or f"{f.id}:void"
        self._claim(key, f, "void", 0)
        rid, idem = _idem(f.id, "void", key)
        res = self._call(f, "paypal.void", idem, None, lambda: orders.void(
            self.client, auth_id, request_id=rid), approval_id, claim=key, reason=reason)
        self._expect(f, "paypal.void", idem, None, res, status=frozenset({"VOIDED"}),
                     amount=None, approval_id=approval_id, custom="if_present",
                     reason=reason)
        return self._commit(f, "paypal.void", idem, None, res, to="voided", move="void",
                            approval_id=approval_id, claim=key, amount=0, reason=reason,
                            guard={"captured_minor": 0})

    def refund(self, funding_id: str, amount_minor: int, *, approval_id: str) -> Step:
        """Return ``amount_minor`` of the capture; at most what is left of it, counting
        refunds still in flight. One approval authorises exactly one refund."""
        if (not isinstance(amount_minor, int) or isinstance(amount_minor, bool)
                or amount_minor <= 0):
            raise FundingError("a refund must be more than zero and at most what is left of "
                               "the capture", "refund_amount")
        self._approval(approval_id)
        f = funding.get(self.conn, funding_id)
        if f.state not in ("captured", "partially_refunded"):
            raise FundingError(f"a funding in state {f.state!r} cannot be refunded",
                               "wrong_state")
        capture_id = self._need(f.capture_id, "no_capture")
        # reserves the amount against the capture, refunds in flight included
        self._claim(approval_id, f, "refund", amount_minor)
        f = funding.get(self.conn, funding_id)  # as of the claim
        invoice = f"{f.id}-{approval_id}"[:127]
        rid, idem = _idem(f.id, "refund", approval_id, str(amount_minor))
        body = {"amount_minor": amount_minor, "currency": f.currency, "invoice_id": invoice}
        res = self._call(f, "paypal.refund", idem, body, lambda: orders.refund(
            self.client, capture_id, amount_minor=amount_minor, currency=f.currency,
            invoice_id=invoice, request_id=rid), approval_id, claim=approval_id)
        self._expect(f, "paypal.refund", idem, body, res, status=REFUND_OK,
                     amount=amount_minor, approval_id=approval_id, custom="if_present")
        # the state and the guard are read at commit time, inside the transaction
        return self._commit(f, "paypal.refund", idem, body, res, to="refund", move="refund",
                            approval_id=approval_id, claim=approval_id,
                            add={"refunded_minor": res.amount_minor},
                            amount=res.amount_minor)

    def reconcile_claim(self, funding_id: str, key: str) -> Step:
        """Settle a ``pending`` claim (an ambiguous outcome: a timeout, a 5xx, a 409, a
        success whose body could not be read) by sending the same step again with the
        same ``PayPal-Request-Id``: PayPal answers with the result of the first request
        instead of acting again, and that answer is checked and recorded like any other.
        ``FundingError`` ``no_claim`` when ``key`` has no pending claim on the funding."""
        pending = [c for c in funding.claims(self.conn, funding_id)
                   if c["key"] == key and c["status"] == "pending"]
        if not pending:
            raise FundingError("no pending money step for this key", "no_claim")
        step, amount = pending[0]["step"], pending[0]["amount_minor"]
        if step == "refund":
            return self.refund(funding_id, amount, approval_id=key)
        if step == "capture":
            return self.capture(funding_id, approval_id=key)
        if step == "authorize":
            return self.authorize(funding_id, approval_id=key)
        return self.void(funding_id, approval_id="" if key.endswith(":void") else key)

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

    def _claim(self, key: str, f: Funding, step: str, amount: int) -> None:
        funding.claim(self.conn, key, funding_id=f.id, step=step, amount_minor=amount,
                      now=self._now())

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
              claim: str = "", **extra: Any) -> orders.Result:
        """Call PayPal and close the claim on failure, failing closed:

        - the request never left the studio (``not exc.sent``: no connection, no token):
          ``released``, the same parameters may try again;
        - PayPal may have acted (``exc.unknown``, a 5xx or a network error after sending):
          stays ``pending`` with its amount reserved; only a retry with the same approval
          and amount, hence the same request id, can settle it;
        - a definite 4xx refusal (and only that): ``refused``, the approval is spent and a
          new signed approval is needed;
        - anything else, including a 2xx whose body could not be read or checked
          (``unknown``): stays ``pending``; ``reconcile_claim`` settles it."""
        try:
            return fn()
        except PayPalError as exc:
            if claim:
                if not exc.sent:
                    funding.end_claim(self.conn, claim, "released", now=self._now())
                elif 400 <= exc.status < 500 and not exc.unknown:
                    funding.end_claim(self.conn, claim, "refused", now=self._now())
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
                to: str, move: str, amount: int, approval_id: str = "", claim: str = "",
                fields: dict[str, Any] | None = None, add: dict[str, int] | None = None,
                guard: dict[str, Any] | None = None, **extra: Any) -> Step:
        """Receipt, state, money move and claim in one transaction. A replayed PayPal
        object (``replayed``), a concurrent change (``conflict``) or a broken invariant
        (the table's CHECK) rolls all of it back; the refusal is then receipted on its own
        and the claim stays pending, so the inconsistency is visible, never silently
        counted."""
        new_fields = dict(fields or {})
        try:
            return self._commit_txn(f, step, idem, body, res, to=to, move=move,
                                    amount=amount, approval_id=approval_id, claim=claim,
                                    fields=new_fields, add=add, guard=guard, **extra)
        except (FundingError, sqlite3.IntegrityError) as exc:
            code = exc.code if isinstance(exc, FundingError) else "invariant"
            self._receipt(f, self._details(f, step, idem, body, res, approval_id=approval_id,
                                           ok=False, error="NOT_RECORDED",
                                           issue=code.upper(), **extra),
                          output=res.body_sha256)
            if isinstance(exc, FundingError):
                raise
            raise FundingError("the money step would break a funding invariant",
                               "invariant") from exc

    def _commit_txn(self, f: Funding, step: str, idem: str, body: Any, res: orders.Result,
                    *, to: str, move: str, amount: int, approval_id: str, claim: str,
                    fields: dict[str, Any], add: dict[str, int] | None,
                    guard: dict[str, Any] | None, **extra: Any) -> Step:
        new_fields = fields
        with funding.atomic(self.conn):
            if to == "refund":  # read inside the transaction: the version the update checks
                f = funding.get(self.conn, f.id)
                left = f.captured_minor - f.refunded_minor - res.amount_minor
                if left < 0:
                    raise FundingError("the refund is more than what is left of the capture",
                                       "refund_amount")
                to = "refunded" if left == 0 else "partially_refunded"
                guard = {"refunded_minor": f.refunded_minor}
            if "order_id" in new_fields:  # the order exists from here on: name it
                f = Funding(**{**f.__dict__, "order_id": new_fields["order_id"]})
            details = self._details(f, step, idem, body, res, approval_id=approval_id,
                                    ok=True, **extra)
            inputs = [details["request_sha256"]] if "request_sha256" in details else []
            receipt = self._receipt(f, details, output=res.body_sha256, inputs=inputs)
            after = funding.advance(self.conn, f.id, to, expect=f.state, now=self._now(),
                                    add=add, guard=guard, **new_fields)
            funding.record_move(self.conn, after, move, paypal_id=res.id, status=res.status,
                                amount_minor=amount, approval_id=approval_id,
                                receipt_id=receipt.id, now=self._now())
            if claim:
                funding.settle_claim(self.conn, claim, res.id, now=self._now())
        return Step(after, receipt, res)
