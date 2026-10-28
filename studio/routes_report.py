"""Report routes: the analyst reads the whole ledger (plus insights and an optional
screenshot) in one long-context call.

- ``GET /report`` (public, read-only) shows the latest report with the token counter;
  visitors without a session get every string redacted (``public_view``).
- ``POST /api/reports`` (session, same-origin, writable) runs the analyst. Body: empty or
  JSON for no screenshot, or a raw ``image/png`` / ``image/jpeg`` Ads Manager screenshot
  (≤ 5 MB), re-encoded without metadata before use (400 ``invalid_screenshot`` if it is
  not a readable image; 413 ``context_too_large`` if the insights alone overflow the
  prompt). One report at a time; the call is capped at ``MAX_COST_USD`` and the
  remaining daily budget.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from studio.agents import analyst
from studio.meta import measurement
from studio.muse.client import MuseError
from studio.muse.vision import strip_metadata
from studio.receipts import Ledger
from studio.routes_inbox import public_view
from studio.settings import BudgetExceeded, ReadOnlyMode, Settings

INSIGHTS_FIXTURE = (Path(__file__).resolve().parent.parent / "fixtures" / "meta"
                    / "insights.SYNTHETIC.json")
MAX_SCREENSHOT = 5 * 1024 * 1024
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
    def report_page(request: Request) -> Response:
        return templates.TemplateResponse(request, "report.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch,
            "logged_in": session_of(request) is not None,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "rep": view(request), "measurement": measurement.latest(conn),
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
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        body = await request.body()
        shot: bytes | None = None
        if ctype in ("image/png", "image/jpeg"):
            if len(body) > MAX_SCREENSHOT:
                return JSONResponse({"error": "screenshot_too_large"}, 413)
            try:
                shot = await run_in_threadpool(strip_metadata, body)
            except ValueError as exc:
                return JSONResponse({"error": "invalid_screenshot",
                                     "detail": str(exc)[:200]}, 400)
        elif body.strip() and ctype != "application/json":
            return JSONResponse({"error": "unsupported_media_type"}, 415)
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        if not lock.acquire(blocking=False):
            return JSONResponse({"error": "report_in_progress"}, 409)
        try:
            muse, _ = app.state.clients()
            run_id = "an_" + secrets.token_hex(8)
            cap = analyst.MAX_COST_USD
            if muse.budget is not None:
                cap = min(cap, muse.budget.remaining())
            out = await run_in_threadpool(partial(
                analyst.report, muse, ledger.export_jsonl(), insights(), shot, run_id=run_id,
                summary={**analyst.inbox_summary(conn),
                         "measurement": measurement.summary(conn)}, max_cost_usd=cap))
            report_id = analyst.save(conn, ledger, run_id, out)
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
