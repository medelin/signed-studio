"""Studio routes: run a brief, follow it live, review it, sign the approval.

- ``POST /api/briefs/draft`` (session, same-origin, writable): the producer drafts a Brief
  v1 from the owner's text or edited voice transcript; gaps are open ``[FACT]`` questions.
- ``POST /api/runs`` (session, same-origin, writable) starts the producer in a background
  thread; one run at a time per process (the model budget is shared). With ``brief_v1``
  and one resolution per open question it first confirms the brief: field errors are a
  422 naming each field, otherwise the ``brief.confirmed`` receipt is written in the same
  transaction that starts the run. A plain ``brief`` string still runs as before.
- ``GET /api/runs/{id}`` and ``GET /api/runs/{id}/events`` (SSE) need a session: the run
  state and its receipts as they are written. At most ``MAX_STREAMS`` event streams are
  open per process; a stream re-reads the run's receipts only when the ledger version
  (``Ledger.seq``) changed.
- ``GET /api/approvals/{id}/payload?approver=<key id>`` (session) returns the exact bytes
  the browser signs, plus the human-readable content those bytes commit to (by hash).
- ``POST /api/approvals/{id}`` (session, same-origin, writable) verifies the signature and
  consumes the nonce with ``verify_and_consume(session_id=...)`` inside one ``BEGIN
  IMMEDIATE`` transaction, together with the approval receipt and the run's status.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
import weakref
from collections.abc import AsyncIterator, Callable
from functools import partial
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from studio import brief as brief_v1
from studio import db
from studio.agents import producer
from studio.approvals import (
    Approval,
    ApprovalError,
    Pending,
    verify_and_consume,
)
from studio.approvals import renew as renew_approval
from studio.meta.adapter import MOCK_PAGE, AdapterError
from studio.muse import voice
from studio.muse.client import MuseClient, MuseError
from studio.muse.openweights import OpenWeightsClient
from studio.muse.structured import StructuredOutputError
from studio.receipts import BRIEF_KIND, Ledger, Receipt
from studio.settings import BudgetExceeded, ReadOnlyMode, Settings

LOG = logging.getLogger("studio.runs")
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
APPROVER = re.compile(r"ed25519:[0-9a-f]{32}")
SIGNATURE = re.compile(r"[0-9a-f]{128}")
BRIEF_ID = re.compile(r"b_[0-9a-f]{16}")
ASSET_NAMES = frozenset({"feed_1x1", "feed_4x5", "story_9x16", "cutout", "video_9x16",
                         *(f"card_{i}" for i in range(1, 11))})
VOICE_TYPES = frozenset({"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/wave",
                         "audio/mp4", "audio/mpeg", "audio/aac"})
SSE_POLL_S = 0.5
SSE_MAX_S = 20 * 60
MAX_STREAMS = 8

Guard = Callable[[Request], "JSONResponse | str"]


class _Slot:
    """One of the ``MAX_STREAMS`` stream slots, released exactly once — whichever comes
    first: the stream ends, its body is closed (even unstarted), the response finishes or
    fails (client gone before the first byte), or the response is garbage-collected."""

    def __init__(self, free: Callable[[], None]) -> None:
        self._free: Callable[[], None] | None = free
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            free, self._free = self._free, None
        if free is not None:
            free()


class _SlotIterator:
    """The SSE body; closing it releases the slot even if iteration never started (an
    unstarted async generator skips its ``finally``)."""

    def __init__(self, inner: AsyncIterator[bytes], slot: _Slot) -> None:
        self._inner = inner
        self._slot = slot

    def __aiter__(self) -> _SlotIterator:
        return self

    async def __anext__(self) -> bytes:
        return await self._inner.__anext__()

    async def aclose(self) -> None:
        try:
            await self._inner.aclose()  # type: ignore[attr-defined]
        finally:
            self._slot.release()


class _SlotStreamingResponse(StreamingResponse):
    def __init__(self, content: _SlotIterator, slot: _Slot, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        weakref.finalize(self, slot.release)
        self._slot = slot

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._slot.release()


def receipt_event(r: Receipt) -> dict[str, Any]:
    """What the receipt ticker shows: ids, hashes and numbers only."""
    d = r.details
    return {
        "id": r.id, "ts": r.ts, "agent": r.agent, "kind": r.kind, "provider": r.provider,
        "model": r.model, "tier": r.tier, "tier_reason": r.tier_reason,
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
        "cached_tokens": r.cached_tokens, "cost_usd": r.cost_usd, "hash": r.hash,
        "tool": d.get("tool", ""), "status": d.get("status", ""),
        "tool_calls": d.get("tool_calls", []), "verdict": d.get("verdict", ""),
        "foreground_pixels": d.get("foreground_pixels"), "envelope": r.envelope,
    }


def _state(conn: Any, run_id: str) -> tuple[str, dict[str, Any], float] | None:
    with conn.lock:
        row = conn.execute("SELECT status, state, updated_ts FROM run_state WHERE run_id = ?",
                           (run_id,)).fetchone()
    if row is None:
        return None
    return str(row[0]), json.loads(row[1]), float(row[2])


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             write_guard: Guard) -> None:
    app.state.muse = None  # created on first run (tests may inject a client)
    app.state.open_weights = None
    app.state.run_lock = threading.Lock()
    app.state.active_run = None
    app.state.streams = 0
    stream_lock = threading.Lock()

    def logged_in(request: Request) -> bool:
        return app.state.sessions.check(request.cookies.get("studio_session")) is not None

    def clients() -> tuple[MuseClient, OpenWeightsClient]:
        if settings.replay and app.state.muse is None:
            app.state.muse, app.state.open_weights = replay_clients(settings, ledger)
        if app.state.muse is None:
            app.state.muse = MuseClient.from_settings(settings, ledger)
        if app.state.open_weights is None:
            app.state.open_weights = OpenWeightsClient.from_settings(settings, ledger)
        return app.state.muse, app.state.open_weights

    app.state.clients = clients
    # action -> executor(verified, payload) -> result dict; runs after the approval commits
    app.state.executors = {}

    def account_currency() -> str:
        """The ad account's currency (the adapter reads it once); PLN before the adapter
        exists (never: create_app makes it right after the routes)."""
        adapter = getattr(app.state, "adapter", None)
        return adapter.account_info()["currency"] if adapter is not None else \
            settings.account_currency

    def worker(run_id: str, brief: str, brief_source: str = "operator",
               confirmed: brief_v1.BriefV1 | None = None, digest: str = "") -> None:
        try:
            muse, open_weights = clients()
            ctx = producer.Context(
                client=muse, open_client=open_weights, conn=conn,
                photo_png=settings.product_photo.read_bytes(),
                concept=settings.product_concept, data_dir=settings.data_dir,
                landing_url=settings.landing_url, brief_source=brief_source,
                max_daily_budget=settings.max_daily_budget, currency=account_currency(),
                brief_v1=confirmed, brief_sha256=digest,
                page_id=getattr(getattr(app.state, "adapter", None), "page_id", "")
                or MOCK_PAGE,
                search=getattr(getattr(app.state, "adapter", None), "search_targeting", None))
            producer.run(ctx, brief, run_id=run_id)
        except Exception as exc:  # noqa: BLE001 - recorded in run_state; never crash the app
            LOG.warning("run %s failed: %s", run_id, type(exc).__name__)
            producer.mark_failed(conn, run_id, type(exc).__name__)
        finally:
            with app.state.run_lock:
                app.state.active_run = None

    def fields_error(fields: dict[str, str], status: int = 422) -> JSONResponse:
        return JSONResponse({"error": "invalid_brief", "fields": fields}, status)

    def confirm_and_start(body: dict[str, Any]) -> Response:
        """Confirm a drafted Brief v1 (every open question answered or struck, every field
        valid) and start its run; the ``brief.confirmed`` receipt, the brief row and the
        run state commit together."""
        brief_id = body.get("brief_id")
        if not isinstance(brief_id, str) or not BRIEF_ID.fullmatch(brief_id):
            return JSONResponse({"error": "invalid_brief_id"}, 400)
        with conn.lock:
            row = conn.execute("SELECT source, run_id, draft_run_id FROM briefs WHERE id = ?",
                               (brief_id,)).fetchone()
        if row is None or not row[2] or row[1]:
            return JSONResponse({"error": "unknown_draft"}, 409)
        try:
            draft = brief_v1.BriefV1.model_validate(body["brief_v1"])
        except ValidationError as exc:
            return fields_error({".".join(str(p) for p in e["loc"]): "invalid"
                                 for e in exc.errors()})
        raw = body.get("resolutions", [])
        try:
            if not isinstance(raw, list):
                raise TypeError
            resolutions = [brief_v1.Resolution.model_validate(r) for r in raw]
        except (ValidationError, TypeError):
            return fields_error({"open_questions": "invalid"})
        missing = {f"open_questions.{i}": "answer_required"
                   for i, r in enumerate(resolutions)
                   if r.action == "answer" and not r.text.strip()}
        missing |= {f"open_questions.{i}": "too_long" for i, r in enumerate(resolutions)
                    if r.action == "answer" and len(r.text) > brief_v1.MAX_FACT}
        if len(resolutions) != len(draft.open_questions):
            missing |= {f"open_questions.{i}": "unresolved"
                        for i in range(len(resolutions), len(draft.open_questions))}
        if missing:
            return fields_error(missing)
        confirmed, answered, struck = brief_v1.resolve(draft, resolutions)
        try:
            currency = account_currency()
        except AdapterError as exc:
            return JSONResponse({"error": "meta_error", "code": exc.code}, 502)
        problems = brief_v1.problems(confirmed, ceiling_daily=settings.max_daily_budget,
                                     currency=currency)
        if problems:
            return fields_error(problems)
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        digest = brief_v1.brief_sha256(confirmed)
        voice_used = row[0] == "voice" or body.get("dictated") is True
        run_id = str(row[2])
        with app.state.run_lock:
            if app.state.active_run is not None:
                return JSONResponse({"error": "run_in_progress",
                                     "run_id": app.state.active_run}, 409)
            app.state.active_run = run_id
        try:
            with db.transaction(conn):
                cur = conn.execute(
                    "UPDATE briefs SET run_id = ?, brief_v1 = ?, brief_sha256 = ? "
                    "WHERE id = ? AND run_id = ''",
                    (run_id, json.dumps(brief_v1.as_json(confirmed), ensure_ascii=False),
                     digest, brief_id))
                if cur.rowcount != 1:
                    raise _Taken
                conn.execute("INSERT INTO run_state (run_id, status, state, updated_ts) "
                             "VALUES (?, 'running', '{}', ?)", (run_id, time.time()))
                ledger.append(run_id, agent="owner", kind=BRIEF_KIND, conn=conn,
                              output_sha256=digest, details={
                                  "schema": brief_v1.SCHEMA, "brief_sha256": digest,
                                  **brief_v1.counts(confirmed, answered, struck),
                                  "voice": voice.MODEL if voice_used else ""})
        except (_Taken, sqlite3.IntegrityError):
            with app.state.run_lock:
                app.state.active_run = None
            return JSONResponse({"error": "unknown_draft"}, 409)
        source = "transcript" if voice_used else "operator"
        thread = threading.Thread(target=worker, daemon=True, name=f"producer-{run_id}",
                                  args=(run_id, brief_v1.render(confirmed), source,
                                        confirmed, digest))
        app.state.last_thread = thread
        thread.start()
        return JSONResponse({"run_id": run_id, "brief_sha256": digest,
                             **brief_v1.counts(confirmed, answered, struck)}, 202)

    @app.post("/api/briefs/draft")
    async def draft_brief(request: Request) -> Response:
        """Owner's text (or the edited transcript of a voice brief) → Brief v1 draft."""
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body = json.loads(await request.body())
            text = body["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "invalid_brief"}, 400)
        text = text.strip()
        if len(text) > brief_v1.MAX_SOURCE:
            return JSONResponse({"error": "brief_too_long"}, 413)
        lang = body.get("lang") if body.get("lang") in ("en", "pl") else request.state.lang
        brief_id = body.get("brief_id")
        source = "transcript" if body.get("dictated") is True else "operator"
        if brief_id is not None:
            if not isinstance(brief_id, str) or not BRIEF_ID.fullmatch(brief_id):
                return JSONResponse({"error": "invalid_brief_id"}, 400)
            with conn.lock:
                row = conn.execute("SELECT source, run_id FROM briefs WHERE id = ?",
                                   (brief_id,)).fetchone()
            if row is None or row[0] != "voice" or row[1]:
                return JSONResponse({"error": "unknown_voice_brief"}, 409)
            source = "transcript"
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        run_id = producer.new_run_id()
        try:
            muse, _ = clients()
            draft = await run_in_threadpool(partial(
                producer.draft_brief, muse, text, run_id=run_id, source=source, lang=lang,
                landing_url=settings.landing_url))
        except (ReadOnlyMode, BudgetExceeded) as exc:
            return JSONResponse({"error": type(exc).__name__}, 503)
        except StructuredOutputError as exc:
            return JSONResponse({"error": "model_error", "code": exc.code}, 502)
        except MuseError as exc:
            return JSONResponse({"error": "model_error", "code": exc.code}, 502)
        stored = json.dumps(brief_v1.as_json(draft), ensure_ascii=False)
        with db.transaction(conn):
            if brief_id is not None:
                conn.execute("UPDATE briefs SET text = ?, draft = ?, draft_run_id = ? "
                             "WHERE id = ? AND source = 'voice' AND run_id = ''",
                             (text, stored, run_id, brief_id))
            else:
                brief_id = "b_" + secrets.token_hex(8)
                conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts, "
                             "draft, draft_run_id) VALUES (?, '', ?, 'text', ?, ?, ?)",
                             (brief_id, text, time.time(), stored, run_id))
        return JSONResponse({
            "brief_id": brief_id, "draft_run_id": run_id, "source": source,
            "brief": draft.model_dump(mode="json"),
            "problems": brief_v1.problems(draft, confirmed=False,
                                          ceiling_daily=settings.max_daily_budget,
                                          currency=account_currency()),
            "ceiling": settings.max_daily_budget, "currency": account_currency(),
            "cost_usd": round(sum(r.cost_usd for r in ledger.receipts(run_id)), 6)})

    @app.post("/api/runs")
    async def start_run(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        body: Any = None
        try:
            body = json.loads(await request.body())
            brief = body["brief"]
            if not isinstance(brief, str) or not brief.strip():
                raise ValueError
        except (ValueError, KeyError, TypeError):
            if isinstance(body, dict) and "brief_v1" in body:
                return await run_in_threadpool(confirm_and_start, body)
            return JSONResponse({"error": "invalid_brief"}, 400)
        brief = brief.strip()
        if len(brief) > producer.MAX_BRIEF:
            return JSONResponse({"error": "brief_too_long"}, 413)
        # A brief that started as a voice transcript stays a transcript after edits: it
        # names its unused voice brief row and runs enveloped, on Standard.
        brief_id = body.get("brief_id") if isinstance(body, dict) else None
        if brief_id is not None:
            if not isinstance(brief_id, str) or not BRIEF_ID.fullmatch(brief_id):
                return JSONResponse({"error": "invalid_brief_id"}, 400)
            with conn.lock:
                row = conn.execute("SELECT source, run_id FROM briefs WHERE id = ?",
                                   (brief_id,)).fetchone()
            if row is None or row[0] != "voice" or row[1]:
                return JSONResponse({"error": "unknown_voice_brief"}, 409)
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        with app.state.run_lock:
            if app.state.active_run is not None:
                return JSONResponse({"error": "run_in_progress",
                                     "run_id": app.state.active_run}, 409)
            run_id = producer.new_run_id()
            app.state.active_run = run_id
        with db.transaction(conn):
            if brief_id is not None:
                conn.execute("UPDATE briefs SET run_id = ?, text = ? WHERE id = ? AND "
                             "source = 'voice' AND run_id = ''", (run_id, brief, brief_id))
            else:
                conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts) "
                             "VALUES (?, ?, ?, 'text', ?)",
                             ("b_" + run_id[4:], run_id, brief, time.time()))
            conn.execute("INSERT INTO run_state (run_id, status, state, updated_ts) "
                         "VALUES (?, 'running', '{}', ?)", (run_id, time.time()))
        source = "transcript" if brief_id is not None else "operator"
        thread = threading.Thread(target=worker, args=(run_id, brief, source), daemon=True,
                                  name=f"producer-{run_id}")
        app.state.last_thread = thread
        thread.start()
        return JSONResponse({"run_id": run_id}, 202)

    @app.post("/api/briefs/voice")
    async def voice_brief(request: Request) -> Response:
        """Raw audio body (what ``MediaRecorder`` produced) → WAV → transcript → brief row.

        ``?lang=en|pl`` sets ``languageBias`` (default: the page language). With
        ``?purpose=field`` (answering a ``[FACT]`` question, dictating into a field) no
        brief row is made: only the transcript comes back."""
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in VOICE_TYPES:
            return JSONResponse({"error": "unsupported_audio_type"}, 415)
        if app.state.muse is None and not settings.model_available:
            return JSONResponse({"error": "model_api_unavailable"}, 503)
        data = await request.body()
        lang = request.query_params.get("lang", "")
        lang = lang if lang in ("en", "pl") else request.state.lang
        field = request.query_params.get("purpose") == "field"
        brief_id = ("v_" if field else "b_") + secrets.token_hex(8)
        try:
            wav = await run_in_threadpool(voice.to_wav, data)
            muse, _ = clients()
            t = await run_in_threadpool(partial(voice.transcribe, muse, wav, run_id=brief_id,
                                                language_bias=(lang,)))
        except voice.VoiceError as exc:
            return JSONResponse({"error": exc.code}, exc.status)
        except (ReadOnlyMode, BudgetExceeded) as exc:
            return JSONResponse({"error": type(exc).__name__}, 503)
        except MuseError as exc:
            return JSONResponse({"error": "model_error", "code": exc.code}, 502)
        if settings.keep_audio:
            audio_dir = settings.data_dir / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            (audio_dir / f"{brief_id}.wav").write_bytes(wav)
        text = t.text[:brief_v1.MAX_SOURCE if field else producer.MAX_BRIEF]
        if field:
            return JSONResponse({**t.as_dict(), "transcript": text, "language": lang,
                                 "cost_usd": t.result.cost_usd if t.result else 0.0})
        with db.transaction(conn):
            conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts) "
                         "VALUES (?, '', ?, 'voice', ?)", (brief_id, text, time.time()))
        return JSONResponse({"brief_id": brief_id, **t.as_dict(), "transcript": text,
                             "language": lang,
                             "receipt_id": t.result.receipt_id if t.result else "",
                             "audio_kept": settings.keep_audio,
                             "cost_usd": t.result.cost_usd if t.result else 0.0})

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> Response:
        if not logged_in(request):
            return JSONResponse({"error": "login_required"}, 401)
        if not RUN_ID.fullmatch(run_id):
            return JSONResponse({"error": "not_found"}, 404)
        found = _state(conn, run_id)
        if found is None:
            return JSONResponse({"error": "not_found"}, 404)
        status, state, updated = found
        return JSONResponse({"run_id": run_id, "status": status, "state": state,
                             "updated_ts": updated,
                             "receipts": [receipt_event(r) for r in ledger.receipts(run_id)]})

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> Response:
        if not logged_in(request):
            return JSONResponse({"error": "login_required"}, 401)
        if not RUN_ID.fullmatch(run_id) or _state(conn, run_id) is None:
            return JSONResponse({"error": "not_found"}, 404)
        with stream_lock:
            if app.state.streams >= MAX_STREAMS:
                return JSONResponse({"error": "too_many_streams"}, 429)
            app.state.streams += 1

        def free() -> None:
            with stream_lock:
                app.state.streams -= 1

        slot = _Slot(free)

        async def stream() -> AsyncIterator[bytes]:
            sent = 0
            seen_seq = -1
            last_update = -1.0
            started = time.monotonic()
            try:
                while time.monotonic() - started < SSE_MAX_S:
                    seq = ledger.seq()
                    if seq != seen_seq:  # read receipts only when the ledger changed
                        seen_seq = seq
                        receipts = ledger.receipts(run_id)
                        for r in receipts[sent:]:
                            yield _sse("receipt", receipt_event(r))
                        sent = len(receipts)
                    found = _state(conn, run_id)
                    if found is None:
                        break
                    status, state, updated = found
                    if updated != last_update:
                        last_update = updated
                        yield _sse("state", {"status": status, "state": state})
                    if status != "running":
                        yield _sse("end", {"status": status})
                        return
                    await asyncio.sleep(SSE_POLL_S)
                yield _sse("end", {"status": "timeout"})
            finally:
                slot.release()

        return _SlotStreamingResponse(_SlotIterator(stream(), slot), slot,
                                      media_type="text/event-stream",
                                      headers={"Cache-Control": "no-store",
                                               "X-Accel-Buffering": "no"})

    @app.get("/assets/{run_id}/{name}.png")
    def asset(run_id: str, name: str) -> Response:
        if not RUN_ID.fullmatch(run_id) or name not in ASSET_NAMES:
            return JSONResponse({"error": "not_found"}, 404)
        path = settings.data_dir / "assets" / run_id / f"{name}.png"
        if not path.is_file():
            return JSONResponse({"error": "not_found"}, 404)
        return FileResponse(path, media_type="image/png",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/assets/{run_id}/video_9x16.mp4")
    def video_asset(run_id: str) -> Response:
        path = settings.data_dir / "assets" / run_id / "video_9x16.mp4"
        if not RUN_ID.fullmatch(run_id) or not path.is_file():
            return JSONResponse({"error": "not_found"}, 404)
        return FileResponse(path, media_type="video/mp4",
                            headers={"Cache-Control": "private, max-age=3600"})

    def _pending_and_payload(approval_id: str) -> tuple[Pending, dict[str, Any]]:
        with conn.lock:
            pending = Pending.load(conn, approval_id)
            row = conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                               (approval_id,)).fetchone()
        if row is None:
            raise _NoPayload
        return pending, json.loads(row[0])

    @app.get("/api/approvals/{approval_id}/payload")
    def approval_payload(approval_id: str, request: Request) -> Response:
        if app.state.sessions.check(request.cookies.get("studio_session")) is None:
            return JSONResponse({"error": "login_required"}, 401)
        approver = request.query_params.get("approver", "")
        if not APPROVER.fullmatch(approver):
            return JSONResponse({"error": "invalid_approver"}, 400)
        try:
            pending, payload = _pending_and_payload(approval_id)
        except (ApprovalError, _NoPayload):
            return JSONResponse({"error": "not_found"}, 404)
        with conn.lock:
            status = conn.execute("SELECT status FROM approvals WHERE id = ?",
                                  (approval_id,)).fetchone()[0]
        message = pending.message(approver)
        return JSONResponse({
            "approval_id": pending.id, "status": status, "fields": pending.fields(approver),
            "message_b64": base64.b64encode(message).decode(),
            "content": payload, "content_sha256": pending.content_sha256,
            "chain_head": pending.chain_head, "expires_ts": pending.ts + 900,
        }, headers={"Cache-Control": "no-store"})

    @app.post("/api/approvals/{approval_id}/renew")
    async def renew(approval_id: str, request: Request) -> Response:
        """No time limit for the human: an approval whose signing window has closed
        unsigned is re-issued for the same content with a fresh nonce and time
        (``approvals.renew``: the old one is superseded, and a renewal is refused while the
        item has an approved or another open approval)."""
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            _, payload = _pending_and_payload(approval_id)
            fresh, renewed = renew_approval(conn, approval_id, payload, ledger=ledger)
        except _NoPayload:
            return JSONResponse({"error": "not_found"}, 404)
        except ApprovalError as exc:
            return JSONResponse({"error": exc.code}, exc.status)
        if not renewed:
            return JSONResponse({"approval_id": fresh.id, "renewed": False})
        with db.transaction(conn):
            row = conn.execute("SELECT state FROM run_state WHERE run_id = ?",
                               (fresh.run_id,)).fetchone()
            if row is not None:
                state = json.loads(row[0])
                if state.get("pending_id") == approval_id:
                    state["pending_id"] = fresh.id
                    conn.execute("UPDATE run_state SET state = ? WHERE run_id = ?",
                                 (json.dumps(state, ensure_ascii=False), fresh.run_id))
        return JSONResponse({"approval_id": fresh.id, "renewed": True})

    @app.post("/api/approvals/{approval_id}")
    async def approve(approval_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body = json.loads(await request.body())
            approver, signature = body["approver"], body["signature"]
            if not (isinstance(approver, str) and APPROVER.fullmatch(approver)
                    and isinstance(signature, str) and SIGNATURE.fullmatch(signature)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "invalid_signature_format"}, 400)
        try:
            with db.transaction(conn):
                pending = Pending.load(conn, approval_id)
                row = conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                                   (approval_id,)).fetchone()
                if row is None:
                    raise _NoPayload
                approval = Approval(
                    pending_id=pending.id, action=pending.action, item_id=pending.item_id,
                    content_sha256=pending.content_sha256, chain_head=pending.chain_head,
                    approver=approver, nonce=pending.nonce, ts=pending.ts,
                    signature=signature, brief_sha256=pending.brief_sha256,
                    spec_sha256=pending.spec_sha256)
                verified = verify_and_consume(conn, approval, json.loads(row[0]),
                                              ledger=ledger, session_id=guard)
                conn.execute("UPDATE run_state SET status = 'approved', updated_ts = ? "
                             "WHERE run_id = ?", (time.time(), verified.run_id))
        except _NoPayload:
            return JSONResponse({"error": "not_found"}, 404)
        except ApprovalError as exc:
            return JSONResponse({"error": exc.code}, exc.status)
        receipt = next(r for r in reversed(ledger.receipts(verified.run_id))
                       if r.kind == "approval" and r.details.get("pending_id") == approval_id)
        out: dict[str, Any] = {"ok": True, "approval_id": verified.pending_id,
                               "action": verified.action,
                               "content_sha256": verified.content_sha256,
                               "signature": verified.signature, "approver": verified.approver,
                               "receipt_id": receipt.id, "receipt_hash": receipt.hash,
                               "executed": False}
        executor = app.state.executors.get(verified.action)
        if executor is not None:
            # Only after the approval (nonce + receipt) committed; outside the write lock.
            try:
                result = await run_in_threadpool(executor, verified, json.loads(row[0]))
                out.update(executed=True, result=result)
            except Exception as exc:  # noqa: BLE001 - reported, the approval stays consumed
                LOG.warning("executor %s failed: %s", verified.action, type(exc).__name__)
                out.update(executed=False, execution_error=getattr(exc, "code", "")
                           or type(exc).__name__)
        return JSONResponse(out)


def replay_clients(settings: Settings, ledger: Ledger, transport: Any = None,
                   root: Any = None) -> tuple[MuseClient, OpenWeightsClient]:
    """Clients that answer from a recorded set (``STUDIO_REPLAY=1``): no network, no key,
    no budget (nothing is spent); receipts name the provider ``<provider>-replay``. The
    open-weights reviewer is available only if the set recorded it."""
    from studio import replay

    root = root or replay.REPLAY_DIR
    tr = transport or replay.ReplayTransport.from_set(settings.replay_set, root)
    muse = MuseClient("replay", settings.model_api_base_url, ledger=ledger, budget=None,
                      read_only=settings.read_only, transport=tr)
    muse.provider = f"{MuseClient.provider}-replay"
    has_open = any("baseten" in str(i.get("route", ""))
                   for i in replay.load_set(settings.replay_set, root).get("interactions", []))
    ow = OpenWeightsClient("replay" if has_open else "", ledger=ledger,
                           read_only=settings.read_only, transport=tr)
    ow.provider = f"{OpenWeightsClient.provider}-replay"
    return muse, ow


class _NoPayload(Exception):
    pass


class _Taken(Exception):
    """The draft was confirmed by another request first."""


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
