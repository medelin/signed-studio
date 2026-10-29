"""The public tour: ``/try`` (``?lang=`` or the cookie), ``/pl/try``, ``/try/data.json`` and
``/try/img/<blob>.png``. No account, no session needed, read-only.

Everything comes from the committed tour file of one replay set (``studio.tour``): no model,
Meta or Google call, no database read or write, no signing, nothing from the settings
beyond whether sign-in is offered. Images are served only from the set's own recorded
outputs (an allowlist of blob names); anything else is a 404.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from studio import replay, tour
from studio.settings import Settings

# ``private``: the gate may attach the CSRF cookie to any first response, which a shared
# cache must never store and hand to someone else; browsers still cache it.
CACHE = "private, max-age=3600"
IMMUTABLE = "private, max-age=31536000, immutable"
_IMG = re.compile(r"[0-9a-f]{32}\.png")


def register(app: FastAPI, settings: Settings, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None],
             set_name: str = tour.DEFAULT_SET) -> None:

    def page(request: Request, lang: str) -> Response:
        data = tour.load(set_name)
        other = "en" if lang == "pl" else "pl"
        return templates.TemplateResponse(request, "try.html", {
            # the footer names the tour's own (throwaway) ledger, not the live one
            "seq": data["manifest"]["seq"], "epoch": data["epoch"],
            "logged_in": session_of(request) is not None,
            "login_enabled": settings.judge_login_enabled,
            "read_only": settings.read_only,
            "page_lang": lang, "tour": data, "repo_url": tour.REPO_URL,
            "other_lang": other, "other_href": "/pl/try" if other == "pl" else "/try?lang=en",
            "variant": data["review"].get("variant_index") or 0,
            "approval_message": tour.public(set_name)["canonical"]["message"],
        })

    @app.get("/try", response_class=HTMLResponse)
    def try_page(request: Request) -> Response:
        return page(request, request.state.lang)

    @app.get("/pl/try", response_class=HTMLResponse)
    def try_page_pl(request: Request) -> Response:
        return page(request, "pl")

    @app.get("/try/data.json")
    def try_data() -> Response:
        return JSONResponse(tour.public(set_name), headers={"Cache-Control": CACHE})

    @app.get("/try/img/{name}")
    def try_image(name: str) -> Response:
        if not _IMG.fullmatch(name) or name not in tour.image_names(set_name):
            return JSONResponse({"error": "not_found"}, 404)
        path = replay.BLOBS / name
        if path.resolve().parent != replay.BLOBS.resolve() or not path.is_file():
            return JSONResponse({"error": "not_found"}, 404)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": IMMUTABLE})
