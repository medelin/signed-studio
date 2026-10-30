"""``/account/billing/checkout|portal`` and ``/webhooks/stripe`` (``studio.billing``), and
the account page's billing view: plans, prices (``studio.pricing``) and consent texts."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response

from studio import auth, billing, pricing
from studio.receipts import Ledger
from studio.routes_account import _fields
from studio.settings import Settings
from studio.ui import i18n, legal_texts

log = logging.getLogger(__name__)

# checkbox names, value "1": the payment (with the Terms; every plan) and the immediate
# start (a private person only: it ends the 14-day right to cancel for the part provided)
CONSENTS = ("recurring_consent", "immediate_start")


def fee_clause(lang: str, plan: str, cfg: billing.BillingConfig) -> str:
    """", plus 5% of the ad spend …" or "" when no ad-spend fee is charged."""
    if not cfg.spend_fee:
        return ""
    key = "billing.consent.fee_once" if plan == "once" else "billing.consent.fee"
    return str(i18n.text(lang, key, pct=cfg.fee_pct_text, days=billing.ONCE_DAYS))


def consent_sentence(lang: str, plan: str, amount: str, cfg: billing.BillingConfig) -> str:
    """The payment consent for ``plan`` as plain text: the checkbox label on the account
    page and the line above Stripe's pay button."""
    fee = fee_clause(lang, plan, cfg)
    key = {"monthly": "billing.consent.recurring", "once": "billing.consent.once"}.get(
        plan, "billing.consent.term")
    if not amount:
        key += "_no_price"
    return str(i18n.text(lang, key, price=amount, fee=fee, days=billing.ONCE_DAYS,
                         months=billing.PLAN_MONTHS.get(plan, 1)))


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn) -> None:  # noqa: ANN001
    from studio.app import COOKIE, _same_origin

    billing.ensure_schema(conn)
    cfg = settings.billing
    app.state.stripe = billing.StripeApi(cfg) if cfg.enabled else None
    # read at call time, so a test (or a later reconfiguration) can swap app.state.stripe
    app.state.price = pricing.PriceCache(lambda: app.state.stripe, settings.price_display)

    def billing_view(request: Request, account: Any) -> dict[str, Any]:
        """The billing section's plans (cached prices only), consent texts and defaults."""
        lang, cache = request.state.lang, app.state.price
        plans = []
        for plan in cache.plans():
            amount = cache.amount(lang, plan, fetch=False)
            plans.append({"plan": plan, "amount": amount,
                          "months": billing.PLAN_MONTHS[plan], "days": billing.ONCE_DAYS,
                          "per_month": cache.per_month(lang, plan, fetch=False),
                          "free_months": cache.free_months(plan, fetch=False),
                          "sentence": consent_sentence(lang, plan, amount, cfg)})
        profile = account.profile()
        return {"billing_plans": plans,
                "billing_fee_pct": cfg.fee_pct_text if cfg.spend_fee else "",
                "billing_form": {"plan": "monthly",
                                 "buyer": profile.get("advertiser_type", ""),
                                 "tax_id": profile.get("tax_id", "")},
                "billing_generic": str(i18n.text(
                    lang, "billing.consent.generic",
                    fee=fee_clause(lang, "monthly", cfg)))}

    app.state.billing_view = billing_view

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
        """Before Stripe is called: a configured ``plan``, the ``buyer`` type and the
        consents that buyer gives: the payment with the Terms (everyone) and, for a private
        person, the immediate start (which ends the 14-day right to cancel for the part of
        the service already provided; a company has no such right). A ``billing.consent``
        receipt records them."""
        account = await signed_in_form(request)
        if isinstance(account, Response):
            return account
        fields = _fields(await request.body()) or {}
        plan = fields.get("plan", "monthly")
        buyer = fields.get("buyer", "")
        tax_id = " ".join(fields.get("tax_id", "").split())[:40]
        error: tuple[str, str] | None = None
        if plan not in cfg.plans:
            error = ("billing.plan.invalid", "plan")
        elif buyer not in billing.BUYERS:
            error = ("billing.buyer.missing", "buyer")
        else:
            needed = CONSENTS if buyer == "person" else CONSENTS[:1]
            missing = [name for name in needed if fields.get(name) != "1"]
            if missing:
                which = missing[0] if len(missing) == 1 else "both"
                error = ("billing.consent.missing_" + which, which)
        if error is not None:
            sid = app.state.sessions.check(request.cookies.get(COOKIE)) or ""
            return app.state.account_page(request, sid, account, status=400,
                                          errors={"billing": error}, extra={
                                              "billing_form": {"plan": plan, "buyer": buyer,
                                                               "tax_id": tax_id}})
        lang = request.state.lang
        price = await run_in_threadpool(app.state.price.get, plan)
        amount = await run_in_threadpool(app.state.price.amount, lang, plan)
        with conn.lock:
            current = billing.of(conn, account.id)
        terms_updated = int(legal_texts.UPDATED.replace("-", ""))  # a dated string would be
        terms_sha256 = legal_texts.digest("terms")                 # redacted as a phone
        consent = {
            "terms_updated": terms_updated, "terms_sha256": terms_sha256, "plan": plan,
            "buyer": buyer, "price_id": cfg.plans[plan], "interval": "month",
            "interval_count": billing.PLAN_MONTHS[plan] if plan != "once" else 0,
            "amount_minor": price.amount if price else 0,
            "currency": price.currency if price else "",
            "spend_fee_pct": cfg.fee_pct_text if cfg.spend_fee else "0",
            "recurring_consent": plan != "once", "immediate_start": buyer == "person"}
        # the full consent stays private (billing_consents); the receipts log, readable by
        # every signed-in account, gets the plan, the terms and the hash of the record
        digest = billing.store_consent(conn, account.id, consent)
        ledger.append(billing.RUN_ID, agent="owner", kind="tool_call", details={
            "tool": "billing.consent", "side_effects": "local", "plan": plan,
            "terms_updated": terms_updated, "terms_sha256": terms_sha256,
            "consent_sha256": digest})
        try:
            url = await run_in_threadpool(
                app.state.stripe.checkout, account_id=account.id, email=account.email,
                customer_id=current.customer_id if current else "",
                success_url=base(request) + "/account?done=billing#billing",
                cancel_url=base(request) + "/account#billing", lang=account.lang,
                consent_text=consent_sentence(lang, plan, amount, cfg), plan=plan,
                buyer=buyer, tax_id=tax_id)
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
        done = billing.apply(conn, event, live_key=cfg.live, months_of={
            pid: billing.PLAN_MONTHS[p] for p, pid in cfg.plans.items() if p != "once"})
        ledger.append(billing.RUN_ID, agent="billing", kind="tool_call", details={
            "tool": "stripe.webhook", "side_effects": "read", "event": event.get("type"),
            "event_id": event.get("id"), "duplicate": done.duplicate, "tier": done.tier})
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

