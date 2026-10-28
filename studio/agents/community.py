"""Community manager: triage one comment, draft a reply, never send anything.

- The comment is customer text: PII is detected on the original, then e-mails, phone
  numbers, NIP/PESEL and long digit runs are masked (``pii.redact``) and the masked
  comment enters the prompt only inside an untrusted envelope
  (``envelope.wrap``, declared to the client as ``envelope_sources=["comment"]``), on the
  Standard tier (``choose_tier(..., source="comment")``), with no tools in the request.
- The model returns a strict ``Triage``. Code then enforces the rules the model cannot
  overrule: complaints and drafts quoting prices outside the approved price sheet, links
  or personal data need a human; a comment that looks like an injection is flagged.
- The action set is a constant of this module (``ACTIONS``): the draft is stored
  automatically (local), ``reply.post`` and ``comment.hide`` exist only as pending approvals
  a human signs. Nothing in the triage can add, remove or trigger an action.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from studio import envelope
from studio.agents import prompts
from studio.agents.schemas import Triage
from studio.approvals import Pending, transaction
from studio.muse.client import MuseClient, MuseResult
from studio.muse.structured import structured
from studio.pii import choose_tier, contains_pii, long_digit_run, redact
from studio.receipts import Ledger, canonical, is_opaque, sha256_hex

AGENT = "community"
LOCAL_ACTIONS = ("draft",)
SIGNED_ACTIONS = ("reply.post", "comment.hide")
ACTIONS = LOCAL_ACTIONS + SIGNED_ACTIONS
MAX_COMMENT = 2000
MAX_REPLY = 600
PRICE_SHEET = (
    "- Hand-thrown stoneware mug, 350 ml: 89 PLN\n"
    "- Delivery in Poland: free above 150 PLN\n"
    "No other prices, discounts or bundles exist."
)
APPROVED_AMOUNTS = frozenset({"89", "150"})
_PRICE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:pln|zł|zl|złotych|usd|eur|\$|€)", re.IGNORECASE)
_LINK = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(?:com|pl|net|org|io|ly)/", re.IGNORECASE)
_INJECTION = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)"
    r"|disregard (?:the |your )?(?:instructions|rules)|system prompt|you are now|"
    r"developer mode|jailbreak|zignoruj|pomiń (?:poprzednie|wcześniejsze)|"
    r"\bact as\b|new instructions|<\s*/?\s*system\s*>|\"tool\"\s*:|function_call",
    re.IGNORECASE,
)


@dataclass
class Triaged:
    triage: Triage
    flags: list[str] = field(default_factory=list)
    actions: tuple[str, ...] = ACTIONS
    result: MuseResult | None = None


def run_id_for(comment_id: str) -> str:
    """The receipt run of one comment's thread (opaque, fits the run-id pattern)."""
    return "ib_" + sha256_hex(comment_id)[:16]


def item_id_for(comment_id: str) -> str:
    return comment_id if is_opaque(comment_id) and len(comment_id) <= 40 else (
        "cm_" + sha256_hex(comment_id)[:16])


def _user_message(text: str) -> str:
    return (
        "Approved price sheet (the only prices you may quote):\n"
        f"{PRICE_SHEET}\n\n"
        "One comment under our ad:\n"
        f"{envelope.wrap(text, 'comment', max_chars=MAX_COMMENT)}\n\n"
        "Triage it and draft one short reply in the comment's language."
    )


def system_prompt() -> str:
    return prompts.system(AGENT) + "\n\n" + envelope.SYSTEM_RULE


