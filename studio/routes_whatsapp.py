"""WhatsApp routes: the Cloud API webhook and the short link an approval template opens.

- ``GET /webhooks/whatsapp``: Meta's handshake (``hub.mode=subscribe``, the verify token
  ``STUDIO_WHATSAPP_VERIFY_TOKEN``, the ``hub.challenge`` echoed).
- ``POST /webhooks/whatsapp``: public and CSRF-exempt (``studio.auth``) because Meta's
  servers post it; the ``X-Hub-Signature-256`` HMAC of the raw body with
  ``META_APP_SECRET`` is its authentication. Always a quick 200 for a verified body (the
  slow work runs on ``studio.whatsapp.Inbox``'s worker), 403 otherwise.
- A voice note (or a typed message) from a linked number becomes a Brief v1 *draft* for
  that account, exactly as the web voice brief: Muse Voice Transcribe, then
  ``producer.draft_brief``. The reply is one text with the counts and a link; the owner
  answers the questions, confirms and signs in the browser. Nothing runs from WhatsApp.
- ``GET /approvals/{id}``: the approval template's button; behind the login gate, it
  redirects to the studio page following that approval's run.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from studio import brief as brief_v1
from studio import db, whatsapp
from studio.agents import producer
from studio.muse import voice
from studio.receipts import Ledger
from studio.settings import Settings

LOG = logging.getLogger("studio.routes_whatsapp")
APPROVAL_ID = re.compile(r"[0-9a-f]{32}")


def brief_link(settings: Settings, brief_id: str) -> str:
    return f"{settings.public_url}/studio?brief={brief_id}"


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             session_of: Callable[[Request], str | None]) -> None:
    cfg = settings.whatsapp
    client: whatsapp.Client | None = getattr(app.state, "whatsapp", None)

    def accepts_work() -> str:
        if settings.read_only:
            return "read_only"
        if app.state.muse is None and not settings.model_available:
            return "model_api_unavailable"
        return ""

    def make_brief(who: whatsapp.Linked, m: whatsapp.Inbound,
                   audio: bytes | None) -> whatsapp.BriefResult:
        """Audio → WAV → transcript, or the typed text; → Brief v1 draft; stored."""
        settings.require_writable()
        muse, _ = app.state.clients()
        lang = who.lang if who.lang in ("en", "pl") else "en"
        brief_id = "b_" + secrets.token_hex(8)
        if audio is not None:
            wav = voice.to_wav(audio)
            t = voice.transcribe(muse, wav, run_id=brief_id, language_bias=(lang,))
            text, source = t.text[:producer.MAX_BRIEF], "voice"
        else:
            text, source = m.text.strip(), "text"
        text = text[:brief_v1.MAX_SOURCE]
        run_id = producer.new_run_id()
        draft = producer.draft_brief(muse, text, run_id=run_id, source="transcript",
                                     lang=lang, landing_url=settings.landing_url)
        stored = json.dumps(brief_v1.as_json(draft), ensure_ascii=False)
        with db.transaction(conn):
            conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts, draft, "
                         "draft_run_id, channel, account_id) VALUES (?, '', ?, ?, ?, ?, ?, "
                         "'whatsapp', ?)",
                         (brief_id, text, source, time.time(), stored, run_id, who.account_id))
        return whatsapp.BriefResult(brief_id, len(draft.confirmed_facts),
                                    len(draft.open_questions))

    def reply(who: whatsapp.Linked, key: str, values: dict[str, Any]) -> str:
        from studio.ui import i18n

        if "brief_id" in values:
            values = {**values, "link": brief_link(settings, values.pop("brief_id"))}
        else:
            values = {**values, "link": f"{settings.public_url}/studio"}
        return str(i18n.text(i18n.norm(who.lang), key, **values))

    inbox = whatsapp.Inbox(conn, ledger, client, make_brief, reply,
                           accepts_work=accepts_work) if client is not None else None
    app.state.whatsapp_inbox = inbox

    @app.get(whatsapp.WEBHOOK_PATH)
    def whatsapp_check(request: Request) -> Response:
        challenge = whatsapp.check_challenge(request.query_params, cfg.verify_token) \
            if cfg.enabled else None
        if challenge is None:
            return JSONResponse({"error": "webhook_refused"}, 403)
        return PlainTextResponse(challenge)

    @app.post(whatsapp.WEBHOOK_PATH)
    async def whatsapp_webhook(request: Request) -> Response:
        if inbox is None:
            return JSONResponse({"error": "webhook_refused"}, 403)
        length = request.headers.get("content-length", "")
        if length and (not length.isdigit() or int(length) > whatsapp.MAX_WEBHOOK_BODY):
            return JSONResponse({"error": "webhook_refused"}, 413)
        body = await request.body()
        if len(body) > whatsapp.MAX_WEBHOOK_BODY or not whatsapp.verify_signature(
                body, request.headers.get("x-hub-signature-256"), cfg.app_secret):
            return JSONResponse({"error": "webhook_refused"}, 403)
        try:
            event = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "webhook_refused"}, 400)
        queued = await run_in_threadpool(inbox.handle, event)
        return JSONResponse({"ok": True, "queued": queued})

    @app.get("/approvals/{approval_id}")
    def approval_link(approval_id: str, request: Request) -> Response:
        """The approval template's button: the studio page following that run (the login
        gate sends a visitor through /login first)."""
        if session_of(request) is None:
            return RedirectResponse("/login?next=/studio", 303)
        row = None
        if APPROVAL_ID.fullmatch(approval_id):
            with conn.lock:
                row = conn.execute("SELECT run_id FROM approvals WHERE id = ?",
                                   (approval_id,)).fetchone()
        target = f"/studio?run={row[0]}" if row and re.fullmatch(
            r"[A-Za-z0-9_-]{1,40}", str(row[0])) else "/studio"
        return RedirectResponse(target, 303)


__all__ = ["brief_link", "register"]
