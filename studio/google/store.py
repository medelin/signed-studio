"""Google Search campaigns and insights snapshots (own tables: the Meta ``campaigns`` row
has Meta's three object ids and a ``mock|graph`` adapter check)."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

from studio.approvals import transaction
from studio.receipts import Ledger, canonical, sha256_hex

SCHEMA = """
CREATE TABLE IF NOT EXISTS google_campaigns (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PAUSED' CHECK (status IN ('PAUSED', 'ENABLED')),
    daily_budget_micros INTEGER NOT NULL CHECK (daily_budget_micros > 0),
    currency TEXT NOT NULL,
    adapter TEXT NOT NULL CHECK (adapter IN ('mock', 'rest')),
    customer_id TEXT NOT NULL,
    resources TEXT NOT NULL DEFAULT '{}',
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS google_insights (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_ts REAL NOT NULL
);
"""
AGENT = "analyst"
KIND = "insights.snapshot"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def campaigns(conn: sqlite3.Connection, adapter: str | None = None) -> list[dict[str, Any]]:
    sql = ("SELECT id, run_id, name, status, daily_budget_micros, currency, adapter, "
           "customer_id, resources, created_ts FROM google_campaigns")
    with conn.lock:  # type: ignore[attr-defined]
        rows = conn.execute(sql + (" WHERE adapter = ?" if adapter else "")
                            + " ORDER BY created_ts DESC",
                            (adapter,) if adapter else ()).fetchall()
    keys = ("id", "run_id", "name", "status", "daily_budget_micros", "currency", "adapter",
            "customer_id")
    return [{**dict(zip(keys, r[:8], strict=True)), "resources": json.loads(r[8]),
             "created_ts": r[9]} for r in rows]


def campaign(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    return next((c for c in campaigns(conn) if c["id"] == item_id), None)


def run_id_for(campaign_id: str) -> str:
    return "gs_" + sha256_hex(campaign_id)[:16]


def save_snapshot(conn: sqlite3.Connection, ledger: Ledger, campaign_id: str,
                  rows: list[Mapping[str, Any]], provenance: str) -> str:
    """Store one 7-day snapshot (rows by day) and write its ``insights.snapshot`` receipt
    (numbers only; no person-level data exists in these insights)."""
    totals = {"impressions": sum(int(r["impressions"]) for r in rows),
              "clicks": sum(int(r["clicks"]) for r in rows),
              "cost": round(sum(float(r["cost"]) for r in rows), 2),
              "conversions": round(sum(float(r["conversions"]) for r in rows), 2)}
    totals["ctr"] = round(100 * totals["clicks"] / totals["impressions"], 2) \
        if totals["impressions"] else 0.0
    snap = {"channel": "google_search", "date_preset": "last_7d", "provenance": provenance,
            "totals": totals, "days": [dict(r) for r in rows]}
    snapshot_id = "gsn_" + secrets.token_hex(6)
    with transaction(conn):
        conn.execute("INSERT INTO google_insights (id, campaign_id, body, created_ts) "
                     "VALUES (?, ?, ?, ?)", (snapshot_id, campaign_id,
                                             json.dumps(snap, ensure_ascii=False,
                                                        sort_keys=True), time.time()))
    ledger.append(run_id_for(campaign_id), agent=AGENT, kind=KIND,
                  output_sha256=sha256_hex(canonical(snap)),
                  details={"campaign": campaign_id, "snapshot": snapshot_id,
                           "channel": "google_search", "provenance": provenance,
                           **{k: totals[k] for k in ("impressions", "clicks", "cost",
                                                     "conversions")},
                           "days": len(rows)})
    return snapshot_id


def pull_all(conn: sqlite3.Connection, ledger: Ledger, adapter: Any) -> dict[str, int]:
    """A snapshot of every Search campaign this adapter created (the hourly insights pull)."""
    n = 0
    for c in campaigns(conn, adapter.name):
        rows = adapter.insights(c["resources"]["campaign"])
        save_snapshot(conn, ledger, c["id"], rows,
                      "synthetic" if adapter.name == "mock" else "live")
        n += 1
    return {"google_campaigns": n}


def latest(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The newest snapshot of every Search campaign, for ``/report``."""
    with conn.lock:  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT s.campaign_id, s.body, s.created_ts, c.name, c.currency, c.status FROM "
            "google_insights s JOIN google_campaigns c ON c.id = s.campaign_id WHERE "
            "s.created_ts = (SELECT max(created_ts) FROM google_insights t WHERE "
            "t.campaign_id = s.campaign_id) ORDER BY s.created_ts DESC LIMIT 10").fetchall()
    return [{"campaign_id": r[0], **json.loads(r[1]), "created_ts": r[2], "name": r[3],
             "currency": r[4], "status": r[5]} for r in rows]
