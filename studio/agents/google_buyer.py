"""Google buyer (executor, not a model): turns a signed approval into a PAUSED Search
campaign.

- ``create``: on a consumed ``google.create`` approval → ``googleAds:mutate`` with
  ``validateOnly`` (the dry run: nothing is created) → the same mutate for real: budget,
  campaign, geo target, ad group, keywords and one Responsive Search Ad, all from the
  signed ``google`` spec, all ``PAUSED``. The spec's hash must be the approval's
  ``spec_sha256``. One ``tool_call`` receipt per adapter call (each names the spec hash)
  and one ``external_write`` receipt for the approved write, so ``studio verify`` links it
  to the signature. The request is atomic: either everything exists or nothing does.
- ``request_activation`` / ``activate``: a second, separate approval (``google.activate``)
  naming the campaign, ad group and ad, the daily budget and the ceiling
  (``STUDIO_MAX_DAILY_BUDGET``, in the Google account's currency, checked again at
  execution time). One mutate enables the ad, the ad group, then the campaign.

Nothing here is reachable from a model: every entry point starts with
``executor.authorize``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from studio import executor
from studio.agents.media_buyer import BuyError
from studio.approvals import Pending, Verified, confirmed_brief, transaction
from studio.google import store
from studio.google.adapter import (
    PAUSED,
    Adapter,
    check_spec,
    micros,
    spec_sha256,
)
from studio.meta.adapter import check_paused
from studio.receipts import Ledger, canonical

AGENT = "google_buyer"
CHANNEL = "google"


def create(conn: sqlite3.Connection, ledger: Ledger, adapter: Adapter, verified: Any,
           payload: dict[str, Any]) -> dict[str, Any]:
    check_paused(payload)  # before anything: an ENABLED creation payload is never executed
    v: Verified = executor.authorize(conn, verified, "google.create", payload)
    if v.brief_sha256 and (confirmed_brief(ledger, v.run_id) != v.brief_sha256
                           or payload.get("brief_sha256") != v.brief_sha256):
        executor.finish(conn, v, "failed", {"error": "brief_changed"})
        raise BuyError("the confirmed brief changed after the approval", "brief_changed")
    raw = payload.get("google")
    spec_hash = spec_sha256(raw) if isinstance(raw, dict) else ""
    if not spec_hash or spec_hash != v.spec_sha256:
        # the spec is not the one the human signed: nothing is created
        executor.finish(conn, v, "failed", {"error": "spec_changed"})
        raise BuyError("the google spec is not the signed one", "spec_changed")
    try:
        spec = check_spec(raw)
        currency = adapter.account_info()["currency"]
    except Exception as exc:
        executor.finish(conn, v, "failed", {"error": getattr(exc, "code", "google_spec")})
        raise
    if currency != spec["currency"]:
        executor.finish(conn, v, "failed", {"error": "currency_changed"})
        raise BuyError(f"the Google account books budgets in {currency}, not "
                       f"{spec['currency']}", "currency_changed")
    details = {"spec_sha256": spec_hash, "channel": "google_search"}

    def step(name: str, validate_only: bool) -> dict[str, str]:
        try:
            out = adapter.create_search_campaign(payload, v, validate_only=validate_only)
        except Exception as exc:
            executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step=name,
                                 status="error", channel=CHANNEL,
                                 details={**details, "error": getattr(exc, "code",
                                                                      type(exc).__name__)})
            raise
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step=name,
                             status="ok", channel=CHANNEL, details={
                                 **details, **({"dry_run": True} if validate_only else {
                                     "object": executor.safe_id(out["campaign"]),
                                     "object_status": PAUSED})})
        return out

    try:
        step("google_validate", True)
        names = step("google_create", False)
    except Exception as exc:
        executor.finish(conn, v, "failed", {"status": "failed",
                                            "error": getattr(exc, "code", type(exc).__name__)})
        raise
    executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, channel=CHANNEL,
                          details={"status": "ok", "object_status": PAUSED, **details,
                                   "objects": {k: executor.safe_id(x)
                                               for k, x in names.items()}})
    with transaction(conn):
        conn.execute(
            "INSERT INTO google_campaigns (id, run_id, name, status, daily_budget_micros, "
            "currency, adapter, customer_id, resources, created_ts) "
            "VALUES (?, ?, ?, 'PAUSED', ?, ?, ?, ?, ?, ?)",
            (v.item_id, v.run_id, spec["campaign"]["name"],
             int(spec["campaign"]["daily_budget_micros"]), spec["currency"], adapter.name,
             spec["customer_id"], json.dumps(names), time.time()))
    result = {"adapter": adapter.name, "status": PAUSED, "campaign_item_id": v.item_id,
              "channel": "google_search"}
    executor.finish(conn, v, "done", result)
    return result


def payload_for(*, run_id: str, item_id: str, brief: Any, brief_sha256: str, link: str,
                variants: list[Any], customer_id: str, currency: str,
                ceiling: int) -> dict[str, Any]:
    """The exact content a ``google.create`` approval authorises (built by code from the
    confirmed brief and the reviewed copy variants). ``SpecError`` when the brief cannot
    make a valid Responsive Search Ad, the budget is above the ceiling or the currency
    is not the brief's."""
    from studio.google.adapter import SpecError, build_spec

    ch = brief.channels
    if ch.google_daily_budget > ceiling:
        raise SpecError("the Google daily budget is above the ceiling", "over_ceiling")
    if brief.budget.currency != currency:
        raise SpecError("the brief's currency is not the Google account's",
                        "google_account_currency")
    spec = build_spec(
        item_id=item_id, customer_id=customer_id, currency=currency,
        display_name=brief.advertiser.display_name, product=brief.offer.product,
        offer=brief.offer.offer, proposition=brief.proposition,
        countries=list(brief.advertiser.countries), locations=list(brief.audience.locations),
        link=link, variants=[{k: getattr(v, k, "") for k in ("headline", "description",
                                                             "primary_text")}
                             for v in variants],
        daily_budget=ch.google_daily_budget,
        facts=[*brief.confirmed_facts, *(r.text for r in brief.offer.reasons_to_believe)])
    return {"action": "google.create", "run_id": run_id, "google": spec,
            "brief_sha256": brief_sha256, "link": link}


