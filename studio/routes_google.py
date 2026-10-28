"""Google Search routes (the optional second channel).

- ``GET /api/google/{item_id}``: the Search campaign a signed ``google.create`` made, its
  status and budget. A visitor without a session gets no Google resource names.
- ``POST /api/google/{item_id}/activate`` (session, same-origin, writable): a pending
  ``google.activate`` approval for a PAUSED campaign; the owner signs it like any other
  approval and only then does the executor enable ad, ad group and campaign.

The executors for ``google.create`` and ``google.activate`` are registered here; each runs
only for a consumed, signed approval (``studio.executor.authorize``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from studio.agents import google_buyer
from studio.agents.media_buyer import BuyError
from studio.google import adapter as google_adapter
from studio.receipts import Ledger, sha256_hex
from studio.routes_inbox import COMMENT_ID
from studio.settings import Settings

Guard = Callable[[Request], "JSONResponse | str"]


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             write_guard: Guard, session_of: Callable[[Request], str | None]) -> None:
    app.state.google = google_adapter.make(settings, conn=conn)
    app.state.executors["google.create"] = (
        lambda verified, payload: google_buyer.create(conn, ledger, app.state.google,
                                                      verified, payload))
    app.state.executors["google.activate"] = (
        lambda verified, payload: google_buyer.activate(conn, ledger, app.state.google,
                                                        verified, payload,
                                                        settings.max_daily_budget))

    @app.get("/api/google/{item_id}")
    def get_campaign(item_id: str, request: Request) -> Response:
        c = google_buyer.campaign(conn, item_id) if COMMENT_ID.fullmatch(item_id) else None
        if c is None:
            return JSONResponse({"error": "not_found"}, 404)
        resources = c.pop("resources")
        c.pop("customer_id")
        if session_of(request) is not None:
            c["resources"] = resources
        else:
            c["resources"] = {k: "sha256:" + sha256_hex(v)[:32] for k, v in resources.items()}
        return JSONResponse({**c, "ceiling": settings.max_daily_budget})

    @app.post("/api/google/{item_id}/activate")
    def request_activate(item_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not COMMENT_ID.fullmatch(item_id):
            return JSONResponse({"error": "not_found"}, 404)
        try:
            pending, payload = google_buyer.request_activation(
                conn, ledger, item_id, settings.max_daily_budget)
        except BuyError as exc:
            return JSONResponse({"error": exc.code}, 404 if exc.code == "not_found" else 409)
        return JSONResponse({"approval_id": pending.id, "content": payload,
                             "content_sha256": pending.content_sha256}, 201)
