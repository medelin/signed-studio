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
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from studio import auth, mandates
from studio.meta import objectives
from studio.meta.adapter import AdapterError
from studio.receipts import Ledger
from studio.settings import Settings

NO_STORE = {"Cache-Control": "no-store"}
MAX_FIELDS = 40  # checkboxes: up to 6 objectives and 10 tokens


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
        body = mandates.build(
            account.id, token_ids=form.get("token", []), objectives_=form.get("objective", []),
            countries=one("countries"), landing_hosts=one("hosts"),
            max_daily_budget=one("max_daily_budget"), currency=currency(),
            max_campaigns=one("max_campaigns"), days=one("days"))
        try:
            pending = mandates.request(conn, ledger, account, body,
                                       ceiling=settings.max_daily_budget, currency=currency(),
                                       meta_adapter=settings.meta_adapter)
        except mandates.MandateError as exc:
            return refuse(request, sid, account, exc.code, exc.status, exc.fields)
        return RedirectResponse(f"/studio?approval={pending.id}", 303, headers=NO_STORE)

    @app.post("/account/mandates/revoke")
    async def revoke(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, form = got
        if not mandates.revoke(conn, ledger, account, (form.get("mandate") or [""])[0]):
            return refuse(request, sid, account, "mandate_unknown", 404)
        return RedirectResponse("/account?mandate=revoked#mandates", 303, headers=NO_STORE)
