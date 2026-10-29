"""``GET /receipts``: the whole ledger as a page — filter by run and kind, full hashes as
tooltips, each receipt copyable as JSON.

With a session the JSON is the receipt exactly as exported (``/api/receipts.jsonl``), so it
verifies. A visitor without a session gets the same view with every string redacted
(``public_view``: e-mail → ``[email]``, phone → ``[phone]``) on top of the redaction the
ledger already applies when a receipt is written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from studio.receipts import KINDS, Ledger, Receipt
from studio.routes_inbox import public_view
from studio.settings import Settings

RUN = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
MAX_ROWS = 500


def receipt_view(r: Receipt, *, full: bool) -> dict[str, Any]:
    """The receipt as the page shows and copies it (redacted for visitors)."""
    data = asdict(r)
    return data if full else public_view(data)  # type: ignore[no-any-return]


def loop_marks(receipts: list[Receipt]) -> dict[str, dict[str, str]]:
    """The reviewer -> rewrite loop in a ledger: per receipt id, ``blocked`` (a review with
    verdict BLOCK; ``rules`` lists the rules), ``rewrite`` (the copywriter call that
    answered it) or ``after`` (the review that followed, ``verdict`` OK/WARN/BLOCK)."""
    marks: dict[str, dict[str, str]] = {}
    state: dict[str, str] = {}  # run -> "" | "blocked" | "rewritten"
    for r in receipts:
        mode = state.get(r.run_id, "")
        if r.kind == "review":
            verdict = str(r.details.get("verdict", ""))
            if mode == "rewritten":
                marks[r.id] = {"mark": "after", "verdict": verdict}
            elif verdict == "BLOCK":
                state[r.run_id] = "blocked"
                marks[r.id] = {"mark": "blocked", "verdict": verdict,
                               "rules": ", ".join(r.details.get("rules") or [])}
        elif mode == "blocked" and r.agent == "copywriter" and r.kind == "model_call":
            state[r.run_id] = "rewritten"
            marks[r.id] = {"mark": "rewrite"}
    return marks


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%S")


def register(app: FastAPI, settings: Settings, ledger: Ledger, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:
    templates.env.filters["utc"] = utc

    @app.get("/receipts", response_class=HTMLResponse)
    def receipts_page(request: Request, run: str = "", kind: str = "") -> Response:
        full = session_of(request) is not None
        everything = ledger.receipts()
        runs = sorted({r.run_id for r in everything})
        bad_filter = bool(run and not RUN.fullmatch(run)) or bool(kind and kind not in KINDS)
        rows = [] if bad_filter else [
            r for r in everything
            if (not run or r.run_id == run) and (not kind or r.kind == kind)]
        shown = rows[-MAX_ROWS:][::-1]  # newest first
        views = []
        for r in shown:
            v = receipt_view(r, full=full)
            views.append({"r": v, "json": json.dumps(v, sort_keys=True, indent=1,
                                                     ensure_ascii=False)})
        return templates.TemplateResponse(request, "receipts.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch, "logged_in": full,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "views": views, "loopmarks": loop_marks(everything),
            "runs": runs, "kinds": sorted(KINDS),
            "run": run if not bad_filter else "", "kind": kind if kind in KINDS else "",
            "total": len(everything), "matched": len(rows), "limit": MAX_ROWS,
            "bad_filter": bad_filter,
        })
