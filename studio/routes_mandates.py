"""The "Mandates" block of ``/account`` (Assistants & API): prepare and revoke a signed
mandate (``studio.mandates``).

- ``POST /account/mandates/prepare`` (same-origin, CSRF field, live session, writable):
  the form's limits become a pending ``mandate.grant`` approval; the owner is sent to the
  studio's signing screen (``/studio?approval=<id>``), which lists every limit in plain
  words before the same Ed25519 signature as any other approval. Signing stores it
  (the ``mandate.grant`` executor, ``mandates.activate``).
- ``POST /account/mandates/revoke`` (same checks): revokes one of the account's own
  mandates and leaves a ``mandate.revoke`` receipt.
- The block is shown to accounts that may grant one (``mandates.may_grant``: never the
  shared judge account, and no trial account while the Meta adapter is live); the same
  rule is checked again by ``mandates.request``, by the signing route and when a run
  would use the mandate. No bearer-token path reaches any of this.
- **Saved PayPal (Vault v3, PayPal on only).** ``POST /account/paypal/vault/start`` sends
  the owner to PayPal to approve saving their PayPal (buyer present); PayPal returns them
  to ``GET /account/paypal/vault/return``, where the setup token becomes a vault token
  (``studio.paypal.vault``). ``POST /account/paypal/vault/delete`` stops charges at once
  and deletes it at PayPal. A mandate prepared with "pay from my saved PayPal" signs a
  ``funding`` block naming that saved PayPal by SHA-256, with a per-charge cap, a cap per
  period, the period and the campaign kinds.
"""

from __future__ import annotations

import html
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from studio import auth, mandates
from studio.meta import objectives
from studio.meta.adapter import AdapterError
from studio.receipts import MANDATE_KINDS, MANDATE_MAX_PERIOD_DAYS, Ledger
from studio.settings import Settings

