"""Conversions API (CAPI): the leads the studio already receives, sent to the owner's
dataset as ``Lead`` events from the server. Off unless ``STUDIO_CAPI=1`` *and* the account
that signed the campaign has recorded its consent (Account → Profile); nothing changes
for anyone else.

- What is sent: ``POST /{dataset_id}/events`` (``Adapter.send_events``), one event per new
  instant-form lead (``leadads.ingest``), in the shape of Meta's CRM integration for lead
  ads: ``event_name=Lead``, ``action_source=system_generated``, ``custom_data.event_source
  =crm`` and ``lead_event_source``, ``user_data.lead_id`` (Meta's own lead id) and the
  e-mail and phone as SHA-256 of their normalised form (``em``, ``ph``): e-mail trimmed
  and lower-cased; phone digits only, with the country code, no leading zeros. A phone
  written without its country code is left out rather than guessed. Nothing else about
  the person (name, city, company) is sent. ``check_events`` refuses any unhashed or
  unknown ``user_data`` field in both adapters, before any call.
- ``event_id`` is ``muse-lead-<lead id>``, fixed per lead, so Meta drops a duplicate of the
  same event (a resend, or a Pixel/website event that carries the same id) within its
  deduplication window.
- ``test_event_code`` (``STUDIO_CAPI_TEST_EVENT_CODE``) sends every event to Events
  Manager → Test events instead of counting it.
- Who decides: the account that signed the campaign's ``campaign.create`` (approval →
  approver key → session → account). It must be an own account (active, full tier, not
  the shared demo), have a dataset id saved (the profile's "Dataset ID (Meta Pixel)") and
  its consent recorded (``accounts.capi_consent_ts``). Otherwise the lead is not sent.
- Receipts: one ``capi.send`` per lead, in the form's run: ``ok`` / ``error`` (the write
  may have reached Meta) / ``skipped`` with the reason. The receipt export is public, so it
  names the lead by its internal id and carries the dataset id, the request and the
  ``user_data`` only as SHA-256 digests of the whole (``dataset_sha256``,
  ``output_sha256``, ``user_data_sha256``) and the field names sent (``fields``): never an
  address, a number, nor the per-field hash Meta receives.
- A failed send is not retried (a write is never sent twice, ``graph.py``); the receipt
  says so and the lead stays in the store.

Sources (read 2026-11-02, developers.facebook.com/documentation/ads-commerce/
conversions-api/...): ``conversion-leads-integration/payload-specification.md``
(``action_source=system_generated``, ``event_source=crm``, ``lead_event_source``,
``lead_id`` as a number, ``event_time`` at most 7 days old and later than the lead);
``parameters/customer-information-parameters.md`` (normalisation, SHA-256);
``deduplicate-pixel-and-server-events.md`` (``event_id``); ``verifying-setup.md`` (test
events).
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from studio.meta import pixel
from studio.meta.adapter import AdapterError
from studio.receipts import Ledger, canonical, sha256_hex

AGENT: Final = "capi"
EVENT_NAME: Final = "Lead"
LEAD_EVENT_SOURCE: Final = "Muse Studio Lite"
# the lead's source → Meta's ``action_source``: instant forms are Meta's own leads, sent
# back from our CRM (Meta's CRM integration names this ``system_generated``)
ACTION_SOURCES: Final = {"lead_form": "system_generated"}
HASHED: Final = frozenset({"em", "ph"})
USER_DATA_KEYS: Final = HASHED | {"lead_id"}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")
_LEAD_ID = re.compile(r"\d{1,19}")
PHONE_DIGITS: Final = (8, 15)  # E.164: at most 15 digits with the country code


def normalize_email(value: str) -> str:
    """Trimmed and lower-cased; ``""`` for anything that is not an address."""
    email = str(value or "").strip().lower()
    return email if _EMAIL.fullmatch(email) else ""


def normalize_phone(value: str) -> str:
    """Digits only, with the country code and no leading zeros (``+48 600-123-456`` →
    ``48600123456``); ``""`` when the number has no country code (``+`` or ``00``) or is
    not a phone number."""
    raw = str(value or "").strip()
    if not raw.startswith(("+", "00")) or re.search(r"[^\d\s+().\-/]", raw):
        return ""
    digits = re.sub(r"\D", "", raw).lstrip("0")
    return digits if PHONE_DIGITS[0] <= len(digits) <= PHONE_DIGITS[1] else ""


def hashed(value: str) -> str:
    return sha256_hex(value)


def user_data(row: Mapping[str, str], external_id: str) -> dict[str, Any]:
    """``em`` / ``ph`` as SHA-256 of the normalised values, ``lead_id`` when the lead has
    Meta's numeric id; empty values are left out."""
    out: dict[str, Any] = {}
    email = normalize_email(row.get("email", ""))
    phone = normalize_phone(row.get("phone", ""))
    if email:
        out["em"] = [hashed(email)]
    if phone:
        out["ph"] = [hashed(phone)]
    if _LEAD_ID.fullmatch(external_id or ""):
        out["lead_id"] = int(external_id)
    return out


