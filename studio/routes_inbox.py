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
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from studio import disapprovals, envelope
from studio.agents import community
from studio.meta.adapter import AdapterError
from studio.muse.client import MuseError
from studio.receipts import Ledger, redact
from studio.settings import BudgetExceeded, ReadOnlyMode, Settings

LOG = logging.getLogger("studio.inbox")
COMMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
POST_ID = re.compile(r"[0-9_]{1,80}")
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
        c["approval_status"] = statuses.get(c["pending_id"], "")
        c["actions"] = list(community.ACTIONS)
        if not full:
            c["author_name"] = initials(c["author_name"])
        out.append(c)
    return out if full else public_view(out)


def initials(name: str) -> str:
    """A visitor sees initials, never the Facebook name: the first letter of the first
    two words that start with a letter (``"Joanna Kowalska" → "J. K."``), else
    ``"Facebook user"``."""
    letters = [w[0].upper() for w in name.split() if w[:1].isalpha()][:2]
    return " ".join(f"{c}." for c in letters) if letters else "Facebook user"


def public_view(value: Any) -> Any:
    """What a visitor without a session sees: every string, at any depth, redacted
    (e-mail → ``[email]``, phone → ``[phone]``). Not a field list: new fields, model text
    and nested lists are covered by default; numbers and booleans pass unchanged."""
    return redact(value)


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
            # ads Meta disapproved (A7): Meta's reason explained, a compliant variant to sign
            "disapprovals": disapprovals.listing(
                conn, getattr(request.state, "lang", "en") or "en"),
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
            return JSONResponse({"error": "invalid_comment", "detail": str(exc),
                                 "detail_code": exc.code, "detail_params": exc.params}, 400)
        return JSONResponse({"comment_id": cid}, 201)

    @app.post("/api/comments/poll")
    async def poll(request: Request) -> Response:
        """Fetch new comments under one Page/ad post through the Meta adapter."""
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        body = await _json(request)
        object_id = body.get("object_id")
        if not isinstance(object_id, str) or not POST_ID.fullmatch(object_id):
            return JSONResponse({"error": "invalid_object_id"}, 400)
        try:
            new = await run_in_threadpool(community.poll, conn, app.state.adapter, object_id)
        except AdapterError as exc:
            return JSONResponse({"error": exc.code}, 502)
        return JSONResponse({"new": new, "adapter": app.state.adapter.name})

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
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        client = app.state.inbox_client()
        try:
            t = community.triage(client, row[0], run_id=community.run_id_for(comment_id),
                                 open_weights=settings.open_weights,
                                 prescreen_client=app.state.glimmer_client())
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
            return JSONResponse({"error": "invalid_request", "detail": str(exc),
                                 "detail_code": exc.code, "detail_params": exc.params}, 400)
        return JSONResponse({"approval_id": pending.id, "content_sha256": pending.content_sha256,
                             "content": payload}, 201)

    @app.post("/api/comments/{comment_id}/reply")
    async def reply(comment_id: str, request: Request) -> Response:
        return await _request(comment_id, request, "reply.post")

    @app.post("/api/comments/{comment_id}/hide")
    async def hide(comment_id: str, request: Request) -> Response:
        return await _request(comment_id, request, "comment.hide")


_LEAD_FIELDS = ("id", "name", "email", "phone", "source", "message", "city", "country",
                "company", "kind", "score", "temp", "persona", "owner", "reasons",
                "next_action", "comment_id", "created_ts")


def list_leads(conn: Any, full: bool) -> list[dict[str, Any]]:
    with conn.lock:
        rows = conn.execute(
            f"SELECT {', '.join(_LEAD_FIELDS)} FROM leads "  # noqa: S608 - fixed columns
            "ORDER BY coalesce(score, -1) DESC, created_ts DESC LIMIT 100").fetchall()
        tasks = conn.execute("SELECT lead_id, title, due_ts, done FROM tasks "
                             "ORDER BY due_ts").fetchall()
    by_lead: dict[str, list[dict[str, Any]]] = {}
    for lead_id, title, due, done in tasks:
        by_lead.setdefault(lead_id, []).append({"title": title, "due_ts": due,
                                                "done": bool(done)})
    out = []
    for row in rows:
        lead = dict(zip(_LEAD_FIELDS, row, strict=True))
        lead["reasons"] = json.loads(lead["reasons"] or "[]")
        for key in ("name", "email", "phone", "message", "company", "persona", "next_action"):
            lead[key] = envelope.clean(str(lead[key] or ""))
        lead["reasons"] = [envelope.clean(str(r)) for r in lead["reasons"]]
        lead["tasks"] = by_lead.get(lead["id"], [])
        if not full:
            lead["name"] = initials(lead["name"])
            if lead["email"]:
                lead["email"] = "[email]"
            if lead["phone"]:
                lead["phone"] = "[phone]"
        out.append(lead)
    return out if full else public_view(out)


def register_leads(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
                   write_guard: Guard, templates: Jinja2Templates,
                   session_of: Callable[[Request], str | None]) -> None:
    from studio.agents import lead_scorer

    def after_triage(comment_id: str, t: community.Triaged) -> dict[str, Any] | None:
        """A comment triaged as a lead becomes a CRM card (rules + Spark rationale)."""
        if t.triage.intent != "lead":
            return None
        lead_id = lead_scorer.lead_from_comment(conn, comment_id)
        muse = app.state.open_weights if settings.open_weights else app.state.muse
        try:
            return lead_scorer.score_lead(conn, ledger, muse, lead_id)
        except (MuseError, ReadOnlyMode, BudgetExceeded) as exc:
            LOG.warning("lead rationale failed: %s", type(exc).__name__)
            return lead_scorer.score_lead(conn, ledger, None, lead_id)

    app.state.after_triage = after_triage

    @app.get("/leads", response_class=HTMLResponse)
    def leads_page(request: Request) -> Response:
        logged_in = session_of(request) is not None
        return templates.TemplateResponse(request, "leads.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch, "logged_in": logged_in,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "leads": list_leads(conn, logged_in),
        })

    @app.get("/api/leads")
    def leads(request: Request) -> Response:
        return JSONResponse({"leads": list_leads(conn, session_of(request) is not None)},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/leads/{lead_id}/score")
    def score(lead_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not COMMENT_ID.fullmatch(lead_id) or lead_scorer.load(conn, lead_id) is None:
            return JSONResponse({"error": "not_found"}, 404)
        muse = None
        if app.state.muse is not None or settings.model_available:
            muse = app.state.inbox_client()
        try:
            card = lead_scorer.score_lead(conn, ledger, muse, lead_id)
        except (ReadOnlyMode, BudgetExceeded) as exc:
            return JSONResponse({"error": type(exc).__name__}, 503)
        except MuseError as exc:
            return JSONResponse({"error": "model_error", "code": exc.code}, 502)
        return JSONResponse(card)
