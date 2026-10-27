"""Lead scorer: deterministic rules decide the score; Muse Spark explains it.

Rules (docs/50 §1.2): base 20; +20 e-mail; +25 phone; +10 city; +15 UK; +10 business;
−30 private; clamped to 0–100. HOT ≥ 80, WARM ≥ 60, else COLD. The model (Standard tier,
customer text inside an envelope) writes persona, short reasons and the next action; it
never sees the e-mail address or phone number (only whether they exist) and cannot change
the score or temperature. A HOT lead gets a "call within 30 min" task and an owner.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from studio import envelope
from studio.agents import prompts
from studio.agents.schemas import Score
from studio.approvals import transaction
from studio.muse.client import MuseClient, MuseResult
from studio.muse.structured import structured
from studio.pii import choose_tier, find_email, find_phone
from studio.receipts import Ledger, redact, sha256_hex

AGENT = "lead_scorer"
HOT, WARM = 80, 60
HOT_CALL_S = 30 * 60
UK_CITIES = frozenset({"london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh",
                       "bristol", "liverpool", "cardiff", "belfast"})
UK_COUNTRIES = frozenset({"uk", "gb", "united kingdom", "great britain", "england",
                          "scotland", "wales"})
BUSINESS_WORDS = ("company", "firma", "ltd", "sp. z o.o", "sp z o o", "s.a.", "business",
                  "jdg", "działalność", "sole proprietorship", "office", "biuro", "coworking",
                  "wholesale", "hurt")
PRIVATE_WORDS = ("private", "prywatnie", "osoba prywatna", "for myself", "dla siebie",
                 "gift for", "prezent dla")
OWNERS = {"business": "Demo Owner A (B2B desk)", "default": "Demo Owner B (shop)"}


@dataclass
class RuleScore:
    score: int
    temp: str
    hits: list[str] = field(default_factory=list)  # rule ids that fired, e.g. "+20 email"
    business: bool = False


def _temp(score: int) -> str:
    return "HOT" if score >= HOT else "WARM" if score >= WARM else "COLD"


def rules(lead: dict[str, Any]) -> RuleScore:
    """The deterministic score. ``lead`` keys: email, phone, city, country, company, kind,
    message (missing keys count as empty)."""
    g = {k: str(lead.get(k) or "").strip() for k in
         ("email", "phone", "city", "country", "company", "kind", "message")}
    text = f"{g['message']} {g['company']}".lower()
    score, hits = 20, ["base 20"]
    if g["email"]:
        score += 20
        hits.append("+20 email")
    if g["phone"]:
        score += 25
        hits.append("+25 phone")
    if g["city"]:
        score += 10
        hits.append("+10 city")
    phone = g["phone"].replace(" ", "")
    uk = (phone.startswith(("+44", "0044")) or g["country"].lower() in UK_COUNTRIES
          or g["city"].lower() in UK_CITIES)
    if uk:
        score += 15
        hits.append("+15 UK")
    business = g["kind"] == "business" or bool(g["company"]) or any(
        w in text for w in BUSINESS_WORDS)
    private = g["kind"] == "private" or any(w in text for w in PRIVATE_WORDS)
    if business and g["kind"] != "private":
        score += 10
        hits.append("+10 business")
    if private:
        score -= 30
        hits.append("-30 private")
    score = max(0, min(100, score))
    return RuleScore(score, _temp(score), hits, business and not private)


def _features(lead: dict[str, Any], rs: RuleScore) -> str:
    """What the model sees: flags and coarse fields, never the e-mail or phone itself."""
    return "\n".join([
        f"Rule score (fixed, do not change): {rs.score} ({rs.temp})",
        f"Rules that fired: {', '.join(rs.hits)}",
        f"Has e-mail: {'yes' if lead.get('email') else 'no'}",
        f"Has phone: {'yes' if lead.get('phone') else 'no'}",
        f"Source: {lead.get('source') or 'unknown'}",
    ])


def explain(client: MuseClient, lead: dict[str, Any], rs: RuleScore, *,
            run_id: str) -> tuple[Score, MuseResult]:
    """Persona, reasons and next action from Muse Spark; score and temp stay the rules'."""
    # contact data inside free text (a comment that quotes an e-mail) is redacted too
    free_text = redact("\n".join(
        f"{k}: {lead.get(k)}" for k in ("city", "country", "company", "message")
        if lead.get(k)))
    user = (_features(lead, rs) + "\n\nLead details:\n"
            + envelope.wrap(free_text or "(none)", "lead", max_chars=1500)
            + "\n\nExplain the score for the owner.")
    tier, reason, model = choose_tier(user, "standard", source="lead")
    raw, result = structured(
        client, model,
        [{"role": "system", "content": prompts.system(AGENT) + "\n\n" + envelope.SYSTEM_RULE},
         {"role": "user", "content": user}],
        Score, tier=tier, tier_reason=reason, run_id=run_id, agent=AGENT, max_tokens=2000,
        reasoning_effort="low", envelope_sources=["lead"],
    )
    reasons = [envelope.clean(r, max_chars=160) for r in raw.reasons[:4]]
    return raw.model_copy(update={
        "score": rs.score, "temp": rs.temp, "reasons": reasons,
        "persona": envelope.clean(raw.persona, max_chars=80),
        "next_action": envelope.clean(raw.next_action, max_chars=200)}), result


