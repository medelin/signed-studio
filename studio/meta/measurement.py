"""Measurement: objective-aware insights snapshots and code-made recommendations.

- ``pull`` reads ``GET /{campaign_id}/insights`` for the campaign's objective (``FIELDS``:
  ``results`` and ``cost_per_result``, plus ``actions`` / ``cost_per_action_type`` to
  count the objective's result where ``results`` is empty, ``reach``, ``frequency``,
  ``cpm``, ``spend``, ``ctr``, ``inline_link_clicks``, ``attribution_setting`` with
  ``use_unified_attribution_setting=true``) over the last 7 days, and the same per
  breakdown: ``age,gender``, ``publisher_platform,platform_position`` and ``region``.
  Every field and breakdown above was accepted by the live API on 2026-10-28
  (``fixtures/cassettes/graph_readonly.json``).
- ``save`` stores the snapshot and writes an ``insights.snapshot`` receipt (numbers, the
  objective and the result it counts; no person-level data exists in insights).
- ``recommend`` turns a snapshot into at most a few recommendations by fixed rules. A
  budget change or a pause is only a *proposal*: executing it needs a new signed
  ``campaign.update`` approval and runs through the executor (``media_buyer.update``),
  never automatically. Placement findings are information: with Advantage+ placements
  Meta moves budget between placements itself.

Insights reference: https://developers.facebook.com/docs/marketing-api/insights/
(fields, ``breakdowns``, ``action_attribution_windows``, unified attribution).
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

from studio.approvals import transaction
from studio.receipts import Ledger, canonical, sha256_hex

AGENT = "analyst"
KIND = "insights.snapshot"
DATE_PRESET = "last_7d"
BASE_FIELDS = ("results", "cost_per_result", "impressions", "reach", "frequency", "spend",
               "cpm", "ctr", "inline_link_clicks", "actions", "cost_per_action_type",
               "attribution_setting")
# fields that are themselves the result for some optimisation goals
RESULT_FIELDS = frozenset({"reach", "estimated_ad_recallers"})
BREAKDOWNS = {"age_gender": "age,gender", "placement": "publisher_platform,platform_position",
              "region": "region"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS insights_snapshots (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'requested', 'executed', 'failed')),
    approval_id TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL
);
"""
# Rule thresholds (code, documented in docs/architecture.md)
COSTLY_RATIO = 1.5  # a placement whose cost per result is 1.5× the campaign's …
COSTLY_SHARE = 0.2  # … and that takes at least 20 % of the spend
SCALE_FREQUENCY = 2.0  # results come in and people have seen it under twice: room to grow
FATIGUE_FREQUENCY = 4.0  # seen four times or more on average: ad fatigue
SCALE_STEP, CUT_STEP = 1.2, 0.8
NO_RESULT_DAYS = 2  # spend of two daily budgets with no result: propose a pause


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def fields_for(spec: Mapping[str, Any]) -> list[str]:
    extra = [spec["result"]] if spec.get("result") == "estimated_ad_recallers" else []
    return [*BASE_FIELDS, *extra]