def event_id(lead_id: str) -> str:
    return f"muse-lead-{lead_id}"


def lead_event(lead_id: str, row: Mapping[str, str], external_id: str, event_time: float,
               *, source: str = "lead_form") -> dict[str, Any]:
    """One ``Lead`` event for a stored lead (``lead_id`` is the studio's own id).
    ``event_time`` is when the studio received it (Meta: later than the lead was made,
    at most 7 days old)."""
    return {"event_name": EVENT_NAME, "event_time": int(event_time),
            "event_id": event_id(lead_id), "action_source": ACTION_SOURCES[source],
            "user_data": user_data(row, external_id),
            "custom_data": {"event_source": "crm", "lead_event_source": LEAD_EVENT_SOURCE}}


def check_events(events: Sequence[Mapping[str, Any]]) -> None:
    """Every ``user_data`` field is a known one and every contact value a SHA-256 hex
    digest; ``AdapterError(code="unhashed")`` otherwise (checked before any call)."""
    for event in events:
        data = event.get("user_data") if isinstance(event, Mapping) else None
        if not isinstance(data, Mapping) or set(data) - USER_DATA_KEYS:
            raise AdapterError("CAPI user data has unknown fields", code="unhashed")
        for key in HASHED & set(data):
            values = data[key]
            if not isinstance(values, list) or not all(
                    isinstance(v, str) and _SHA256.fullmatch(v) for v in values):
                raise AdapterError("CAPI contact data must be SHA-256 hashes", code="unhashed")
        if "lead_id" in data and (isinstance(data["lead_id"], bool)
                                  or not isinstance(data["lead_id"], int)):
            raise AdapterError("CAPI lead_id must be Meta's number", code="unhashed")


def check_dataset(dataset_id: str) -> str:
    if not isinstance(dataset_id, str) or not pixel.PIXEL_ID.fullmatch(dataset_id):
        raise AdapterError("not a dataset id", code="bad_id")
    return dataset_id


def user_data_sha256(data: Mapping[str, Any]) -> str:
    return sha256_hex(json.dumps(data, sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True)
class Owner:
    """The account that signed a run's campaign, as far as CAPI cares."""

    account_id: str
    dataset_id: str
    consent_ts: float
    own: bool  # active, full tier, not the shared demo account


def owner_of(conn: sqlite3.Connection, form_id: str) -> Owner | None:
    """The account whose key signed the latest ``campaign.create`` approval of the run
    that created this instant form (``lead_forms.run_id``)."""
    with conn.lock:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT acc.id, acc.pixel_id, acc.capi_consent_ts, acc.status, acc.is_demo, "
            "acc.tier FROM lead_forms f JOIN approvals a ON a.run_id = f.run_id "
            "JOIN keys k ON k.approver = a.approver "
            "JOIN account_sessions s ON s.sid_sha256 = k.session_sha256 "
            "JOIN accounts acc ON acc.id = s.account_id "
            "WHERE f.form_id = ? AND a.action = 'campaign.create' AND a.status = 'approved' "
            "ORDER BY a.decided_ts DESC LIMIT 1", (form_id,)).fetchone()
    if row is None:
        return None
    aid, dataset, consent, status, demo, tier = row
    return Owner(str(aid), str(dataset or ""), float(consent or 0),
                 status == "active" and not demo and tier == "full")


