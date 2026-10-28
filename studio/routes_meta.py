"""Meta features beyond the campaign itself: template data for the objective form.

``register`` is called once by ``app.create_app`` after the adapter exists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from studio.meta import objectives
from studio.receipts import Ledger
from studio.settings import Settings

Guard = Callable[[Request], "JSONResponse | str"]


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