def _num(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def _action(row: Mapping[str, Any], key: str, action: str) -> float:
    for item in row.get(key) or []:
        if isinstance(item, Mapping) and item.get("action_type") == action:
            return _num(item.get("value"))
    return 0.0


def result_of(row: Mapping[str, Any], result: str) -> float:
    """The objective's result count in one insights row: ``results`` when Meta filled it,
    else the field (reach, ad recallers) or the action of that type."""
    results = row.get("results")
    if isinstance(results, list):
        total = sum(_num(v.get("value")) for r in results if isinstance(r, Mapping)
                    for v in (r.get("values") or []) if isinstance(v, Mapping))
        if total:
            return total
    if result in RESULT_FIELDS:
        return _num(row.get(result))
    return _action(row, "actions", result)


def normalize(row: Mapping[str, Any], result: str) -> dict[str, Any]:
    spend = _num(row.get("spend"))
    results = result_of(row, result)
    return {"impressions": int(_num(row.get("impressions"))), "reach": int(_num(row.get("reach"))),
            "frequency": round(_num(row.get("frequency")), 2), "spend": round(spend, 2),
            "cpm": round(_num(row.get("cpm")), 2), "ctr": round(_num(row.get("ctr")), 2),
            "inline_link_clicks": int(_num(row.get("inline_link_clicks"))),
            "results": int(results),
            "cost_per_result": round(spend / results, 2) if results else None}


_KEYS = {"age_gender": ("age", "gender"), "placement": ("publisher_platform",
                                                        "platform_position"),
         "region": ("region",)}


def pull(adapter: Any, external_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """One snapshot of a campaign: totals and each breakdown, normalised."""
    fields = fields_for(spec)
    total = adapter.insights_report(external_id, fields)
    rows = [r for r in total.get("data") or [] if isinstance(r, Mapping)]
    snap: dict[str, Any] = {
        "objective": spec["objective"], "optimization_goal": spec["optimization_goal"],
        "result": spec["result"], "date_preset": DATE_PRESET,
        "attribution_setting": str(rows[0].get("attribution_setting", "")) if rows else "",
        "totals": normalize(rows[0], spec["result"]) if rows else normalize({}, spec["result"]),
        "breakdowns": {}, "provenance": str(total.get("_provenance") or "live")}
    for name, value in BREAKDOWNS.items():
        body = adapter.insights_report(external_id, fields, breakdowns=value)
        snap["breakdowns"][name] = [
            {"key": " · ".join(str(r.get(k, "")) for k in _KEYS[name]),
             **normalize(r, spec["result"])}
            for r in (body.get("data") or [])[:50] if isinstance(r, Mapping)]
    return snap


def run_id_for(campaign_id: str) -> str:
    return "ms_" + sha256_hex(campaign_id)[:16]


def save(conn: sqlite3.Connection, ledger: Ledger, campaign_id: str,
         snap: Mapping[str, Any]) -> str:
    snapshot_id = "sn_" + secrets.token_hex(6)
    body = json.dumps(dict(snap), ensure_ascii=False, sort_keys=True)
    with transaction(conn):
        conn.execute("INSERT INTO insights_snapshots (id, campaign_id, body, created_ts) "
                     "VALUES (?, ?, ?, ?)", (snapshot_id, campaign_id, body, time.time()))
    t = snap["totals"]
    ledger.append(run_id_for(campaign_id), agent=AGENT, kind=KIND,
                  output_sha256=sha256_hex(canonical(dict(snap))),
                  details={"campaign": campaign_id, "snapshot": snapshot_id,
                           "objective": snap["objective"], "result": snap["result"],
                           "provenance": snap["provenance"],
                           "attribution": snap["attribution_setting"],
                           **{k: t[k] for k in ("impressions", "reach", "spend", "results",
                                                "frequency")},
                           "breakdown_rows": {k: len(v) for k, v in snap["breakdowns"].items()}})
    return snapshot_id


def recommend(snap: Mapping[str, Any], *, daily_budget_minor: int, ceiling_minor: int,
              offset: int = 100, currency: str = "PLN") -> list[dict[str, Any]]:
    """Fixed rules over one snapshot. ``change`` is set on the executable ones."""
    out = _rules(snap, daily_budget_minor, ceiling_minor, offset)
    for r in out:
        change = r.get("change")
        if change and change["kind"] == "budget":
            change.update({"currency": currency, "from_units": change["from_minor"] / offset,
                           "to_units": change["to_minor"] / offset})
    return out


def _rules(snap: Mapping[str, Any], daily_budget_minor: int, ceiling_minor: int,
           offset: int) -> list[dict[str, Any]]:
    t = snap["totals"]
    out: list[dict[str, Any]] = []
    cpr = t["cost_per_result"]
    spend = t["spend"] or 0.0
    for row in snap["breakdowns"].get("placement", []):
        if (cpr and row["cost_per_result"] and spend
                and row["cost_per_result"] >= COSTLY_RATIO * cpr
                and row["spend"] / spend >= COSTLY_SHARE):
            out.append({"code": "placement_costly", "params": {
                "placement": row["key"], "ratio": round(row["cost_per_result"] / cpr, 1),
                "share": round(100 * row["spend"] / spend)}})
    budget_units = daily_budget_minor / offset
    if t["results"] == 0 and spend >= NO_RESULT_DAYS * budget_units:
        out.append({"code": "no_results", "params": {"spend": spend},
                    "change": {"kind": "status", "status": "PAUSED"}})
    elif t["frequency"] >= FATIGUE_FREQUENCY:
        to = max(offset, int(daily_budget_minor * CUT_STEP))
        out.append({"code": "fatigue", "params": {"frequency": t["frequency"]},
                    "change": {"kind": "budget", "from_minor": daily_budget_minor,
                               "to_minor": to}})
    elif t["results"] > 0 and t["frequency"] < SCALE_FREQUENCY and \
            daily_budget_minor < ceiling_minor:
        to = min(ceiling_minor, int(math.ceil(daily_budget_minor * SCALE_STEP)))
        out.append({"code": "scale", "params": {"frequency": t["frequency"],
                                                "results": t["results"]},
                    "change": {"kind": "budget", "from_minor": daily_budget_minor,
                               "to_minor": to}})
    return out


def store_recommendations(conn: sqlite3.Connection, campaign_id: str, snapshot_id: str,
                          recs: list[dict[str, Any]]) -> list[str]:
    ids = []
    with transaction(conn):
        conn.execute("DELETE FROM recommendations WHERE campaign_id = ? AND status = 'open'",
                     (campaign_id,))
        for r in recs:
            rid = "rc_" + secrets.token_hex(6)
            conn.execute("INSERT INTO recommendations (id, campaign_id, snapshot_id, body, "
                         "created_ts) VALUES (?, ?, ?, ?, ?)",
                         (rid, campaign_id, snapshot_id, json.dumps(r), time.time()))
            ids.append(rid)
    return ids


def latest(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The newest snapshot of every campaign, with its recommendations."""
    with conn.lock:  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT s.id, s.campaign_id, s.body, s.created_ts FROM insights_snapshots s "
            "WHERE s.created_ts = (SELECT max(created_ts) FROM insights_snapshots t "
            "WHERE t.campaign_id = s.campaign_id) ORDER BY s.created_ts DESC LIMIT 10"
        ).fetchall()
        recs = conn.execute("SELECT id, campaign_id, snapshot_id, body, status, approval_id "
                            "FROM recommendations ORDER BY created_ts").fetchall()
    out = []
    for sid, cid, body, ts in rows:
        out.append({"id": sid, "campaign_id": cid, "created_ts": ts, **json.loads(body),
                    "recommendations": [
                        {"id": r[0], **json.loads(r[3]), "status": r[4],
                         "approval_id": r[5]} for r in recs if r[2] == sid]})
    return out


def recommendation(conn: sqlite3.Connection, rec_id: str) -> dict[str, Any] | None:
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute("SELECT id, campaign_id, snapshot_id, body, status FROM "
                           "recommendations WHERE id = ?", (rec_id,)).fetchone()
    if row is None:
        return None
    return {"id": row[0], "campaign_id": row[1], "snapshot_id": row[2], **json.loads(row[3]),
            "status": row[4]}


def summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """What the analyst reads: per campaign the objective, its result, totals and the
    recommendation codes (numbers only)."""
    return [{"campaign": s["campaign_id"], "objective": s["objective"],
             "result": s["result"], "totals": s["totals"],
             "attribution": s["attribution_setting"],
             "placements": s["breakdowns"].get("placement", [])[:6],
             "recommendations": [r["code"] for r in s["recommendations"]]}
            for s in latest(conn)]


def signed_delivery(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    """The delivery block the owner signed for this campaign (traffic for campaigns made
    before objectives were mapped)."""
    from studio.meta import objectives
    from studio.meta.adapter import MOCK_PAGE

    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT p.payload FROM approvals a JOIN pending_payloads p ON p.approval_id = a.id "
            "WHERE a.item_id = ? AND a.action = 'campaign.create' AND a.status = 'approved' "
            "ORDER BY a.decided_ts DESC LIMIT 1", (campaign_id,)).fetchone()
    spec = json.loads(row[0]).get("delivery") if row else None
    return dict(spec) if isinstance(spec, Mapping) else objectives.delivery(
        "traffic", page_id=MOCK_PAGE)


def pull_all(conn: sqlite3.Connection, ledger: Ledger, adapter: Any,
             ceiling: int) -> dict[str, int]:
    """A snapshot and fresh recommendations for every campaign this adapter created (the
    hourly ``studio insights-pull``)."""
    from studio.meta.currency import offset, to_minor

    with conn.lock:  # type: ignore[attr-defined]
        rows = conn.execute("SELECT id, external_id, daily_budget_minor, currency FROM "
                            "campaigns WHERE adapter = ? AND external_id != ''",
                            (adapter.name,)).fetchall()
    n = recs = 0
    for cid, external, budget, currency in rows:
        snap = pull(adapter, external, signed_delivery(conn, cid))
        snapshot_id = save(conn, ledger, cid, snap)
        made = recommend(snap, daily_budget_minor=int(budget),
                         ceiling_minor=to_minor(ceiling, currency), offset=offset(currency),
                         currency=currency)
        recs += len(store_recommendations(conn, cid, snapshot_id, made))
        n += 1
    out = {"campaigns": n, "recommendations": recs}
    # every ad's review state (read-only): a newly disapproved ad is stored and explained
    from studio import disapprovals

    found = disapprovals.check_all(conn, ledger, adapter)
    if found:
        out["disapproved"] = found
    return out
