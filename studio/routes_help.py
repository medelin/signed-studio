"""Help/FAQ (``/help``, ``/pl/help``), the cast gallery (``/style/cast``) and the HTML 404.

All server-rendered and usable without JavaScript: the FAQ is a ``<details>`` accordion with
one anchor per question; illustrations are inline SVG from ``studio.ui.cast``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from studio.receipts import Ledger
from studio.settings import Settings
from studio.ui import cast, help_texts

# Paths whose clients expect JSON, never an HTML page.
_API_PREFIXES = ("/api/", "/assets/", "/static/")


def register(app: FastAPI, settings: Settings, ledger: Ledger, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:
    templates.env.globals["scene"] = cast.scene
    templates.env.globals["group"] = cast.group
    templates.env.globals["people"] = cast.people
    templates.env.globals["cast"] = cast.CAST

    def base(request: Request) -> dict[str, Any]:
        return {"seq": ledger.seq(), "epoch": ledger.epoch,
                "logged_in": session_of(request) is not None,
                "login_enabled": settings.judge_login_enabled,
                "read_only": settings.read_only}

    def help_page(request: Request, lang: str) -> Response:
        if request.query_params.get("lang") == "pl":
            lang = "pl"
        return templates.TemplateResponse(request, "help.html", {
            **base(request), "page_lang": lang, "t": help_texts.PAGE[lang],
            "entries": help_texts.entries(lang),
            "other_href": "/help" if lang == "pl" else "/pl/help",
            "other_lang": "en" if lang == "pl" else "pl",
            "people": cast.BY_KEY,
        })

    @app.get("/help", response_class=HTMLResponse)
    def help_en(request: Request) -> Response:
        return help_page(request, "en")

    @app.get("/pl/help", response_class=HTMLResponse)
    def help_pl(request: Request) -> Response:
        return help_page(request, "pl")

    @app.get("/style/cast", response_class=HTMLResponse)
    def cast_page(request: Request) -> Response:
        response = templates.TemplateResponse(request, "cast.html", {
            **base(request), "noindex": True, "scenes": cast.SCENES})
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def not_found(request: Request, exc: StarletteHTTPException) -> Response:
        accepts_html = "text/html" in request.headers.get("accept", "")
        if (exc.status_code != 404 or not accepts_html
                or request.url.path.startswith(_API_PREFIXES)):
            return await http_exception_handler(request, exc)
        return templates.TemplateResponse(request, "404.html", base(request), status_code=404)
