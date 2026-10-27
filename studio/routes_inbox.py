"""Inbox routes: comments → triage (draft) → human-signed reply or hide.

- ``GET /inbox`` and ``GET /api/comments`` are public and read-only; visitors without a
  session see comment text with e-mails and phone numbers redacted.
- ``POST /api/comments/simulate`` (session) adds a comment as if it arrived under the ad.
- ``POST /api/comments/{id}/triage`` (session) runs the community agent: one model call,
  the draft is stored, nothing is sent.
- ``POST /api/comments/{id}/reply`` / ``/hide`` (session) create a pending ``reply.post``
  (for the exact text in the body) or ``comment.hide``; the browser then signs it via
  ``/api/approvals/{id}`` like every other approval.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from studio import envelope
from studio.agents import community
from studio.muse.client import MuseError
from studio.receipts import Ledger, redact
from studio.settings import BudgetExceeded, ReadOnlyMode, Settings

LOG = logging.getLogger("studio.inbox")
COMMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
Guard = Callable[[Request], "JSONResponse | str"]

_FIELDS = ("id", "post_id", "author_name", "text", "created_ts", "status", "intent", "draft",
           "sentiment", "risk", "requires_human", "reason", "confidence", "flags", "source",
           "pending_id", "lead_id")


def list_comments(conn: Any, full: bool) -> list[dict[str, Any]]:
    with conn.lock:
        rows = conn.execute(
            f"SELECT {', '.join(_FIELDS)} FROM comments "  # noqa: S608 - fixed columns
            "ORDER BY created_ts DESC LIMIT 100").fetchall()
        statuses = dict(conn.execute("SELECT id, status FROM approvals").fetchall())
    out = []
    for row in rows:
        c = dict(zip(_FIELDS, row, strict=True))
        c["flags"] = json.loads(c["flags"] or "[]")
        c["requires_human"] = bool(c["requires_human"])
        for key in ("text", "draft", "reason", "author_name"):
            c[key] = envelope.clean(str(c[key] or ""))
            if not full:
                c[key] = redact(c[key])
        c["approval_status"] = statuses.get(c["pending_id"], "")
        c["actions"] = list(community.ACTIONS)
        out.append(c)
    return out


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             write_guard: Guard, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:

    @app.get("/inbox", response_class=HTMLResponse)
    def inbox_page(request: Request) -> Response:
        logged_in = session_of(request) is not None
        return templates.TemplateResponse(request, "inbox.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch, "logged_in": logged_in,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "comments": list_comments(conn, logged_in), "adapter": settings.meta_adapter,
        })

    @app.get("/api/comments")
    def comments(request: Request) -> Response:
        return JSONResponse({"comments": list_comments(conn, session_of(request) is not None)},
                            headers={"Cache-Control": "no-store"})

    async def _json(request: Request) -> dict[str, Any]:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @app.post("/api/comments/simulate")
    async def simulate(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        body = await _json(request)
        text, author = body.get("text"), body.get("author", "Simulated visitor")
        if not isinstance(text, str) or not isinstance(author, str):
            return JSONResponse({"error": "invalid_comment"}, 400)
        try:
            cid = community.simulate(conn, text, author)
        except community.InboxError as exc:
            return JSONResponse({"error": "invalid_comment", "detail": str(exc)}, 400)
        return JSONResponse({"comment_id": cid}, 201)

    @app.post("/api/comments/{comment_id}/triage")
    def triage(comment_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not COMMENT_ID.fullmatch(comment_id):
            return JSONResponse({"error": "not_found"}, 404)
        with conn.lock:
            row = conn.execute("SELECT text FROM comments WHERE id = ?",
                               (comment_id,)).fetchone()
        if row is None:
            return JSONResponse({"error": "not_found"}, 404)
        if app.state.muse is None and not settings.model_api_key:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        muse, _ = app.state.clients()
        try:
            t = community.triage(muse, row[0], run_id=community.run_id_for(comment_id))
        except (ReadOnlyMode, BudgetExceeded) as exc:
            return JSONResponse({"error": type(exc).__name__}, 503)
        except MuseError as exc:
            LOG.warning("triage %s failed: %s", comment_id, exc.code)
            return JSONResponse({"error": "model_error", "code": exc.code}, 502)
        community.save(conn, ledger, comment_id, t)
        hook = getattr(app.state, "after_triage", None)
        lead = hook(comment_id, t) if hook else None
        return JSONResponse({"comment_id": comment_id, "triage": t.triage.model_dump(),
                             "flags": t.flags, "actions": list(t.actions), "lead": lead})

    async def _request(comment_id: str, request: Request, action: str) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not COMMENT_ID.fullmatch(comment_id):
            return JSONResponse({"error": "not_found"}, 404)
        body = await _json(request)
        message = body.get("message", "")
        if not isinstance(message, str):
            return JSONResponse({"error": "invalid_reply"}, 400)
        try:
            pending, payload = community.request(conn, ledger, comment_id, action, message)
        except community.InboxError as exc:
            return JSONResponse({"error": "invalid_request", "detail": str(exc)}, 400)
        return JSONResponse({"approval_id": pending.id, "content_sha256": pending.content_sha256,
                             "content": payload}, 201)

    @app.post("/api/comments/{comment_id}/reply")
    async def reply(comment_id: str, request: Request) -> Response:
        return await _request(comment_id, request, "reply.post")

    @app.post("/api/comments/{comment_id}/hide")
    async def hide(comment_id: str, request: Request) -> Response:
        return await _request(comment_id, request, "comment.hide")