def consent_receipt(ledger: Ledger, account_id: str, consent: bool) -> None:
    """The owner recorded (``consent: true``) or withdrew their customers' consent."""
    ledger.append("capi_" + sha256_hex(account_id)[:16], agent=AGENT, kind="tool_call",
                  details={"tool": "capi.consent", "status": "ok", "side_effects": "local",
                           "account": account_id, "consent": consent})


class Sender:
    """Sends one stored lead (``leadads.ingest`` calls it after storing it); never raises."""

    def __init__(self, conn: sqlite3.Connection, ledger: Ledger, adapter: Any,
                 test_event_code: str = "") -> None:
        self.conn = conn
        self.ledger = ledger
        self.adapter = adapter
        self.test_event_code = test_event_code
        self.clock = time.time

    def _receipt(self, run_id: str, details: dict[str, Any], **fields: Any) -> None:
        self.ledger.append(run_id, agent=AGENT, kind="tool_call", details={
            "tool": "capi.send", **details}, **fields)

    def __call__(self, lead_id: str, row: Mapping[str, str], external_id: str,
                 form_id: str) -> str:
        """``ok``, ``error`` or the reason it was skipped; the receipt goes to the form's
        run (``leadads.run_id_for``)."""
        from studio.leadads import run_id_for

        run_id = run_id_for(form_id)
        owner = owner_of(self.conn, form_id)
        reason = ("no_account" if owner is None or not owner.own
                  else "no_consent" if owner.consent_ts <= 0
                  else "no_dataset" if not pixel.PIXEL_ID.fullmatch(owner.dataset_id)
                  else "")
        event = lead_event(lead_id, row, external_id, self.clock())
        if not reason and not event["user_data"]:
            reason = "no_contact"
        if reason or owner is None:
            self._receipt(run_id, {"status": "skipped", "side_effects": "none",
                                   "lead": lead_id, "reason": reason or "no_account"})
            return reason or "no_account"
        common = {"lead": lead_id, "event_name": event["event_name"],
                  "event_id": event["event_id"], "action_source": event["action_source"],
                  "fields": sorted(event["user_data"]),
                  "user_data_sha256": user_data_sha256(event["user_data"]),
                  "dataset_sha256": sha256_hex(owner.dataset_id),
                  "consent_ts": owner.consent_ts, "test": bool(self.test_event_code),
                  "side_effects": "measurement"}
        request = sha256_hex(canonical({"data": [event],
                                        "test_event_code": self.test_event_code}))
        provider = f"meta-{getattr(self.adapter, 'name', 'unknown')}"
        try:
            out = self.adapter.send_events(owner.dataset_id, [event],
                                           test_event_code=self.test_event_code)
        except AdapterError as exc:
            self._receipt(run_id, {"status": "error", **common, "error": exc.code,
                                   "transient": bool(getattr(exc, "transient", False))},
                          provider=provider, output_sha256=request)
            return "error"
        self._receipt(run_id, {"status": "ok", **common,
                               "events_received": int(out.get("events_received", 0))},
                      provider=provider, output_sha256=request)
        return "ok"


def sender(conn: sqlite3.Connection, ledger: Ledger, adapter: Any,
           settings: Any) -> Sender | None:
    """The configured sender, or ``None`` (nothing sent, nothing recorded) unless
    ``STUDIO_CAPI=1``; also ``None`` under ``STUDIO_READ_ONLY`` (no external writes)."""
    if not getattr(settings, "capi", False) or getattr(settings, "read_only", False):
        return None
    return Sender(conn, ledger, adapter, getattr(settings, "capi_test_event_code", ""))