def enforce(triage: Triage, comment: str) -> tuple[Triage, list[str]]:
    """Rules applied in code after the model; they only ever make the result stricter."""
    flags: list[str] = []
    draft = envelope.clean(triage.draft, max_chars=MAX_REPLY)
    risk = triage.risk
    human = triage.requires_human
    order = {"low": 0, "medium": 1, "high": 2}

    def at_least(level: str) -> None:
        nonlocal risk
        if order[level] > order[risk]:
            risk = level  # type: ignore[assignment]

    if _INJECTION.search(envelope.neutralize(comment)):
        flags.append("injection_suspected")
        at_least("medium")
    if contains_pii(comment) or long_digit_run(comment):
        flags.append("pii_in_comment")
    if triage.intent == "complaint":
        human = True
        at_least("medium")
        flags.append("complaint")
    amounts = {a.replace(",", ".").removesuffix(".00") for a in _PRICE.findall(draft)}
    if amounts - APPROVED_AMOUNTS:
        flags.append("unapproved_price")
        human = True
        at_least("high")
    if _LINK.search(draft):
        flags.append("link_in_draft")
        human = True
        at_least("medium")
    if contains_pii(draft) or long_digit_run(draft):
        flags.append("pii_in_draft")
        human = True
        at_least("medium")
    if risk == "high":
        human = True
    confidence = min(max(float(triage.confidence), 0.0), 1.0)
    return triage.model_copy(update={"draft": draft, "risk": risk, "requires_human": human,
                                     "confidence": confidence,
                                     "reason": envelope.clean(triage.reason, max_chars=300)}
                             ), flags


def triage(
    client: MuseClient, comment: str, *, run_id: str, max_tokens: int = 2000
) -> Triaged:
    """One strict ``Triage`` for ``comment``; no tools, envelope declared, Standard tier."""
    comment = comment[:MAX_COMMENT]
    # Tier and flags are decided on the original; the model sees contact data masked.
    tier, reason, model = choose_tier(comment, "standard", source="comment")
    raw, result = structured(
        client, model,
        [{"role": "system", "content": system_prompt()},
         {"role": "user", "content": _user_message(redact(comment))}],
        Triage, tier=tier, tier_reason=reason, run_id=run_id, agent=AGENT,
        max_tokens=max_tokens, reasoning_effort="low", envelope_sources=["comment"],
    )
    checked, flags = enforce(raw, comment)
    return Triaged(checked, flags, ACTIONS, result)


# -- storage and approvals ------------------------------------------------------------


def save(conn: sqlite3.Connection, ledger: Ledger, comment_id: str, t: Triaged) -> None:
    """Store the draft (the only automatic action) and leave a local ``tool_call`` receipt."""
    tr = t.triage
    run_id = run_id_for(comment_id)
    with transaction(conn):
        conn.execute(
            "UPDATE comments SET status = 'drafted', intent = ?, draft = ?, sentiment = ?, "
            "risk = ?, requires_human = ?, reason = ?, confidence = ?, flags = ?, run_id = ? "
            "WHERE id = ?",
            (tr.intent, tr.draft, tr.sentiment, tr.risk, int(tr.requires_human), tr.reason,
             tr.confidence, json.dumps(t.flags), run_id, comment_id))
    ledger.append(run_id, agent=AGENT, kind="tool_call",
                  output_sha256=sha256_hex(tr.draft),
                  details={"tool": "draft", "status": "ok", "side_effects": "local",
                           "intent": tr.intent, "risk": tr.risk,
                           "requires_human": tr.requires_human, "flags": t.flags,
                           "model_receipt": t.result.receipt_id if t.result else ""})


class InboxError(ValueError):
    pass


def _comment(conn: sqlite3.Connection, comment_id: str) -> dict[str, Any]:
    with conn.lock:  # type: ignore[attr-defined]  # StudioConnection
        row = conn.execute("SELECT id, external_id, status FROM comments WHERE id = ?",
                           (comment_id,)).fetchone()
    if row is None:
        raise InboxError("no such comment")
    return {"id": row[0], "external_id": row[1], "status": row[2]}


def reply_payload(comment: dict[str, Any], message: str) -> dict[str, Any]:
    return {"action": "reply.post", "comment_id": comment["id"],
            "external_id": comment["external_id"], "message": message,
            "run_id": run_id_for(comment["id"])}


def hide_payload(comment: dict[str, Any]) -> dict[str, Any]:
    return {"action": "comment.hide", "comment_id": comment["id"],
            "external_id": comment["external_id"], "run_id": run_id_for(comment["id"])}


