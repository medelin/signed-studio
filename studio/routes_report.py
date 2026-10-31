"""Report routes: the analyst reads the whole ledger plus Meta's insights in one
long-context call. No screenshots: the numbers come from the platform's API.

- ``GET /report`` (public, read-only) shows the latest report with the token counter;
  visitors without a session get every string redacted (``public_view``).
- ``POST /api/reports`` (session, same-origin, writable) runs the analyst. Body: empty or
  JSON; an image is refused (415 ``screenshots_not_used``); 413 ``context_too_large`` if
  the insights alone overflow the prompt. The page pulls fresh insights first
  (``/api/insights/pull``). One report at a time; the call is capped at ``MAX_COST_USD``
  and the remaining daily budget.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from studio.agents import analyst
from studio.google import store as google_store
from studio.meta import measurement
from studio.muse.client import MuseError
from studio.receipts import Ledger
from studio.routes_inbox import public_view
from studio.settings import BudgetExceeded, ReadOnlyMode, Settings

INSIGHTS_FIXTURE = (Path(__file__).resolve().parent.parent / "fixtures" / "meta"
                    / "insights.SYNTHETIC.json")
Guard = Callable[[Request], "JSONResponse | str"]


def latest(conn: Any) -> dict[str, Any] | None:
    with conn.lock:
        row = conn.execute("SELECT id, run_id, body, created_ts FROM reports "
                           "ORDER BY created_ts DESC LIMIT 1").fetchone()
    if row is None:
        return None
    return {"id": row[0], "run_id": row[1], "created_ts": row[3], **json.loads(row[2])}


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             write_guard: Guard, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:
    lock = threading.Lock()

    def insights() -> dict[str, Any]:
        source = getattr(app.state, "insights", None)
        if callable(source):
            return source()  # type: ignore[no-any-return]
        return json.loads(INSIGHTS_FIXTURE.read_text())  # type: ignore[no-any-return]

    def view(request: Request) -> dict[str, Any] | None:
        rep = latest(conn)
        return rep if rep is None or session_of(request) is not None else public_view(rep)

    @app.get("/report", response_class=HTMLResponse)
    def report_page(request: Request, month: str = "") -> Response:
        from studio import exports, routes_export

        logged_in = session_of(request) is not None
        if not exports.MONTH.fullmatch(month):
            month = exports.current_month()
        return templates.TemplateResponse(request, "report.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch,
            "logged_in": logged_in,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "rep": view(request), "measurement": measurement.latest(conn),
            "google": google_store.latest(conn),
            # the monthly statement (signed-in only): preview + downloads
            "statement": routes_export.statement_view(app, conn, request, month)
            if logged_in else None,
        })

    @app.get("/api/reports/latest")
    def report_latest(request: Request) -> Response:
        rep = view(request)
        if rep is None:
            return JSONResponse({"error": "not_found"}, 404)
        return JSONResponse(rep, headers={"Cache-Control": "no-store"})

    @app.post("/api/reports")
    async def make_report(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        # The report reads Meta's own numbers (the insights API), never screenshots: an
        # image is refused, a JSON body (or none) is the only request.
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        body = await request.body()
        if ctype.startswith("image/"):
            return JSONResponse({"error": "screenshots_not_used"}, 415)
        if body.strip() and ctype != "application/json":
            return JSONResponse({"error": "unsupported_media_type"}, 415)
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        if not lock.acquire(blocking=False):
            return JSONResponse({"error": "report_in_progress"}, 409)
        run_id = "an_" + secrets.token_hex(8)

        def build() -> tuple[str, analyst.AnalystResult]:
            """The whole report in one worker thread: the ledger export, insights and
            summaries are read there too, never on the event loop."""
            muse, _ = app.state.clients()
            cap = analyst.MAX_COST_USD
            if muse.budget is not None:
                cap = min(cap, muse.budget.remaining())
            made = analyst.report(
                muse, ledger.export_jsonl(), insights(), None, run_id=run_id,
                summary={**analyst.inbox_summary(conn),
                         "measurement": measurement.summary(conn)}, max_cost_usd=cap)
            return analyst.save(conn, ledger, run_id, made), made

        try:
            report_id, out = await run_in_threadpool(build)
        except analyst.ContextTooLarge:
            return JSONResponse({"error": "context_too_large"}, 413)
        except (ReadOnlyMode, BudgetExceeded) as exc:
            return JSONResponse({"error": type(exc).__name__}, 503)
        except MuseError as exc:
            return JSONResponse({"error": exc.code or "model_error"},
                                402 if exc.code == "analyst_budget" else 502)
        finally:
            lock.release()
        return JSONResponse({"report_id": report_id, "run_id": run_id, **out.as_dict()})
