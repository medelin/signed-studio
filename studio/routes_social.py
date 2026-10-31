"""``/auth/{google,facebook}/start|callback``: sign in, register or link a provider identity
(``studio.social_login``)."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from studio import accounts, auth, routes_account, social_login
from studio.settings import Settings
from studio.ui import i18n

log = logging.getLogger(__name__)


def _redirect_uri(request: Request, settings: Settings, provider: str) -> str:
    base = settings.public_url or str(request.base_url)
    return base.rstrip("/") + f"/auth/{provider}/callback"


def register(app: FastAPI, settings: Settings) -> None:
    states = social_login.States()
    app.state.social_states = states
    app.state.external_signins = {}  # account id -> time of its last provider sign-in

    def client(provider: str) -> tuple[str, str] | None:
        return settings.social.client(provider) if provider in social_login.PROVIDERS else None

    def signed_in(request: Request) -> accounts.Account | None:
        from studio.app import COOKIE
        return app.state.sessions.account_of(request.cookies.get(COOKIE))

    def failed(request: Request, code: str) -> Response:
        log.info("social sign-in refused: %s", code)
        return RedirectResponse(f"/login?social_error={code}", 303)

    @app.get("/auth/{provider}/start")
    def start(request: Request, provider: str, next: str = "") -> Response:  # noqa: A002
        pair = client(provider)
        if pair is None or settings.read_only:
            return failed(request, "unavailable")
        from studio.app import client_ip, limiter_key

        current = signed_in(request)
        try:
            state, pending = states.open(
                provider, auth.safe_next(next), current.id if current else "",
                client=limiter_key(client_ip(request, settings.trusted_proxies)))
        except social_login.StatesFull:
            return failed(request, "busy")
        response = RedirectResponse(social_login.authorize_url(
            provider, pair[0], _redirect_uri(request, settings, provider), state, pending), 303)
        response.set_cookie(social_login.STATE_COOKIE, state,
                            max_age=int(social_login.STATE_TTL_S), httponly=True,
                            samesite="lax", secure=routes_account._secure(request, settings),
                            path="/auth/")
        return response

    @app.get("/auth/{provider}/callback")
    def callback(request: Request, provider: str, code: str = "", state: str = "",
                 error: str = "") -> Response:
        pair = client(provider)
        if pair is None:
            return failed(request, "unavailable")
        pending = states.take(state, request.cookies.get(social_login.STATE_COOKIE), provider)
        if pending is None:
            return failed(request, "state")
        if error or not code:
            return failed(request, "cancelled")
        try:
            who = social_login.fetch_identity(provider, pair, code,
                                              _redirect_uri(request, settings, provider),
                                              pending, getattr(app.state, "social_http", None))
        except social_login.ProviderError as exc:
            log.warning("social sign-in: %s", exc)
            return failed(request, "provider")
        store: accounts.Accounts = app.state.accounts
        owner = store.by_identity(provider, who.subject)
        lang = request.state.lang
        if pending.link_account:
            if owner is not None and owner.id != pending.link_account:
                return failed(request, "linked_elsewhere")
            try:
                store.link_identity(pending.link_account, provider, who.subject)
            except accounts.AccountError:
                return failed(request, "linked_elsewhere")
            app.state.external_signins[pending.link_account] = time.time()  # a re-auth too
            return _bounce("/account?done=linked#password", lang)
        if owner is None:
            owner = store.create_external(provider, who.subject, email=who.email,
                                          display_name=who.name)
            app.state.notifier.security("welcome", owner)
        if owner.status != "active" or owner.is_demo:
            return failed(request, "inactive")
        app.state.external_signins[owner.id] = time.time()
        if owner.totp_enabled:
            token = app.state.challenges.open(owner.id, pending.nxt,
                                              gen=store.credential_gen(owner.id))
            response = _bounce("/login/2fa", lang)
            response.set_cookie(routes_account.CHALLENGE_COOKIE, token,
                                max_age=int(accounts.Challenges.TTL_S), httponly=True,
                                samesite="strict",
                                secure=routes_account._secure(request, settings), path="/login")
            return response
        response = _bounce(pending.nxt, lang)
        routes_account.start_session(app, request, response, owner)
        return response


def _bounce(target: str, lang: str) -> HTMLResponse:
    response = HTMLResponse(social_login.bounce_page(target, i18n.text(lang, "social.continue")))
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(social_login.STATE_COOKIE, path="/auth/")
    return response


def external_fresh(app: FastAPI, account_id: str, now: float | None = None) -> bool:
    """The account's session began with a provider sign-in a short while ago."""
    at: Any = getattr(app.state, "external_signins", {}).get(account_id, 0.0)
    now = time.time() if now is None else now
    return now - float(at) < accounts.EXTERNAL_PASSWORD_WINDOW_S
