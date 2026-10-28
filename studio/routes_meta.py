"""Meta features beyond the campaign itself.

- Template data for the objective form (``form_globals``).
- ``POST /api/leadforms/poll`` (session, same-origin, writable): fetch new leads of every
  instant form this studio created (``leadads.poll``) into the lead store and scorer.
- ``GET /api/meta/webhook``: Meta's subscription check (``hub.verify_token`` must equal
  ``META_WEBHOOK_VERIFY_TOKEN``; the ``hub.challenge`` digits are echoed).
- ``POST /api/meta/webhook``: a Page ``leadgen`` event. Refused (403) unless its
  ``X-Hub-Signature-256`` verifies with ``META_APP_SECRET``; only forms this studio
  created, on its own Page, are fetched (``GET /{lead_id}``) and ingested. Like every
  route it sits behind the login gate, so it is reachable only where
  ``STUDIO_REQUIRE_LOGIN`` is off (the operator's machine, never the sandbox).

- ``POST /api/insights/pull`` (session, same-origin, writable): an objective-aware insights
  snapshot of every campaign (``insights.snapshot`` receipts) and fresh recommendations;
  ``studio insights-pull`` does the same from the hourly timer.
- ``POST /api/recommendations/{id}/approve`` (session, same-origin, writable): a pending
  ``campaign.update`` approval for one executable recommendation; the owner signs it like
  any other approval and only then does the executor change the budget or pause.

``register`` is called once by ``app.create_app`` after the adapter exists.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from studio import leadads
from studio.agents import media_buyer
from studio.google import store as google_store
from studio.meta import measurement, objectives
from studio.meta.adapter import AdapterError
from studio.receipts import Ledger
from studio.settings import Settings

Guard = Callable[[Request], "JSONResponse | str"]
LOG = logging.getLogger("studio.meta")
CHALLENGE = re.compile(r"\d{1,64}")
REC_ID = re.compile(r"rc_[0-9a-f]{12}")


def form_globals() -> dict[str, Any]:
    """What the Brief v1 form needs to render the objective table: goals, their
    sub-choices, and for each id field the ``goal:sub`` rows that use it (the form shows
    a field only for those rows)."""
    uses: dict[str, list[str]] = {name: [] for name in (
        "pixel_id", "application_id", "object_store_url", "event_id", "product_catalog_id",
        "product_set_id", "privacy_policy_url", "custom_event_type")}
    for (goal, sub), row in objectives.TABLE.items():
        for name in row.needs:
            uses[name].append(f"{goal}:{sub}")
        if row.event:
            uses["custom_event_type"].append(f"{goal}:{sub}")
    return {"goals": objectives.GOALS, "subs": objectives.SUBS,
            "needs": {k: " ".join(v) for k, v in uses.items()},
            "events": (*objectives.LEAD_EVENTS, *objectives.SALES_EVENTS),
            "lead_fields": objectives.LEAD_FIELDS,
            "default_subs": {g: subs[0] for g, subs in objectives.SUBS.items()}}


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             write_guard: Guard, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:
    templates.env.globals.update(form_globals())

    def client() -> Any:
        if app.state.muse is not None or settings.model_available:
            try:
                return app.state.clients()[0]
            except Exception:  # noqa: BLE001 - leads are scored by rules without a model
                return None
        return None

    app.state.executors["campaign.update"] = (
        lambda verified, payload: media_buyer.update(conn, ledger, app.state.adapter, verified,
                                                     payload, settings.max_daily_budget))

    @app.post("/api/insights/pull")
    async def pull_insights(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            out = await run_in_threadpool(measurement.pull_all, conn, ledger,
                                          app.state.adapter, settings.max_daily_budget)
        except AdapterError as exc:
            return JSONResponse({"error": "meta_error", "code": exc.code}, 502)
        google = getattr(app.state, "google", None)
        if google is not None:
            try:
                pulled = await run_in_threadpool(google_store.pull_all, conn, ledger, google)
                if pulled["google_campaigns"]:  # the Meta-only answer keeps its shape
                    out.update(pulled)
            except AdapterError as exc:
                return JSONResponse({"error": "google_error", "code": exc.code}, 502)
        return JSONResponse(out)

    @app.get("/api/measurement")
    def measurement_latest(request: Request) -> Response:
        """The newest snapshot of every campaign and its recommendations (numbers only)."""
        return JSONResponse({"campaigns": measurement.latest(conn)},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/recommendations/{rec_id}/approve")
    def approve_recommendation(rec_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not REC_ID.fullmatch(rec_id):
            return JSONResponse({"error": "not_found"}, 404)
        try:
            pending, payload = media_buyer.request_update(conn, ledger, rec_id,
                                                          settings.max_daily_budget)
        except media_buyer.BuyError as exc:
            return JSONResponse({"error": exc.code}, 404 if exc.code == "not_found" else 409)
        return JSONResponse({"approval_id": pending.id, "content": payload,
                             "content_sha256": pending.content_sha256}, 201)

    @app.post("/api/leadforms/poll")
    async def poll_leads(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            out = await run_in_threadpool(leadads.poll, conn, ledger, app.state.adapter,
                                          client())
        except AdapterError as exc:
            return JSONResponse({"error": "meta_error", "code": exc.code}, 502)
        return JSONResponse(out)

    @app.get("/api/meta/webhook")
    def webhook_check(request: Request) -> Response:
        q = request.query_params
        token = settings.meta_webhook_verify_token
        challenge = q.get("hub.challenge", "")
        if (q.get("hub.mode") != "subscribe" or not token or not CHALLENGE.fullmatch(challenge)
                or not secrets.compare_digest(q.get("hub.verify_token", ""), token)):
            return JSONResponse({"error": "webhook_refused"}, 403)
        return PlainTextResponse(challenge)

    @app.post("/api/meta/webhook")
    async def webhook(request: Request) -> Response:
        body = await request.body()
        if not leadads.verify_signature(body, request.headers.get("x-hub-signature-256"),
                                        settings.meta_app_secret):
            return JSONResponse({"error": "webhook_refused"}, 403)
        try:
            event = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "webhook_refused"}, 400)
        adapter = app.state.adapter

        def ingest() -> int:
            n = 0
            for change in leadads.leadgen_changes(event):
                if (change["page_id"] and change["page_id"] != adapter.page_id) or \
                        not leadads.known_form(conn, change["form_id"], adapter.name):
                    continue  # not a form this studio made, on its own Page
                try:
                    lead = adapter.get_lead(change["leadgen_id"])
                except AdapterError as exc:
                    LOG.warning("leadgen fetch failed: %s", exc.code)
                    continue
                n += leadads.ingest(conn, ledger, client(), lead, via="webhook") is not None
            return n

        return JSONResponse({"received": await run_in_threadpool(ingest)})
