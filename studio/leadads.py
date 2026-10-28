"""Lead ads: instant forms made through the API, their leads polled or pushed by webhook
into the lead store and the lead scorer.

- The form is built by code (``form_spec``) from the confirmed Brief v1: the questions
  the owner chose and the owner's own privacy-policy URL, which Meta requires; the studio
  never invents one and adds no notices of its own. It is part of the signed
  ``campaign.create`` payload (``lead_form``) and is created by the media buyer before the
  creative (``POST /{page_id}/leadgen_forms``).
- Leads come from ``GET /{form_id}/leads`` (``poll``) or from a ``leadgen`` webhook whose
  ``X-Hub-Signature-256`` verifies with the app secret (``verify_signature``), followed by
  ``GET /{lead_id}``. Only forms this studio created are accepted.
- ``ingest`` maps the form's fields to the lead scorer's rule inputs (name, e-mail, phone,
  city, company, country), stores the lead once per Meta lead id and scores it. Contact
  data stays in the ``leads`` table as for comment leads; receipts carry counts and opaque
  ids only, and the model rationale never sees the e-mail or phone (``lead_scorer``).

Sources (read 2026-10-28): lead form creation
https://developers.facebook.com/docs/graph-api/reference/page/leadgen_forms/ ; lead ads
https://developers.facebook.com/docs/marketing-api/guides/lead-ads/create ; retrieval
https://developers.facebook.com/docs/marketing-api/guides/lead-ads/retrieving ; webhooks
signature https://developers.facebook.com/docs/graph-api/webhooks/getting-started .
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from studio import envelope
from studio.approvals import transaction
from studio.receipts import Ledger, sha256_hex

AGENT = "lead_ads"
SCHEMA = """
CREATE TABLE IF NOT EXISTS lead_forms (
    form_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    adapter TEXT NOT NULL,
    created_ts REAL NOT NULL,
    polled_ts REAL NOT NULL DEFAULT 0
);
"""
# Meta's field_data names for the prefill questions → the lead scorer's rule inputs.
FIELD_MAP = {"full_name": "name", "first_name": "name", "email": "email",
             "phone_number": "phone", "city": "city", "company_name": "company",
             "country": "country"}
MAX_FIELD = 200


@dataclass(frozen=True)
class RemoteLead:
    external_id: str
    form_id: str
    created_ts: float
    fields: dict[str, str] = field(default_factory=dict)  # field_data name → first value
    ad_id: str = ""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The forms table, and one lead per Meta lead id (``leads.external_id``, a column the
    database migration adds)."""
    conn.executescript(SCHEMA)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS leads_external_once ON leads "
                 "(external_id) WHERE external_id != ''")


def link_text(url: str) -> str:
    """The privacy link's label: the owner's own address (host and path), no added words;
    Meta allows at most 70 characters."""
    parts = urlsplit(url)
    return (str(parts.hostname or "") + parts.path).rstrip("/")[:70] or url[:70]


def form_spec(name: str, questions: list[str], privacy_url: str,
              follow_up_url: str) -> dict[str, Any]:
    """The ``leadgen_forms`` body for the signed payload (``lead_form``)."""
    if not privacy_url:
        raise ValueError("an instant form needs the owner's privacy-policy URL")
    return {"name": name[:100],
            "questions": [{"type": q, "key": q.lower()} for q in questions],
            "privacy_policy": {"url": privacy_url, "link_text": link_text(privacy_url)},
            "follow_up_action_url": follow_up_url}


def run_id_for(form_id: str) -> str:
    return "lf_" + sha256_hex(form_id)[:16]


def record_form(conn: sqlite3.Connection, form_id: str, campaign_id: str, run_id: str,
                adapter: str) -> None:
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO lead_forms (form_id, campaign_id, run_id, adapter, "
                     "created_ts) VALUES (?, ?, ?, ?, ?)",
                     (form_id, campaign_id, run_id, adapter, time.time()))


def forms(conn: sqlite3.Connection, adapter: str) -> list[tuple[str, float]]:
    with conn.lock:  # type: ignore[attr-defined]
        return [(str(a), float(b)) for a, b in conn.execute(
            "SELECT form_id, polled_ts FROM lead_forms WHERE adapter = ? ORDER BY created_ts",
            (adapter,))]


