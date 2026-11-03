"""Help/FAQ (``/help``, ``/pl/help``), the cast gallery (``/style/cast``) and the HTML 404.

All server-rendered and usable without JavaScript: the FAQ is a ``<details>`` accordion with
one anchor per question; illustrations are inline SVG from ``studio.ui.cast``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from studio.receipts import Ledger
from studio.settings import Settings
from studio.ui import cast, help_texts, legal_texts

# Paths whose clients expect JSON, never an HTML page.
_API_PREFIXES = ("/api/", "/assets/", "/static/")


RICKROLL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

def register(app: FastAPI, settings: Settings, ledger: Ledger, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:
    templates.env.globals["scene"] = cast.scene
    templates.env.globals["group"] = cast.group
    templates.env.globals["people"] = cast.people
    templates.env.globals["rotation"] = cast.rotation
    templates.env.globals["cast"] = cast.CAST

    def base(request: Request) -> dict[str, Any]:
        return {"seq": ledger.seq(), "epoch": ledger.epoch,
                "logged_in": session_of(request) is not None,
                "login_enabled": settings.judge_login_enabled,
                "read_only": settings.read_only}

    def help_page(request: Request, lang: str) -> Response:
        return templates.TemplateResponse(request, "help.html", {
            **base(request), "page_lang": lang, "entries": help_texts.entries(lang),
            "other_href": "/help?lang=en" if lang == "pl" else "/pl/help",
            "other_lang": "en" if lang == "pl" else "pl",
            "owners": cast.BY_KEY,
        })

    @app.get("/help", response_class=HTMLResponse)
    def help_en(request: Request) -> Response:
        return help_page(request, request.state.lang)  # ?lang= or the cookie

    @app.get("/pl/help", response_class=HTMLResponse)
    def help_pl(request: Request) -> Response:
        return help_page(request, "pl")

    @app.get("/style/cast", response_class=HTMLResponse)
    def cast_page(request: Request) -> Response:
        response = templates.TemplateResponse(request, "cast.html", {
            **base(request), "noindex": True, "scenes": cast.SCENES})
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    facts = {"seller_name": settings.seller_name, "seller_number": settings.seller_number,
             "seller_address": settings.seller_address, "contact": legal_texts.CONTACT,
             # the studio fee on each campaign's PayPal funding (STUDIO_FEE_PCT)
             "fee_pct": settings.paypal.fee_pct_text}

    def legal_page(request: Request, name: str) -> Response:
        title, sections = legal_texts.page(name, request.state.lang, facts)
        return templates.TemplateResponse(request, "legal.html", {
            **base(request), "title": title, "sections": sections,
            "anchors": legal_texts.ANCHORS.get(name, ()), "updated": legal_texts.UPDATED})

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request) -> Response:
        return legal_page(request, "privacy")

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request) -> Response:
        return legal_page(request, "terms")

    @app.get("/pricing", response_class=HTMLResponse)
    def pricing_page(request: Request) -> Response:
        """No plan: each campaign is funded through PayPal, budget plus the studio fee
        (``studio.paypal``, ``studio.funding``)."""
        return templates.TemplateResponse(request, "pricing.html", {
            **base(request), "fee_pct": settings.paypal.fee_pct_text, "seller": facts})

    @app.get("/data-deletion", response_class=HTMLResponse)
    def data_deletion(request: Request) -> Response:
        return legal_page(request, "data-deletion")

    @app.get("/k/judge-master-key")
    def judge_master_key() -> Response:
        """The "secret admin link" on the monitor in the demo video. There is no master
        key; whoever zooms in and follows it gets the classic instead."""
        return RedirectResponse(RICKROLL, 302)

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        accepts_html = "text/html" in request.headers.get("accept", "")
        if (exc.status_code != 404 or not accepts_html
                or request.url.path.startswith(_API_PREFIXES)):
            return await http_exception_handler(request, exc)
        return templates.TemplateResponse(request, "404.html", base(request), status_code=404)