NO_STORE = {"Cache-Control": "no-store"}
MAX_FIELDS = 50  # checkboxes: up to 6 objectives, 10 tokens and 3 campaign kinds


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any) -> None:
    from studio.app import COOKIE, _same_origin

    app.state.executors[mandates.GRANT] = (
        lambda verified, payload: mandates.activate(conn, verified, payload))

    def currency() -> str:
        adapter = getattr(app.state, "adapter", None)
        try:
            return str(adapter.account_info()["currency"]) if adapter is not None else \
                settings.account_currency
        except AdapterError:
            return settings.account_currency

    previous = getattr(app.state, "account_extras", None)

    def extras(account: Any) -> dict[str, Any]:
        out = dict(previous(account)) if previous is not None else {}
        show = not account.is_demo and mandates.may_grant(account, settings.meta_adapter)
        now = time.time()
        tokens = [t for t in app.state.assistants.tokens.listing(account.id)
                  if t.active(now)] if show else []
        out.update({
            "mandate_show": show,
            "mandate_list": [m.view(now=now) for m in mandates.of_account(conn, account.id)]
            if show else [],
            "mandate_goals": objectives.GOALS, "mandate_tokens": [t.view() for t in tokens],
            "mandate_labels": {t.id: t.label for t in
                               app.state.assistants.tokens.listing(account.id)},
            "mandate_currency": currency(), "mandate_ceiling": settings.max_daily_budget,
            "mandate_max_campaigns": mandates.MAX_CAMPAIGNS,
            "mandate_max_days": mandates.MAX_DAYS})
        funder = getattr(app.state, "funder", None)
        out["mandate_paypal"] = bool(show and funder is not None)
        out["mandate_vaults"] = [v.view() for v in funder.vault.of_account(account.id)] \
            if show and funder is not None else []
        out["mandate_kinds"] = list(MANDATE_KINDS)
        out["mandate_max_period"] = MANDATE_MAX_PERIOD_DAYS
        return out

    app.state.account_extras = extras

    async def posted(request: Request) -> tuple[str, Any, dict[str, list[str]]] | Response:
        """Same-origin, CSRF field, live session, writable; every value of every field."""
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        try:
            form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True,
                            max_num_fields=MAX_FIELDS)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid_form"}, 400)
        if not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                 (form.get(auth.CSRF_FIELD) or [""])[0]):
            return JSONResponse({"error": "csrf"}, 403)
        cookie = request.cookies.get(COOKIE)
        sid = app.state.sessions.check(cookie)
        account = app.state.sessions.account_of(cookie) if sid else None
        if not sid or account is None:
            return JSONResponse({"error": "login_required"}, 401)
        if settings.read_only:
            return JSONResponse({"error": "read_only"}, 503)
        return sid, account, form

    def refuse(request: Request, sid: str, account: Any, code: str, status: int,
               fields: list[str] | None = None) -> Response:
        response: Response = app.state.account_page(
            request, sid, account, status=status, errors={"mandates": ("err." + code, "")},
            extra={"mandate_problems": fields or []})
        return response

    @app.post("/account/mandates/prepare")
    async def prepare(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, form = got

        def one(name: str) -> str:
            return (form.get(name) or [""])[0]

        if account.is_demo or not mandates.may_grant(account, settings.meta_adapter):
            return refuse(request, sid, account, "mandate_not_allowed", 403)
        funder = getattr(app.state, "funder", None)
        block = None
        if one("pay") == "1":
            # pay from the saved PayPal: the mandate signs it by SHA-256 (never the id)
            digest = ""
            if funder is not None:
                try:
                    saved = funder.vault.get(one("vault_ref"))
                    if saved.account_id == account.id and saved.status == "active":
                        digest = saved.token_sha256
                except Exception:  # noqa: BLE001,S110 - an unknown reference: "vault"
                    pass
            block = mandates.funding_block(
                vault_sha256=digest, max_charge=one("max_charge"),
                funding_cap=one("funding_cap"), period_days=one("period_days"),
                kinds=form.get("kind", []), currency=currency())
        body = mandates.build(
            account.id, token_ids=form.get("token", []), objectives_=form.get("objective", []),
            countries=one("countries"), landing_hosts=one("hosts"),
            max_daily_budget=one("max_daily_budget"), currency=currency(),
            max_campaigns=one("max_campaigns"), days=one("days"), funding=block)
        try:
            pending = mandates.request(
                conn, ledger, account, body, ceiling=settings.max_daily_budget,
                currency=currency(), meta_adapter=settings.meta_adapter,
                vault_active=(lambda d: funder.vault_active(account.id, d))
                if funder is not None else None)
        except mandates.MandateError as exc:
            return refuse(request, sid, account, exc.code, exc.status, exc.fields)
        return RedirectResponse(f"/studio?approval={pending.id}", 303, headers=NO_STORE)

    # -- the saved PayPal (Vault v3) ---------------------------------------------------------

    def vault_service() -> Any:
        funder = getattr(app.state, "funder", None)
        return funder

    def base(request: Request) -> str:
        return (settings.public_url or str(request.base_url)).rstrip("/")

    @app.post("/account/paypal/vault/start")
    async def vault_start(request: Request) -> Response:
        got = await posted(request)  # same-origin, CSRF and session before anything
        if isinstance(got, Response):
            return got
        funder = vault_service()
        if funder is None:
            return JSONResponse({"error": "not_found"}, 404)
        sid, account, form = got
        if account.is_demo or not mandates.may_grant(account, settings.meta_adapter):
            return refuse(request, sid, account, "mandate_not_allowed", 403)
        from studio.paypal.client import PayPalError
        from studio.paypal.vault import VaultError

        try:
            ref, approve = funder.vault.start(
                account.id, return_url=base(request) + "/account/paypal/vault/return",
                cancel_url=base(request) + "/account?paypal=cancelled#mandates")
        except (PayPalError, VaultError):
            return refuse(request, sid, account, "paypal_vault_failed", 502)
        if funder.fake is not None:  # mock mode: the fake plays the buyer's approval
            funder.fake.approve_setup(funder.vault.setup_token_of(account.id, ref))
            return RedirectResponse(f"/account/paypal/vault/return?vault_ref={ref}", 303,
                                    headers=NO_STORE)
        return RedirectResponse(approve, 303, headers=NO_STORE)

    @app.get("/account/paypal/vault/return")
    def vault_return(request: Request, vault_ref: str = "",
                     approval_token_id: str = "", bounced: str = "") -> Response:
        funder = vault_service()
        if funder is None:
            return JSONResponse({"error": "not_found"}, 404)
        cookie = request.cookies.get(COOKIE)
        sid = app.state.sessions.check(cookie)
        account = app.state.sessions.account_of(cookie) if sid else None
        cross_site = request.headers.get("sec-fetch-site") == "cross-site"
        if (not sid or account is None) and cross_site and not bounced:
            # PayPal's redirect back is cross-site, so the browser withholds the
            # SameSite=Strict session cookie. Refresh to the same URL once: that
            # navigation starts on this site and carries the cookie.
            query = urlencode({"vault_ref": vault_ref[:40],
                               "approval_token_id": approval_token_id[:64], "bounced": "1"})
            target = html.escape(f"/account/paypal/vault/return?{query}")
            return HTMLResponse(
                "<!doctype html><html><head><meta name=\"robots\" content=\"noindex\">"
                f"<meta http-equiv=\"refresh\" content=\"0;url={target}\"></head></html>",
                headers=NO_STORE)
        if not sid or account is None:  # signing in brings the owner back here
            again = urlencode({"vault_ref": vault_ref[:40],
                               "approval_token_id": approval_token_id[:64]})
            target = quote(f"/account/paypal/vault/return?{again}", safe="/")
            return RedirectResponse(f"/login?next={target}", 303, headers=NO_STORE)
        if settings.read_only:
            return JSONResponse({"error": "read_only"}, 503)
        from studio.paypal.client import PayPalError
        from studio.paypal.vault import VaultError

        try:
            funder.vault.finish(account.id, vault_ref[:40], approval_token_id[:64])
        except VaultError as exc:
            return refuse(request, sid, account, "paypal_vault_" + exc.code, exc.status)
        except PayPalError:
            return refuse(request, sid, account, "paypal_vault_failed", 502)
        return RedirectResponse("/account?paypal=saved#mandates", 303, headers=NO_STORE)

    @app.post("/account/paypal/vault/delete")
    async def vault_delete(request: Request) -> Response:
        got = await posted(request)  # same-origin, CSRF and session before anything
        if isinstance(got, Response):
            return got
        funder = vault_service()
        if funder is None:
            return JSONResponse({"error": "not_found"}, 404)
        sid, account, form = got
        from studio.paypal.vault import VaultError

        try:
            funder.vault.delete(account.id, (form.get("vault_ref") or [""])[0][:40])
        except VaultError as exc:
            return refuse(request, sid, account, "paypal_vault_" + exc.code, exc.status)
        return RedirectResponse("/account?paypal=deleted#mandates", 303, headers=NO_STORE)

    @app.post("/account/mandates/revoke")
    async def revoke(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, form = got
        if not mandates.revoke(conn, ledger, account, (form.get("mandate") or [""])[0]):
            return refuse(request, sid, account, "mandate_unknown", 404)
        return RedirectResponse("/account?mandate=revoked#mandates", 303, headers=NO_STORE)