def known_form(conn: sqlite3.Connection, form_id: str, adapter: str) -> bool:
    with conn.lock:  # type: ignore[attr-defined]
        return conn.execute("SELECT 1 FROM lead_forms WHERE form_id = ? AND adapter = ?",
                            (form_id, adapter)).fetchone() is not None


def lead_row(lead: RemoteLead) -> dict[str, str]:
    """The lead scorer's inputs from the form's answers (untrusted text, cleaned)."""
    out = {"name": "", "email": "", "phone": "", "city": "", "company": "", "country": ""}
    for key, value in lead.fields.items():
        target = FIELD_MAP.get(key.lower())
        if target and not out[target]:
            out[target] = envelope.clean(str(value), max_chars=MAX_FIELD)
    out["kind"] = "business" if out["company"] else ""
    return out


def ingest(conn: sqlite3.Connection, ledger: Ledger, client: Any, lead: RemoteLead,
           *, via: str) -> dict[str, Any] | None:
    """Store one Meta lead (once per lead id) and score it; ``None`` if already stored."""
    from studio.agents import lead_scorer

    row = lead_row(lead)
    lead_id = "l-" + secrets.token_hex(5)
    with transaction(conn):
        if conn.execute("SELECT 1 FROM leads WHERE external_id = ?",
                        (lead.external_id,)).fetchone():
            return None
        conn.execute(
            "INSERT INTO leads (id, name, email, phone, source, message, city, country, "
            "company, kind, created_ts, external_id, form_id) "
            "VALUES (?, ?, ?, ?, 'lead_form', '', ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, row["name"] or "Lead", row["email"], row["phone"], row["city"],
             row["country"], row["company"], row["kind"], lead.created_ts or time.time(),
             lead.external_id, lead.form_id))
    ledger.append(run_id_for(lead.form_id), agent=AGENT, kind="tool_call",
                  output_sha256=sha256_hex(lead.external_id),
                  details={"tool": "leadgen.ingest", "status": "ok", "side_effects": "local",
                           "via": via, "lead": lead_id,
                           "fields": sorted(k for k, v in row.items() if v and k != "kind")})
    try:
        return lead_scorer.score_lead(conn, ledger, client, lead_id)
    except Exception:  # noqa: BLE001 - the rules score never fails; the rationale may
        return lead_scorer.score_lead(conn, ledger, None, lead_id)


def poll(conn: sqlite3.Connection, ledger: Ledger, adapter: Any, client: Any = None,
         now: float | None = None) -> dict[str, int]:
    """``GET /{form_id}/leads`` for every form this adapter created; new leads are stored
    and scored. One ``leadgen.poll`` receipt per form (counts only)."""
    clock = time.time() if now is None else now
    total = {"forms": 0, "fetched": 0, "new": 0}
    for form_id, since in forms(conn, adapter.name):
        fetched = adapter.list_leads(form_id, since)
        new = sum(ingest(conn, ledger, client, lead, via="poll") is not None
                  for lead in fetched)
        with transaction(conn):
            conn.execute("UPDATE lead_forms SET polled_ts = ? WHERE form_id = ?",
                         (clock, form_id))
        ledger.append(run_id_for(form_id), agent=AGENT, kind="tool_call",
                      provider=f"meta-{adapter.name}",
                      details={"tool": "leadgen.poll", "status": "ok", "side_effects": "read",
                               "fetched": len(fetched), "new": new})
        total["forms"] += 1
        total["fetched"] += len(fetched)
        total["new"] += new
    return total


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """``X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body, keyed with the app
    secret>``; unsigned or wrongly signed requests are refused, and so is everything
    when no app secret is configured."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:].strip().lower())


def leadgen_changes(event: Any) -> list[dict[str, str]]:
    """``{leadgen_id, form_id, page_id}`` of every ``leadgen`` change in a Page webhook
    event; anything malformed is skipped."""
    out: list[dict[str, str]] = []
    if not isinstance(event, Mapping) or event.get("object") != "page":
        return out
    for entry in event.get("entry") or []:
        if not isinstance(entry, Mapping):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, Mapping) or change.get("field") != "leadgen":
                continue
            value = change.get("value")
            if isinstance(value, Mapping) and all(
                    isinstance(value.get(k), str | int) for k in ("leadgen_id", "form_id")):
                out.append({"leadgen_id": str(value["leadgen_id"]),
                            "form_id": str(value["form_id"]),
                            "page_id": str(value.get("page_id", ""))})
    return out[:50]
