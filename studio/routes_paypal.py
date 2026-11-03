"""PayPal routes: fund a campaign, PayPal's webhooks, refunds (``studio.paypal``).

Registered only when ``PAYPAL_ADAPTER`` is set (``app.state.funder``):

- ``GET /api/paypal/config``: what the approval screen needs to show the PayPal button
  (the adapter, and the sandbox REST app's client id, which is public by design).
- ``POST /api/funding/{approval_id}/order``: for an unsigned ``campaign.create``
  approval the caller may sign, create (or reuse) the PayPal order for its budget: daily
  budget from the payload × days (the payload's schedule, else the ``days`` asked for,
  else ``DEFAULT_DAYS``) plus the studio fee. The amount is computed by code from the
  payload, never taken from the request. ``custom_id`` is the ad's hash.
- ``POST /api/funding/{approval_id}/payer-approved``: after the buyer approved in PayPal
  (``onApprove``), read the order back; once PayPal says APPROVED, the approval is
  superseded by one that carries the funding block (``approvals.attach_funding``), and
  its id is returned: the owner signs that one. ``/mock-approve`` does the buyer's part
  in mock mode only.
- ``POST /webhooks/paypal``: verified offline before parsing (``webhooks.apply``); no
  session and no CSRF token (PayPal's RSA signature is the authentication).
- ``GET /api/funding/item/{item_id}``: the item's fundings and money moves.
- ``POST /api/funding/{funding_id}/refund``: a pending ``funding.refund`` approval for an
  amount of the capture; signing it runs the refund (``Funder.execute_refund``).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from studio import funding
from studio.approvals import (
    ApprovalError,
    Pending,
    ad_payload,
    attach_funding,
    payload_sha256,
)
from studio.funding import FundingError
from studio.meta.currency import to_minor
from studio.paypal import webhooks
from studio.paypal.client import PayPalError
from studio.paypal.hooks import Funder, FundingRefused, days_of
from studio.receipts import Ledger
from studio.settings import Settings

LOG = logging.getLogger(__name__)
APPROVAL_ID = re.compile(r"[0-9a-f]{32}")
FUNDING_ID = re.compile(r"fund_[0-9a-f]{16}")
ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
NO_STORE = {"Cache-Control": "no-store"}


def _days(payload: dict[str, Any], asked: Any) -> int:
    return days_of(payload, asked)


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any, write_guard: Any,
             session_of: Any) -> None:
    funder: Funder | None = app.state.funder
    if funder is None:
        return
    app.state.executors["funding.refund"] = funder.execute_refund

    def caller(guard: str) -> Any:
        accounts = getattr(app.state, "accounts", None)
        aid = accounts.session_account(guard) if accounts is not None else None
        return accounts.get(aid) if accounts is not None and aid else None

    def owns_item(item_id: str, account: Any) -> bool:
        """The caller may see this item's money: the same ownership as its campaign
        approval (``approval_refusal``: the account whose run it is, or the studio's own
        work for a signed-in caller). Unknown items are nobody's. Call under conn.lock."""
        row = conn.execute("SELECT id FROM approvals WHERE item_id = ? AND action = "
                           "'campaign.create' ORDER BY ts DESC LIMIT 1", (item_id,)).fetchone()
        if row is None:
            return False
        try:
            pending = Pending.load(conn, row[0])
        except ApprovalError:
            return False
        return app.state.approval_refusal(pending, account) is None

    def pending_for(approval_id: str, guard: str) -> tuple[Pending, dict[str, Any]]:
        """An open ``campaign.create`` approval the caller may sign, and its payload."""
        if not APPROVAL_ID.fullmatch(approval_id):
            raise FundingRefused("not found", "not_found")
        with conn.lock:
            try:
                pending = Pending.load(conn, approval_id)
            except ApprovalError:
                raise FundingRefused("not found", "not_found") from None
            if app.state.approval_refusal(pending, caller(guard)) is not None:
                raise FundingRefused("not found", "not_found")
            status, superseded = conn.execute(
                "SELECT status, superseded_by FROM approvals WHERE id = ?",
                (approval_id,)).fetchone()
            row = conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                               (approval_id,)).fetchone()
        if pending.action != "campaign.create" or row is None:
            raise FundingRefused("only a campaign creation is funded", "not_fundable")
        if status != "pending" or superseded:
            raise FundingRefused("this approval is no longer open", "not_pending")
        return pending, json.loads(row[0])

    def refused(exc: Exception) -> JSONResponse:
        code = getattr(exc, "code", "") or "error"
        status = {"not_found": 404, "not_fundable": 409, "not_pending": 409,
                  "not_approved_yet": 409}.get(code, 409)
        if isinstance(exc, PayPalError):
            return JSONResponse({"error": "paypal", "paypal_error": exc.name,
                                 "paypal_issue": exc.issue, "reason": exc.reason}, 502)
        return JSONResponse({"error": code}, status)

    @app.get("/api/paypal/config")
    def paypal_config(request: Request) -> Response:
        if session_of(request) is None:
            return JSONResponse({"error": "login_required"}, 401)
        return JSONResponse({"enabled": True, "adapter": settings.paypal.adapter,
                             "client_id": settings.paypal.client_id
                             if settings.paypal.adapter == "rest" else "",
                             "fee_pct": settings.paypal.fee_pct_text,
                             "sandbox": True}, headers=NO_STORE)

    @app.post("/api/funding/{approval_id}/order")
    async def create_order(approval_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            asked = (await request.json()).get("days")
        except (ValueError, AttributeError):
            asked = None
        try:
            pending, payload = pending_for(approval_id, guard)
            ad_hash = payload_sha256(ad_payload(payload))
            if ad_hash != pending.content_sha256:
                raise FundingRefused("this approval already carries a funding", "funded")
            for f in funding.of_item(conn, pending.item_id):  # one open order per ad
                if f.state in ("created", "payer_approved") and f.content_sha256 == ad_hash \
                        and f.order_id:
                    return JSONResponse(view(f), headers=NO_STORE)
            campaign = payload.get("campaign") or {}
            step = funder.money.open(
                run_id=pending.run_id, item_id=pending.item_id, content_sha256=ad_hash,
                currency=str(campaign.get("currency", "")),
                daily_budget_minor=int(campaign.get("daily_budget_minor", 0)),
                days=_days(payload, asked), fee_pct=settings.paypal.fee_pct,
                max_daily_minor=to_minor(settings.max_daily_budget,
                                         str(campaign.get("currency", ""))))
        except (FundingRefused, FundingError, PayPalError) as exc:
            return refused(exc)
        out = view(step.funding)
        out["approve_url"] = step.result.approve_url if step.result else ""
        return JSONResponse(out, 201, headers=NO_STORE)

    def payer_approved(approval_id: str, guard: str, *, mock: bool) -> Response:
        try:
            pending, payload = pending_for(approval_id, guard)
            ad_hash = payload_sha256(ad_payload(payload))
            fs = [f for f in funding.of_item(conn, pending.item_id)
                  if f.content_sha256 == ad_hash and f.order_id
                  and f.state in ("created", "payer_approved")]
            if not fs:
                raise FundingRefused("no PayPal order for this approval", "no_order")
            f = fs[-1]
            if mock:
                if funder.fake is None:
                    raise FundingRefused("not found", "not_found")
                funder.fake.approve(str(f.order_id))
            if f.state == "created" and funder.money.sync_order(f.id) is None:
                raise FundingRefused("PayPal has not approved the order yet",
                                     "not_approved_yet")
            f = funding.get(conn, f.id)
            fresh, _ = attach_funding(conn, pending.id, payload, f.block(), ledger=ledger)
        except (FundingRefused, FundingError, PayPalError) as exc:
            return refused(exc)
        except ApprovalError as exc:
            return JSONResponse({"error": exc.code}, exc.status)
        return JSONResponse({**view(f), "approval_id": fresh.id,
                             "funding_sha256": fresh.funding_sha256}, headers=NO_STORE)

    @app.post("/api/funding/{approval_id}/payer-approved")
    def payer_approved_route(approval_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        return payer_approved(approval_id, guard, mock=False)

    @app.post("/api/funding/{approval_id}/mock-approve")
    def mock_approve(approval_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not funder.mock:
            return JSONResponse({"error": "not_found"}, 404)
        return payer_approved(approval_id, guard, mock=True)

    @app.get("/api/funding/item/{item_id}")
    def funding_of_item(item_id: str, request: Request) -> Response:
        sid = session_of(request)
        if sid is None:
            return JSONResponse({"error": "login_required"}, 401)
        if not ITEM_ID.fullmatch(item_id):
            return JSONResponse({"error": "not_found"}, 404)
        with conn.lock:
            if not owns_item(item_id, caller(sid)):
                return JSONResponse({"error": "not_found"}, 404)  # as if it did not exist
            out = [{**view(f), "moves": funding.moves(conn, f.id)}
                   for f in funding.of_item(conn, item_id)]
        return JSONResponse({"fundings": out}, headers=NO_STORE)

    @app.post("/api/funding/{funding_id}/refund")
    async def request_refund(funding_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        account = caller(guard)
        if account is None or not account.sees_personal_data:
            return JSONResponse({"error": "not_found"}, 404)  # full accounts only
        if not FUNDING_ID.fullmatch(funding_id):
            return JSONResponse({"error": "not_found"}, 404)
        with conn.lock:
            try:
                item = funding.get(conn, funding_id).item_id
            except FundingError:
                return JSONResponse({"error": "not_found"}, 404)
            if not owns_item(item, account):
                return JSONResponse({"error": "not_found"}, 404)
        try:
            amount = (await request.json()).get("amount_minor")
        except (ValueError, AttributeError):
            amount = None
        if not isinstance(amount, int) or isinstance(amount, bool):
            return JSONResponse({"error": "refund_amount"}, 400)
        try:
            pending, payload = funder.request_refund(funding_id, amount)
        except (FundingRefused, FundingError) as exc:
            return refused(exc)
        return JSONResponse({"approval_id": pending.id, "content": payload,
                             "content_sha256": pending.content_sha256}, 201)

    @app.post("/webhooks/paypal")
    async def paypal_webhook(request: Request) -> Response:
        body = await request.body()
        try:
            applied = webhooks.apply(conn, ledger, funder.verifier, request.headers, body,
                                     lookup=funder.lookup)
        except webhooks.WebhookRefused as exc:
            LOG.warning("PayPal webhook refused: %s", exc.code)
            # PayPal's API could not be read: nothing was stored, so PayPal's retry of
            # this delivery redoes the check
            return JSONResponse({"error": exc.code},
                                503 if exc.code == "lookup_failed" else 400)
        return JSONResponse({"ok": True, "status": applied.status})


def view(f: funding.Funding) -> dict[str, Any]:
    q = f.quote
    return {"funding_id": f.id, "item_id": f.item_id, "state": f.state,
            "order_id": f.order_id, "authorization_id": f.authorization_id,
            "capture_id": f.capture_id, "currency": f.currency, "days": f.days,
            "budget": q.amount("budget"), "fee": q.amount("fee"), "total": q.amount(),
            "fee_pct": format(f.fee_pct.normalize(), "f"), "total_minor": f.total_minor,
            "captured_minor": f.captured_minor, "refunded_minor": f.refunded_minor,
            "content_sha256": f.content_sha256}
