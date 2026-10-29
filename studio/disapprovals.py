"""A7/B7: Meta disapproved an ad after launch — read, explain, propose, sign.

- ``check_all`` (read-only, part of every insights pull): ``GET /{ad_id}?fields=
  effective_status,ad_review_feedback,issues_info`` for every ad this studio created. A
  newly DISAPPROVED ad is stored (``disapprovals``) with Meta's reasons and the checklist
  rule they map to, and leaves one ``ad.review`` receipt (keys and codes, no free text).
- ``explain``: Meta's reason in one plain sentence (EN/PL) from the rules table
  (``agents.rules``), with the policy link, next to Meta's own words.
- ``propose``: the copywriter writes a compliant variant from the same facts (the run's
  confirmed Brief v1, when it has one) and Meta's reason as the blocked finding; both
  reviewers re-check it on the approved hero image. Only a variant that is not BLOCKed
  becomes a pending ``campaign.update`` approval of kind ``replace_creative``: a new
  creative and a new ad under the same ad set, created PAUSED. Nothing is sent to Meta.
- ``replace`` (the executor, behind ``executor.authorize``): the three calls the signed
  payload names. The disapproved ad is **left as it is**: Meta does not deliver a
  disapproved ad, leaving it makes no write the owner did not need, and it keeps the
  owner's option to ask Meta for another review in Ads Manager. The new ad stays PAUSED
  until the owner activates it.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from studio import brief as brief_v1
from studio import executor
from studio.agents import rules
from studio.approvals import Pending, Verified, confirmed_brief, transaction
from studio.meta.adapter import DISAPPROVED, PAUSED, REPLACE, Adapter, AdapterError
from studio.receipts import Ledger, canonical, sha256_hex

AGENT = "media_buyer"
KIND = "ad.review"
SCHEMA = """
CREATE TABLE IF NOT EXISTS disapprovals (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    ad_external_id TEXT NOT NULL,
    body TEXT NOT NULL,
    rule TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'blocked', 'requested', 'replaced', 'failed')),
    approval_id TEXT NOT NULL DEFAULT '',
    proposal TEXT NOT NULL DEFAULT '{}',
    created_ts REAL NOT NULL,
    UNIQUE (campaign_id, ad_external_id)
);
"""
# Meta's reason (its key and description, lower case) → the checklist rule that explains
# it; the first match wins. Anything else is shown with Meta's own words and the
# Advertising Standards link (``META``).
REASON_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("personal attribute",), "P1"),
    (("before", "after", "body image", "ideal body"), "P2"),
    (("health", "medical", "weight", "cure", "drug", "pharma", "financial", "credit",
      "loan"), "P8"),
    (("non-existent", "nonexistent", "functionality", "button"), "P6"),
    (("adult", "weapon", "tobacco", "alcohol", "shocking", "sensational"), "P7"),
    (("misleading", "unacceptable business", "exaggerat", "guarantee", "scam", "deceptive",
      "unrealistic"), "P3"),
)
META = "META"  # a reason no checklist rule explains


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


class DisapprovalError(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def rule_for(reasons: list[dict[str, str]]) -> str:
    text = " ".join(f"{r.get('key', '')} {r.get('text', '')}" for r in reasons).lower()
    for words, rule_id in REASON_RULES:
        if any(w in text for w in words):
            return rule_id
    return META


def explain(rule_id: str, lang: str = "en") -> dict[str, str]:
    """The rule in one plain sentence and its source (Meta's Advertising Standards when
    no checklist rule explains the reason)."""
    if rule_id in rules.RULES:
        return {"rule": rule_id, "sentence": rules.explain(rule_id, lang),
                "source": rules.source(rule_id)}
    return {"rule": META, "source": rules.META_STANDARDS, "sentence": (
        "Meta found the ad against its Advertising Standards; its own words are below."
        if lang != "pl" else
        "Meta uznała reklamę za niezgodną ze Standardami reklamowymi; jej słowa są niżej.")}


def _ads(conn: sqlite3.Connection, adapter_name: str) -> list[tuple[str, str, str]]:
    with conn.lock:  # type: ignore[attr-defined]
        rows = conn.execute("SELECT id, run_id, ad_external_id, more_ad_ids FROM campaigns "
                            "WHERE adapter = ? AND ad_external_id != ''",
                            (adapter_name,)).fetchall()
    out = []
    for cid, run_id, ad, more in rows:
        for ad_id in [ad, *json.loads(more or "[]")]:
            out.append((cid, run_id, ad_id))
    return out


def check_all(conn: sqlite3.Connection, ledger: Ledger, adapter: Adapter) -> int:
    """Read every ad's review state; store each newly disapproved ad. Returns how many
    are new. A failed read is skipped (it is retried on the next pull)."""
    new = 0
    for cid, run_id, ad_id in _ads(conn, adapter.name):
        try:
            state = adapter.ad_review(ad_id)
        except (AdapterError, AttributeError):
            continue
        if state.get("effective_status") != DISAPPROVED:
            continue
        rule_id = rule_for(state.get("reasons") or [])
        did = "dp_" + secrets.token_hex(6)
        with transaction(conn):
            cur = conn.execute(
                "INSERT OR IGNORE INTO disapprovals (id, campaign_id, ad_external_id, body, "
                "rule, created_ts) VALUES (?, ?, ?, ?, ?, ?)",
                (did, cid, ad_id, json.dumps(state, ensure_ascii=False), rule_id,
                 time.time()))
        if cur.rowcount != 1:
            continue  # already known
        new += 1
        ledger.append(run_id, agent=AGENT, kind=KIND,
                      output_sha256=sha256_hex(canonical(state)),
                      details={"campaign": cid, "disapproval": did,
                               "ad": executor.safe_id(ad_id),
                               "effective_status": DISAPPROVED, "rule": rule_id,
                               "reasons": sorted({r["key"] for r in state["reasons"]})[:10],
                               "issues": [i["error_code"] for i in state["issues"]][:10],
                               "side_effects": "read"})
    return new


def _row(conn: sqlite3.Connection, did: str) -> dict[str, Any] | None:
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute("SELECT id, campaign_id, ad_external_id, body, rule, status, "
                           "approval_id, proposal, created_ts FROM disapprovals WHERE id = ?",
                           (did,)).fetchone()
    if row is None:
        return None
    keys = ("id", "campaign_id", "ad_external_id", "body", "rule", "status", "approval_id",
            "proposal", "created_ts")
    out = dict(zip(keys, row, strict=True))
    out["body"] = json.loads(out["body"])
    out["proposal"] = json.loads(out["proposal"] or "{}")
    return out


PUBLIC_FIELDS = ("id", "campaign_id", "rule", "status", "created_ts", "explain")


def public_view(d: dict[str, Any]) -> dict[str, Any]:
    """What a visitor without a session sees (like comments and leads): the rule, its
    explanation and Meta's reason keys, every string redacted; never the proposal, the
    approval id, the Graph ad id or the raw issues."""
    from studio.receipts import redact

    out = {k: d[k] for k in PUBLIC_FIELDS if k in d}
    out["body"] = redact({"reasons": [{"key": r.get("key", ""), "text": r.get("text", ""),
                                       "scope": r.get("scope", "")}
                                      for r in (d.get("body") or {}).get("reasons") or []]})
    out["proposal"], out["approval_id"] = {}, ""
    return out


def listing(conn: sqlite3.Connection, lang: str = "en",
            full: bool = False) -> list[dict[str, Any]]:
    """Every disapproval, newest first, with its explanation (for the inbox). Without
    ``full`` (no session) each one is ``public_view``."""
    rows = _listing(conn, lang)
    return rows if full else [public_view(d) for d in rows]


def _listing(conn: sqlite3.Connection, lang: str) -> list[dict[str, Any]]:
    with conn.lock:  # type: ignore[attr-defined]
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM disapprovals ORDER BY created_ts DESC LIMIT 20").fetchall()]
    out = []
    for did in ids:
        d = _row(conn, did)
        if d is not None:
            d["explain"] = explain(d["rule"], lang)
            out.append(d)
    return out


def _signed_create(conn: sqlite3.Connection, campaign_id: str) -> dict[str, Any]:
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT p.payload FROM approvals a JOIN pending_payloads p ON p.approval_id = a.id "
            "WHERE a.item_id = ? AND a.action = 'campaign.create' AND a.status = 'approved' "
            "ORDER BY a.decided_ts DESC LIMIT 1", (campaign_id,)).fetchone()
    if row is None:
        raise DisapprovalError("no signed campaign for this ad", "not_found")
    return json.loads(row[0])  # type: ignore[no-any-return]


def _brief(conn: sqlite3.Connection, run_id: str) -> tuple[brief_v1.BriefV1 | None, str]:
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute("SELECT brief_v1, brief_sha256 FROM briefs WHERE run_id = ? AND "
                           "brief_v1 IS NOT NULL AND brief_v1 != ''", (run_id,)).fetchone()
    if row is None:
        return None, ""
    return brief_v1.BriefV1.model_validate(json.loads(row[0])), str(row[1] or "")


def propose(conn: sqlite3.Connection, ledger: Ledger, client: Any, open_client: Any,
            did: str, data_dir: Path) -> dict[str, Any]:
    """One compliant variant (copywriter + both reviewers), then a pending
    ``campaign.update`` (``replace_creative``) for the owner to sign. A variant the
    reviewers BLOCK, or one with an unfilled ``[FACT]``, is not proposed (``blocked``)."""
    from studio.agents import copywriter, media_buyer, reviewer
    from studio.agents.schemas import Angle

    d = _row(conn, did)
    if d is None:
        raise DisapprovalError("no such disapproval", "not_found")
    if d["status"] not in ("open", "blocked"):
        raise DisapprovalError("a replacement was already requested", "not_open")
    c = media_buyer.campaign(conn, d["campaign_id"])
    if c is None or not c["adset_external_id"]:
        raise DisapprovalError("no such campaign", "not_found")
    signed = _signed_create(conn, c["id"])
    brief, digest = _brief(conn, c["run_id"])
    if digest and confirmed_brief(ledger, c["run_id"]) != digest:
        raise DisapprovalError("the confirmed brief changed", "brief_changed")
    old = signed["ad"]
    angle = Angle(name=str(signed["campaign"].get("angle", "campaign"))[:80],
                  hook=old["headline"], persona=str(signed.get("audience", ""))[:200])
    reasons = d["body"].get("reasons") or []
    meta_words = "; ".join(f"{r['key']}: {r['text']}" for r in reasons)[:600] or DISAPPROVED
    blocked = [{"rule": d["rule"] if d["rule"] != META else "P3",
                "evidence": f"Meta disapproved the running ad ({meta_words}). The ad said: "
                            f"{old['headline']} | {old['primary_text']}"[:900]}]
    pack, _ = copywriter.write(
        client, angle, angle.persona, run_id=c["run_id"],
        brief=brief_v1.render(brief) if brief is not None else "",
        facts=brief.confirmed_facts if brief is not None else None, blocked=blocked)
    variant = pack.variants[0]
    hero = signed["images"][0]
    png = (data_dir / "assets" / c["run_id"] / f"{hero['placement']}.png").read_bytes()
    if sha256_hex(png) != hero["sha256"]:
        raise DisapprovalError("the approved image changed on disk", "image_changed")
    channels = ("meta", "google") if brief is not None and brief.channels.google_search \
        else ("meta",)
    review = reviewer.run(client, open_client, png, variant, run_id=c["run_id"],
                          placement=hero["placement"], brief=brief, channels=channels)
    proposal = {"variant": variant.model_dump(), "verdict": review.verdict,
                "left_out": [x.model_dump() for x in pack.left_out],
                "findings": [f.model_dump() for f in review.primary.findings]}
    if review.verdict == "BLOCK" or copywriter.has_unfilled_fact(variant) or (
            brief is not None and reviewer.grounding_net(variant, brief)):
        with transaction(conn):
            conn.execute("UPDATE disapprovals SET status = 'blocked', proposal = ? "
                         "WHERE id = ?", (json.dumps(proposal, ensure_ascii=False), did))
        return {"status": "blocked", "proposal": proposal}
    payload = {
        "action": "campaign.update", "campaign_id": c["id"], "run_id": c["run_id"],
        "adapter": c["adapter"], "external_id": c["external_id"],
        "adset_external_id": c["adset_external_id"], "currency": c["currency"],
        "change": {"kind": REPLACE, "disapproved_ad": d["ad_external_id"],
                   "disapproved_ad_action": "left_as_is", "new_ad_status": PAUSED},
        "disapproval": {"id": did, "rule": d["rule"],
                        "reasons": [r["key"] for r in reasons][:10]},
        "delivery": signed["delivery"], "campaign": {"angle": angle.name},
        "link": signed.get("link", ""), "ad": variant.model_dump(), "image": dict(hero),
        "review": {"verdict": review.verdict, "second_opinion": review.second_status,
                   "disagreements": review.disagreements},
        **({"left_out": proposal["left_out"]} if proposal["left_out"] else {}),
        **({"brief_sha256": digest} if digest else {}),
        **({"form_external_id": c["created"].get("lead_form_id", "")}
           if signed["delivery"].get("creative") == "lead_form" else {}),
    }
    pending = Pending.create("campaign.update", c["id"], payload, ledger=ledger,
                             run_id=c["run_id"])
    with transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (pending.id, c["run_id"], canonical(payload).decode(), time.time()))
        conn.execute("UPDATE disapprovals SET status = 'requested', approval_id = ?, "
                     "proposal = ? WHERE id = ?",
                     (pending.id, json.dumps(proposal, ensure_ascii=False), did))
    return {"status": "requested", "approval_id": pending.id, "content": payload,
            "content_sha256": pending.content_sha256, "proposal": proposal}


def replace(conn: sqlite3.Connection, ledger: Ledger, adapter: Adapter, v: Verified,
            payload: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    """Execute a signed replacement (``v`` is already authorized): upload the approved
    image, create the creative, create the ad PAUSED under the signed ad set."""
    from studio.agents import media_buyer

    c = media_buyer.campaign(conn, v.item_id)
    did = str((payload.get("disapproval") or {}).get("id", ""))
    d = _row(conn, did)
    problem = ""
    if c is None or [c["external_id"], c["adset_external_id"], c["adapter"]] != [
            payload.get("external_id"), payload.get("adset_external_id"), adapter.name]:
        problem = "campaign_changed"
    elif d is None or d["approval_id"] != v.pending_id or d["campaign_id"] != v.item_id:
        problem = "not_proposed"
    elif payload.get("brief_sha256") and confirmed_brief(
            ledger, v.run_id) != payload["brief_sha256"]:
        problem = "brief_changed"
    if problem:
        executor.finish(conn, v, "failed", {"error": problem})
        executor.record_step(ledger, v, agent=AGENT, adapter=adapter.name, step="update",
                             status="rejected", details={"reason": problem})
        raise DisapprovalError(f"replacement refused: {problem}", problem)
    assert c is not None
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
                                                   **({"object_status": PAUSED}
                                                      if name == "replace_ad" else {})})
        return out

    try:
        image = payload["image"]
        png = (data_dir / "assets" / str(payload["run_id"]) / f"{image['placement']}.png"
               ).read_bytes()
        image_hash = step("replace_image", adapter.upload_replacement_image, png, payload, v)
        creative = step("replace_creative", adapter.create_replacement_creative, image_hash,
                        payload, v)
        ad_id = step("replace_ad", adapter.create_replacement_ad, creative, payload, v)
    except Exception as exc:
        if created:
            executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, details={
                "status": "partial", "change": REPLACE,
                "objects": {k: executor.safe_id(x) for k, x in created.items()}})
        executor.finish(conn, v, "failed", {"error": getattr(exc, "code", type(exc).__name__),
                                            "created": created})
        with transaction(conn):
            conn.execute("UPDATE disapprovals SET status = 'failed' WHERE id = ?", (did,))
        raise
    executor.record_write(ledger, v, agent=AGENT, adapter=adapter.name, details={
        "status": "ok", "change": REPLACE, "object_status": PAUSED,
        "disapproved_ad": "left_as_is",
        "objects": {k: executor.safe_id(x) for k, x in created.items()}})
    with transaction(conn):
        more = [*c["more_ad_external_ids"], ad_id]
        conn.execute("UPDATE campaigns SET more_ad_ids = ? WHERE id = ?",
                     (json.dumps(more), v.item_id))
        conn.execute("UPDATE disapprovals SET status = 'replaced' WHERE id = ?", (did,))
    result = {"adapter": adapter.name, "campaign_item_id": v.item_id,
              "change": dict(payload["change"]), "ad_id": ad_id, "status": PAUSED}
    executor.finish(conn, v, "done", result)
    return result
