"""Media buyer (executor, not a model): turns a signed approval into PAUSED Meta objects.

- ``create``: on a consumed ``campaign.create`` approval → upload the approved hero image →
  campaign → ad set → creative → ad, all PAUSED → preview. One ``tool_call`` receipt per
  adapter call and one ``external_write`` receipt (``approval_id``, ``action``,
  ``item_id``, content hash) for the approved write, so ``studio verify`` links it to the
  signature. The receipt and the ``campaigns`` row are written before the preview; a
  failed preview is reported (``preview: null``, ``preview_error``) but is not fatal. If a
  step fails after something was created, the ``external_write`` still records what exists
  (``status: partial``) — the objects are PAUSED and visible in Ads Manager.
- ``request_activation`` / ``activate``: a second, separate approval (``campaign.activate``)
  with the daily budget and the ceiling in the signed payload; the ceiling
  (``STUDIO_MAX_DAILY_BUDGET_PLN``) is checked again at execution time.

Nothing here is reachable from a model: there is no tool for it (``skills.yaml`` marks
both actions external), and every entry point starts with ``executor.authorize``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from studio import executor
from studio.approvals import Pending, Verified, transaction
from studio.meta.adapter import PAUSED, Adapter, PausedOnly, campaign_name, check_paused
from studio.receipts import Ledger, canonical

AGENT = "media_buyer"


class BuyError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _hero(payload: dict[str, Any], data_dir: Path) -> bytes:
    """The approved hero image from disk, checked against the signed hash."""
    image = payload["images"][0]
    path = data_dir / "assets" / str(payload["run_id"]) / f"{image['placement']}.png"
    png = path.read_bytes()
    if hashlib.sha256(png).hexdigest() != image["sha256"]:
        raise BuyError("the image on disk is not the approved image", "image_changed")
    return png


def create(conn: sqlite3.Connection, ledger: Ledger, adapter: Adapter, verified: Any,
           payload: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    check_paused(payload)  # before anything: an ACTIVE creation payload is never executed
    v: Verified = executor.authorize(conn, verified, "campaign.create", payload)
    created: dict[str, str] = {}

    def step(name: str, fn: Any, *args: Any) -> str:
        try:
            out = str(fn(*args))
        except Exception as exc:
            executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step=name,
                                 status="error",
                                 details={"error": getattr(exc, "code", type(exc).__name__)})
            raise
        created[name] = out
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step=name,
                             status="ok", details={"object": executor.safe_id(out),
                                                   **({"object_status": PAUSED} if name in (
                                                       "campaign", "adset", "ad") else {})})
        return out

    try:
        png = _hero(payload, data_dir)
        image_hash = step("image", adapter.upload_image, png, payload, v)
        campaign_id = step("campaign", adapter.create_campaign_paused, payload, v)
        adset_id = step("adset", adapter.create_adset_paused, campaign_id, payload, v)
        creative_id = step("creative", adapter.create_creative, image_hash, payload, v)
        ad_id = step("ad", adapter.create_ad_paused, adset_id, creative_id, payload, v)
    except Exception as exc:
        failure = {"status": "partial" if created else "failed", "created": created,
                   "error": getattr(exc, "code", type(exc).__name__)}
        if created:
            executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, details={
                "status": "partial", "objects": {k: executor.safe_id(x)
                                                 for k, x in created.items()}})
        executor.finish(conn, v, "failed", failure)
        raise
    # Everything exists now: the write receipt and the campaign row come before the
    # preview, so a failed preview (a read) cannot leave PAUSED objects unaccounted for.
    executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, details={
        "status": "ok", "object_status": PAUSED,
        "objects": {k: executor.safe_id(x) for k, x in created.items()}})
    with transaction(conn):
        conn.execute(
            "INSERT INTO campaigns (id, run_id, name, objective, status, daily_budget_minor, "
            "adapter, external_id, created_ts) VALUES (?, ?, ?, ?, 'PAUSED', ?, ?, ?, ?)",
            (v.item_id, v.run_id, campaign_name(payload), payload["campaign"]["objective"],
             int(payload["campaign"]["daily_budget_minor"]), adapter.name, campaign_id,
             time.time()))
    result: dict[str, Any] = {"adapter": adapter.name, "status": PAUSED,
                              "campaign_item_id": v.item_id,
                              **{f"{k}_id": x for k, x in created.items()}}
    try:
        result["preview"] = asdict(adapter.preview(ad_id))
    except Exception as exc:  # not fatal: the ad exists and is PAUSED in Ads Manager
        result["preview"] = None
        result["preview_error"] = getattr(exc, "code", type(exc).__name__)
    executor.finish(conn, v, "done", result)
    return result


def campaign(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT c.id, c.run_id, c.name, c.status, c.daily_budget_minor, c.adapter, "
            "c.external_id, e.result FROM campaigns c LEFT JOIN executions e ON "
            "e.item_id = c.id AND e.action = 'campaign.create' WHERE c.id = ?",
            (item_id,)).fetchone()
    if row is None:
        return None
    keys = ("id", "run_id", "name", "status", "daily_budget_minor", "adapter", "external_id")
    out = dict(zip(keys, row[:7], strict=True))
    out["created"] = json.loads(row[7]) if row[7] else {}
    return out


def request_activation(conn: sqlite3.Connection, ledger: Ledger, item_id: str,
                       ceiling_pln: int) -> tuple[Pending, dict[str, Any]]:
    """A pending ``campaign.activate`` for a PAUSED campaign within the budget ceiling."""
    c = campaign(conn, item_id)
    if c is None:
        raise BuyError("no such campaign", "not_found")
    if c["status"] != PAUSED:
        raise BuyError("campaign is not PAUSED", "not_paused")
    ceiling = ceiling_pln * 100
    if c["daily_budget_minor"] > ceiling:
        raise BuyError(f"daily budget above the {ceiling_pln} PLN ceiling", "over_ceiling")
    payload = {"action": "campaign.activate", "campaign_id": c["id"],
               "external_id": c["external_id"], "adapter": c["adapter"],
               "daily_budget_minor": c["daily_budget_minor"], "ceiling_minor": ceiling,
               "currency": "PLN", "run_id": c["run_id"]}
    pending = Pending.create("campaign.activate", c["id"], payload, ledger=ledger,
                             run_id=c["run_id"])
    with transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (pending.id, c["run_id"], canonical(payload).decode(), time.time()))
    return pending, payload


def activate(conn: sqlite3.Connection, ledger: Ledger, adapter: Adapter, verified: Any,
             payload: dict[str, Any], ceiling_pln: int) -> dict[str, Any]:
    v: Verified = executor.authorize(conn, verified, "campaign.activate", payload)
    c = campaign(conn, v.item_id)
    problem = ""
    if c is None or c["status"] != PAUSED:
        problem = "not_paused"
    elif (c["external_id"], c["daily_budget_minor"], c["adapter"]) != (
            payload.get("external_id"), payload.get("daily_budget_minor"), adapter.name):
        problem = "campaign_changed"
    elif int(payload["daily_budget_minor"]) > min(int(payload["ceiling_minor"]),
                                                  ceiling_pln * 100):
        problem = "over_ceiling"
    if problem:
        executor.finish(conn, v, "failed", {"error": problem})
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step="activate",
                             status="rejected", details={"reason": problem})
        raise BuyError(f"activation refused: {problem}", problem)
    try:
        adapter.activate(payload, v)
    except Exception as exc:
        executor.finish(conn, v, "failed", {"error": type(exc).__name__})
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step="activate",
                             status="error",
                             details={"error": getattr(exc, "code", type(exc).__name__)})
        raise
    executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, details={
        "status": "ok", "object_status": "ACTIVE",
        "daily_budget_minor": payload["daily_budget_minor"]})
    with transaction(conn):
        conn.execute("UPDATE campaigns SET status = 'ACTIVE' WHERE id = ?", (v.item_id,))
    result = {"adapter": adapter.name, "status": "ACTIVE", "campaign_item_id": v.item_id}
    executor.finish(conn, v, "done", result)
    return result


__all__ = ["AGENT", "BuyError", "PausedOnly", "activate", "campaign", "create",
           "request_activation"]
