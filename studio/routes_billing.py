"""``/account/billing/checkout|portal`` and ``/webhooks/stripe`` (``studio.billing``)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response

from studio import auth, billing
from studio.receipts import Ledger
from studio.routes_account import _fields
from studio.settings import Settings

log = logging.getLogger(__name__)


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn) -> None:  # noqa: ANN001
    from studio.app import COOKIE, _same_origin

    billing.ensure_schema(conn)
    cfg = settings.billing
    app.state.stripe = billing.StripeApi(cfg) if cfg.enabled else None

    def base(request: Request) -> str:
        return (settings.public_url or str(request.base_url)).rstrip("/")

    async def signed_in_form(request: Request):  # noqa: ANN202
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        fields = _fields(await request.body())
        if fields is None or not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                                   fields.get(auth.CSRF_FIELD)):
            return JSONResponse({"error": "csrf"}, 403)
        account = app.state.sessions.account_of(request.cookies.get(COOKIE))
        if account is None:
            return JSONResponse({"error": "login_required"}, 401)
        if settings.read_only:
            return JSONResponse({"error": "read_only"}, 503)
        if app.state.stripe is None:
            return JSONResponse({"error": "billing_off"}, 404)
        if account.is_demo:
            return JSONResponse({"error": "demo_account"}, 403)
        return account

    def failed(code: str) -> Response:
        log.warning("billing: %s", code)
        return RedirectResponse(f"/account?billing_error={code[:40]}#billing", 303)

    @app.post("/account/billing/checkout")
    async def checkout(request: Request) -> Response:
        account = await signed_in_form(request)
        if isinstance(account, Response):
            return account
        with conn.lock:
            current = billing.of(conn, account.id)
        try:
            url = await run_in_threadpool(
                app.state.stripe.checkout, account_id=account.id, email=account.email,
                customer_id=current.customer_id if current else "",
                success_url=base(request) + "/account?done=billing#billing",
                cancel_url=base(request) + "/account#billing", lang=account.lang)
        except billing.BillingError as exc:
            return failed(exc.code)
        return RedirectResponse(url, 303)

    @app.post("/account/billing/portal")
    async def portal(request: Request) -> Response:
        account = await signed_in_form(request)
        if isinstance(account, Response):
            return account
        with conn.lock:
            current = billing.of(conn, account.id)
        if current is None or not current.customer_id:
            return failed("no_customer")
        try:
            url = await run_in_threadpool(app.state.stripe.portal,
                                          customer_id=current.customer_id,
                                          return_url=base(request) + "/account#billing")
        except billing.BillingError as exc:
            return failed(exc.code)
        return RedirectResponse(url, 303)

    @app.post("/webhooks/stripe")
    async def webhook(request: Request) -> Response:
        if not cfg.enabled:
            return JSONResponse({"error": "billing_off"}, 404)
        body = await request.body()
        if len(body) > 512 * 1024 or not billing.verify_signature(
                body, request.headers.get("stripe-signature", ""), cfg.webhook_secret):
            return JSONResponse({"error": "bad_signature"}, 400)
        event = billing.event_of(body)
        if event is None:
            return JSONResponse({"error": "invalid_event"}, 400)
        done = billing.apply(conn, event, live_key=cfg.live)
        ledger.append(billing.RUN_ID, agent="billing", kind="tool_call", details={
            "tool": "stripe.webhook", "side_effects": "read", "event": event.get("type"),
            "event_id": event.get("id"), "duplicate": done.duplicate,
            "account_id": done.account_id, "tier": done.tier})
        store = app.state.accounts
        account = store.get(done.account_id) if done.account_id else None
        if account is not None:
            if done.email and not account.email:
                account = store.update_profile(account.id, {**account.profile(),
                                                             "email": done.email})
            if done.tier and done.tier != account.tier and not account.is_demo:
                account = store.set_tier(account.username, done.tier)
            if done.mail:
                app.state.notifier.security(done.mail, account)
        return JSONResponse({"ok": True})