def campaign(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    return store.campaign(conn, item_id)


def request_activation(conn: sqlite3.Connection, ledger: Ledger, item_id: str,
                       ceiling: int) -> tuple[Pending, dict[str, Any]]:
    """A pending ``google.activate`` for a PAUSED campaign within the budget ceiling."""
    c = store.campaign(conn, item_id)
    if c is None:
        raise BuyError("no such campaign", "not_found")
    if c["status"] != PAUSED:
        raise BuyError("campaign is not PAUSED", "not_paused")
    res = c["resources"]
    if not all(res.get(k) for k in ("campaign", "ad_group", "ad_group_ad")):
        raise BuyError("campaign, ad group or ad missing", "incomplete")
    ceiling_micros = micros(ceiling)
    if c["daily_budget_micros"] > ceiling_micros:
        raise BuyError(f"daily budget above the {ceiling} {c['currency']} ceiling",
                       "over_ceiling")
    payload = {"action": "google.activate", "campaign_id": c["id"], "run_id": c["run_id"],
               "customer_id": c["customer_id"], "campaign": res["campaign"],
               "ad_group": res["ad_group"], "ad_group_ad": res["ad_group_ad"],
               "adapter": c["adapter"], "daily_budget_micros": c["daily_budget_micros"],
               "ceiling_micros": ceiling_micros, "currency": c["currency"]}
    pending = Pending.create("google.activate", c["id"], payload, ledger=ledger,
                             run_id=c["run_id"])
    with transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (pending.id, c["run_id"], canonical(payload).decode(), time.time()))
    return pending, payload


def activate(conn: sqlite3.Connection, ledger: Ledger, adapter: Adapter, verified: Any,
             payload: dict[str, Any], ceiling: int) -> dict[str, Any]:
    v: Verified = executor.authorize(conn, verified, "google.activate", payload)
    c = store.campaign(conn, v.item_id)
    problem = ""
    res = c["resources"] if c else {}
    if c is None or c["status"] != PAUSED:
        problem = "not_paused"
    elif ([res.get("campaign"), res.get("ad_group"), res.get("ad_group_ad"),
           c["daily_budget_micros"], c["adapter"], c["currency"], c["customer_id"]]
          != [payload.get("campaign"), payload.get("ad_group"), payload.get("ad_group_ad"),
              payload.get("daily_budget_micros"), adapter.name, payload.get("currency"),
              payload.get("customer_id")]):
        problem = "campaign_changed"
    elif int(payload["daily_budget_micros"]) > min(int(payload["ceiling_micros"]),
                                                   micros(ceiling)):
        problem = "over_ceiling"
    if problem:
        executor.finish(conn, v, "failed", {"error": problem})
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name,
                             step="google_activate", status="rejected", channel=CHANNEL,
                             details={"reason": problem})
        raise BuyError(f"activation refused: {problem}", problem)
    try:
        adapter.activate(payload, v)
    except Exception as exc:
        error = getattr(exc, "code", type(exc).__name__)
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name,
                             step="google_activate", status="error", channel=CHANNEL,
                             details={"error": error})
        executor.finish(conn, v, "failed", {"error": error})
        raise
    executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step="google_activate",
                         status="ok", channel=CHANNEL, details={
                             "object": executor.safe_id(payload["campaign"]),
                             "object_status": "ENABLED"})
    executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, channel=CHANNEL,
                          details={"status": "ok", "object_status": "ENABLED",
                                   "daily_budget_micros": payload["daily_budget_micros"]})
    with transaction(conn):
        conn.execute("UPDATE google_campaigns SET status = 'ENABLED' WHERE id = ?", (v.item_id,))
    result = {"adapter": adapter.name, "status": "ENABLED", "campaign_item_id": v.item_id,
              "channel": "google_search"}
    executor.finish(conn, v, "done", result)
    return result