def request(conn: sqlite3.Connection, ledger: Ledger, comment_id: str, action: str,
            message: str = "") -> tuple[Pending, dict[str, Any]]:
    """A pending ``reply.post`` (for the exact, human-edited ``message``) or
    ``comment.hide``. Nothing is sent: the human signs it in the inbox."""
    if action not in SIGNED_ACTIONS:
        raise InboxError("unknown inbox action")
    comment = _comment(conn, comment_id)
    if comment["status"] in ("replied", "hidden"):
        raise InboxError(f"comment already {comment['status']}")
    if action == "reply.post":
        text = envelope.clean(message, max_chars=10_000).strip()
        if not text or len(text) > MAX_REPLY:
            raise InboxError(f"reply must be 1-{MAX_REPLY} characters")
        payload = reply_payload(comment, text)
    else:
        payload = hide_payload(comment)
    run_id = run_id_for(comment_id)
    pending = Pending.create(action, item_id_for(comment_id), payload, ledger=ledger,
                             run_id=run_id)
    with transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (pending.id, run_id, canonical(payload).decode(), time.time()))
        conn.execute("UPDATE comments SET pending_id = ? WHERE id = ?", (pending.id, comment_id))
    return pending, payload


def simulate(conn: sqlite3.Connection, text: str, author: str = "Simulated visitor",
             post_id: str = "post-demo-1") -> str:
    """Insert a comment as if it had arrived under the ad (sandbox / demo)."""
    text = text.strip()
    if not text or len(text) > MAX_COMMENT:
        raise InboxError(f"comment must be 1-{MAX_COMMENT} characters")
    comment_id = "sim-" + secrets.token_hex(6)
    with transaction(conn):
        conn.execute(
            "INSERT INTO comments (id, post_id, author_name, text, created_ts, status, source) "
            "VALUES (?, ?, ?, ?, ?, 'new', 'simulated')",
            (comment_id, post_id, envelope.clean(author, max_chars=60) or "Simulated visitor",
             text, time.time()))
    return comment_id


def ingest(conn: sqlite3.Connection, external_id: str, text: str, author: str,
           created_ts: float, post_id: str) -> str | None:
    """Insert a comment fetched from the Page (deduplicated by its Graph id)."""
    comment_id = "fb-" + hashlib.sha256(external_id.encode()).hexdigest()[:16]
    with transaction(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO comments (id, post_id, author_name, text, created_ts, "
            "status, source, external_id) VALUES (?, ?, ?, ?, ?, 'new', 'graph', ?)",
            (comment_id, post_id[:80], envelope.clean(author, max_chars=60), text[:MAX_COMMENT],
             created_ts, external_id))
    return comment_id if cur.rowcount == 1 else None


def send(conn: sqlite3.Connection, ledger: Ledger, adapter: Any, verified: Any,
         payload: dict[str, Any]) -> dict[str, Any]:
    """Executor for ``reply.post`` / ``comment.hide``: only after ``executor.authorize``."""
    from studio import executor

    action = getattr(verified, "action", "")
    if action not in SIGNED_ACTIONS:
        raise executor.ExecutionRefused("not an inbox action", "wrong_action")
    executor.authorize(conn, verified, action, payload)
    try:
        if action == "reply.post":
            reply_id = adapter.reply(payload, verified)
            result = {"adapter": adapter.name, "reply_id": executor.safe_id(reply_id)}
            status = "replied"
        else:
            adapter.hide(payload, verified)
            result = {"adapter": adapter.name}
            status = "hidden"
    except Exception as exc:
        executor.finish(conn, verified, "failed", {"error": type(exc).__name__})
        executor.record_step(ledger, verified, agent=AGENT, adapter=adapter.name,
                             step=action, status="error",
                             details={"error": getattr(exc, "code", type(exc).__name__)})
        raise
    executor.record_write(ledger, verified, agent=AGENT, adapter=adapter.name,
                          details=result)
    executor.finish(conn, verified, "done", result)
    with transaction(conn):
        conn.execute("UPDATE comments SET status = ? WHERE id = ?",
                     (status, payload["comment_id"]))
    return result


def poll(conn: sqlite3.Connection, adapter: Any, object_id: str, since: float = 0.0
         ) -> list[str]:
    """Fetch new comments under one Page post / ad post into the inbox (Graph poller)."""
    new = []
    for c in adapter.list_comments(object_id, since):
        cid = ingest(conn, c.external_id, c.message, c.author, c.created_ts, c.object_id)
        if cid:
            new.append(cid)
    return new
