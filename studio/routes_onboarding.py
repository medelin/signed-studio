"""Onboarding: ``/start`` (``?lang=`` or the cookie) and ``/pl/start``, public, plus the
key that remembers the first-visit banner on ``/studio`` per account.

Six illustrated steps of the real flow (``studio.ui.onboarding_art``), server-rendered and
readable without JavaScript; ``static/onboarding.js`` turns them into a looping sequence.
Nothing is read from the database or the ledger beyond the footer's counters.

The banner's dismissal stays in the browser (``localStorage``, wrapped in try/catch): the
viewer preferences (``studio.prefs``) are cookies without a per-account key, and a new
column or table for one flag is not worth it. The key is a short hash of the account id,
so two accounts in one browser each see the banner once and no id is written to storage.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from studio import routes_account
from studio.receipts import Ledger
from studio.settings import Settings
from studio.ui import onboarding_art
from studio.ui.i18n_onboarding import STEPS

COOKIE = "studio_session"


def register(app: FastAPI, settings: Settings, ledger: Ledger, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None], cookie: str = COOKIE) -> None:
    templates.env.globals["onboarding_art"] = onboarding_art.art

    def banner_key(request: Request) -> str:
        """Per-account storage key suffix for the first-visit banner ("" when signed out)."""
        value = request.cookies.get(cookie)
        if session_of(request) is None:
            return ""
        account_of = getattr(app.state.sessions, "account_of", None)
        account = account_of(value) if account_of else None
        ref = account.id if account is not None else "session"
        return hashlib.sha256(f"onboarding|{ref}".encode()).hexdigest()[:16]

    templates.env.globals["onboarding_key"] = banner_key

    def page(request: Request, lang: str) -> Response:
        other = "en" if lang == "pl" else "pl"
        return templates.TemplateResponse(request, "start.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch,
            "logged_in": session_of(request) is not None,
            "login_enabled": routes_account.login_enabled(app, settings),
            "read_only": settings.read_only,
            "page_lang": lang, "steps": range(1, STEPS + 1),
            "other_lang": other,
            "other_href": "/pl/start" if other == "pl" else "/start?lang=en",
        })

    @app.get("/start", response_class=HTMLResponse)
    def start(request: Request) -> Response:
        return page(request, request.state.lang)

    @app.get("/pl/start", response_class=HTMLResponse)
    def start_pl(request: Request) -> Response:
        return page(request, "pl")