def run_id_for(lead_id: str) -> str:
    return "ld_" + sha256_hex(lead_id)[:16]


def load(conn: sqlite3.Connection, lead_id: str) -> dict[str, Any] | None:
    cols = ("id", "name", "email", "phone", "source", "message", "city", "country", "company",
            "kind")
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute(f"SELECT {', '.join(cols)} FROM leads WHERE id = ?",  # noqa: S608
                           (lead_id,)).fetchone()
    return dict(zip(cols, row, strict=True)) if row else None


def score_lead(conn: sqlite3.Connection, ledger: Ledger, client: MuseClient | None,
               lead_id: str, now: float | None = None) -> dict[str, Any]:
    """Rules → (optional) rationale → CRM card fields + a HOT task; receipts for each."""
    lead = load(conn, lead_id)
    if lead is None:
        raise KeyError(lead_id)
    rs = rules(lead)
    run_id = run_id_for(lead_id)
    persona, reasons, next_action, model_receipt = "", list(rs.hits), "", ""
    if client is not None:
        score, result = explain(client, lead, rs, run_id=run_id)
        persona, next_action, model_receipt = score.persona, score.next_action, result.receipt_id
        reasons = list(rs.hits) + score.reasons
    owner = OWNERS["business"] if rs.business else OWNERS["default"]
    clock = time.time() if now is None else now
    task_id = ""
    with transaction(conn):
        conn.execute(
            "UPDATE leads SET score = ?, temp = ?, persona = ?, owner = ?, reasons = ?, "
            "next_action = ?, run_id = ? WHERE id = ?",
            (rs.score, rs.temp, persona, owner, json.dumps(reasons, ensure_ascii=False),
             next_action, run_id, lead_id))
        if rs.temp == "HOT":
            have = conn.execute("SELECT id FROM tasks WHERE lead_id = ? AND done = 0 AND "
                                "title LIKE 'Call within 30 min%'", (lead_id,)).fetchone()
            if have is None:
                task_id = "t-" + secrets.token_hex(5)
                conn.execute("INSERT INTO tasks (id, lead_id, title, due_ts, done) "
                             "VALUES (?, ?, ?, ?, 0)",
                             (task_id, lead_id, f"Call within 30 min ({owner})",
                              clock + HOT_CALL_S))
    ledger.append(run_id, agent=AGENT, kind="tool_call",
                  output_sha256=sha256_hex(json.dumps(reasons, ensure_ascii=False)),
                  details={"tool": "lead_score", "status": "ok", "side_effects": "local",
                           "score": rs.score, "temp": rs.temp, "rules": rs.hits,
                           "task": bool(task_id), "model_receipt": model_receipt})
    return {"lead_id": lead_id, "score": rs.score, "temp": rs.temp, "reasons": reasons,
            "persona": persona, "owner": owner, "next_action": next_action, "task_id": task_id}


def lead_from_comment(conn: sqlite3.Connection, comment_id: str) -> str:
    """A CRM lead for a comment triaged as ``intent=lead`` (idempotent per comment)."""
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute("SELECT author_name, text, lead_id FROM comments WHERE id = ?",
                           (comment_id,)).fetchone()
    if row is None:
        raise KeyError(comment_id)
    if row[2]:
        return str(row[2])
    lead_id = "l-" + secrets.token_hex(5)
    text = str(row[1])
    with transaction(conn):
        conn.execute(
            "INSERT INTO leads (id, name, email, phone, source, message, comment_id, "
            "created_ts) VALUES (?, ?, ?, ?, 'comment', ?, ?, ?)",
            (lead_id, row[0], find_email(text), find_phone(text),
             envelope.clean(text, max_chars=1000), comment_id, time.time()))
        conn.execute("UPDATE comments SET lead_id = ? WHERE id = ?", (lead_id, comment_id))
    return lead_id
