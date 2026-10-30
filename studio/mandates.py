"""A signed mandate: the owner's assistant creates PAUSED campaigns within signed limits.

Every external write in the studio waits for a human signature. A mandate is the one
narrow exception, the ad-buying counterpart of an AP2 "intent mandate": the owner signs a
set of limits once (``mandate.grant``, the same WebCrypto Ed25519 approval as every other
signature), and within them a run started by one of the owner's listed API tokens creates
its campaign **PAUSED** without a new tap.

- **Only ``campaign.create``, only PAUSED.** A mandate's scope is the closed list
  ``["campaign.create"]``; activation, budget or status changes, replies, Google Ads and
  everything else still need the owner's own signature. ``executor.authorize`` accepts a
  mandate approval for ``campaign.create`` only, and ``check_paused`` still runs first.
- **Only within the signed limits** (``fits``): the brief's objective, every targeted
  country, the host of every URL the ad sends people to, the daily budget, the currency,
  the number of campaigns made under it, its expiry, revocation and the calling token.
  Anything outside them leaves the run waiting for the owner exactly as before, and the
  reasons go back to the assistant.
- **Only for the owner's own tokens, never for limited accounts on a live adapter.** The
  tokens must belong to the mandate's account and be live; the shared judge account and
  trial accounts cannot grant or use one while the Meta adapter is real
  (``clip.may_add``: one rule).
- **Provable.** The ``mandate.grant`` approval receipt carries the signed body, a
  ``mandate.revoke`` receipt marks a revocation, and every campaign made under a mandate
  has an ``approval`` receipt with ``by_mandate``, ``mandate_sha256`` and the values
  ``checked``. ``studio verify`` checks all of it (``receipts.verify``).
- **Never through a token.** No API or MCP operation creates, signs or revokes a mandate:
  those are ``/account`` forms (session + CSRF) and the studio's signing screen.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from studio import clip
from studio.approvals import (
    TTL_S,
    NonceUsed,
    Pending,
    Verified,
    confirmed_brief,
    payload_sha256,
    transaction,
)
from studio.meta import objectives, pixel
from studio.meta.adapter import NotApproved, PausedOnly, check_paused, delivery_of
from studio.meta.currency import from_minor, to_minor
from studio.receipts import (
    HOST,
    MANDATE_ACTIONS,
    MANDATE_APPROVER,
    MANDATE_FIELD,
    MANDATE_GRANT,
    MANDATE_REVOKE,
    Ledger,
    canonical,
    is_opaque,
    mandate_body_problem,
)

GRANT = MANDATE_GRANT
SCOPE = sorted(MANDATE_ACTIONS)  # ["campaign.create"]: never activate, budget or reply
MAX_CAMPAIGNS = 20
MAX_DAYS = 30
MAX_COUNTRIES = 50
MAX_HOSTS = 10
MAX_TOKENS = 10
DAY_S = 86400
# the creative kinds whose every destination code can see (``adapter._link``): app
# installs, events and catalogs point elsewhere and never fit a mandate
CREATIVES = frozenset({"link", "message", "lead_form"})
_URL = re.compile(r"(?i)\bhttps?://[^\s\"'<>]+")
_COUNTRY = re.compile(r"[A-Z]{2}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS mandates (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    body TEXT NOT NULL,                  -- canonical JSON of the signed limits
    sha256 TEXT NOT NULL,                -- content_sha256 of the signed mandate.grant
    approval_id TEXT NOT NULL UNIQUE,    -- the owner's signed mandate.grant approval
    created_ts REAL NOT NULL,
    revoked_ts REAL,
    used INTEGER NOT NULL DEFAULT 0      -- campaigns created under it
);
CREATE INDEX IF NOT EXISTS mandates_account ON mandates (account_id);
-- one row per campaign.create approved by a mandate: which token's run it was
CREATE TABLE IF NOT EXISTS mandate_uses (
    approval_id TEXT PRIMARY KEY,
    mandate_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL
);
"""


class MandateError(Exception):
    def __init__(self, code: str, status: int = 400, fields: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.fields = fields or []


@dataclass(frozen=True)
class Mandate:
    id: str
    account_id: str
    body: dict[str, Any]
    sha256: str
    approval_id: str
    created_ts: float
    revoked_ts: float | None
    used: int

    def live(self, now: float | None = None) -> bool:
        clock = time.time() if now is None else now
        return (self.revoked_ts is None and clock < self.body["valid_until"]
                and self.used < self.body["max_campaigns"])

    def status(self, now: float | None = None) -> str:
        clock = time.time() if now is None else now
        if self.revoked_ts is not None:
            return "revoked"
        if clock >= self.body["valid_until"]:
            return "expired"
        if self.used >= self.body["max_campaigns"]:
            return "used_up"
        return "active"

    def view(self, token_id: str = "", now: float | None = None) -> dict[str, Any]:
        """What ``/account`` and ``GET /api/v1/mandates`` show."""
        return {"mandate_id": self.id, "limits": dict(self.body), "sha256": self.sha256,
                "used": self.used, "created_ts": self.created_ts,
                "revoked_ts": self.revoked_ts, "status": self.status(now),
                **({"lists_this_token": token_id in self.body["token_ids"]}
                   if token_id else {})}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def may_grant(account: Any, meta_adapter: str) -> bool:
    """Whether ``account`` may grant or use a mandate: on a live Meta adapter a full
    account only, never the shared judge account or a trial account (``clip.may_add``)."""
    return clip.may_add(account, meta_adapter)


def payload_of(body: Mapping[str, Any]) -> dict[str, Any]:
    """The exact content the owner signs (``content_sha256`` of the grant)."""
    return {"action": MANDATE_GRANT, MANDATE_FIELD: dict(body)}


def new_id() -> str:
    while True:
        candidate = "mdt_" + secrets.token_hex(8)
        if is_opaque(candidate):
            return candidate


# -- tokens ------------------------------------------------------------------------------


def token_account(conn: Any, token_id: str, now: float) -> str | None:
    """The account of a live token (not revoked or expired, its account active, not the
    demo account, credentials unchanged since it was made), else ``None``. The same
    conditions as ``api_tokens.Tokens.verify`` without the secret."""
    row = conn.execute(
        "SELECT t.account_id, t.revoked_ts, t.expires_ts, t.credential_gen, a.credential_gen, "
        "a.status, a.is_demo FROM api_tokens t JOIN accounts a ON a.id = t.account_id "
        "WHERE t.id = ?", (token_id,)).fetchone()
    if row is None:
        return None
    aid, revoked, expires, gen, account_gen, status, demo = row
    if revoked is not None or expires <= now or gen != account_gen or status != "active" \
            or demo:
        return None
    return str(aid)


def run_account(conn: Any, run_id: str) -> str | None:
    """The account whose token confirmed the brief this run was started from (the
    assistants API tags every brief it drafts or confirms), else ``None``."""
    row = conn.execute("SELECT a.account_id FROM briefs b JOIN api_briefs a ON "
                       "a.brief_id = b.id WHERE b.run_id = ?", (run_id,)).fetchone()
    return str(row[0]) if row else None


# -- the body ----------------------------------------------------------------------------


def _clean(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[\s,;]+", values)
    if not isinstance(values, list | tuple):
        return []
    return sorted({str(v).strip() for v in values if str(v).strip()})


def build(account_id: str, *, token_ids: Any, objectives_: Any, countries: Any,
          landing_hosts: Any, max_daily_budget: Any, currency: str, max_campaigns: Any,
          days: Any, now: float | None = None) -> dict[str, Any]:
    """A mandate body from form values (normalised: sorted, upper-case countries,
    lower-case hosts); ``check`` decides whether it can be signed."""
    clock = time.time() if now is None else now

    def num(value: Any) -> Any:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return value

    d = num(days)
    return {
        "scope": list(SCOPE),
        "account_id": account_id,
        "token_ids": _clean(token_ids),
        "objectives": _clean(objectives_),
        "countries": sorted({c.upper() for c in _clean(countries)}),
        "landing_hosts": sorted({h.lower().rstrip(".") for h in _clean(landing_hosts)}),
        "max_daily_budget": num(max_daily_budget),
        "currency": currency,
        "max_campaigns": num(max_campaigns),
        "valid_until": int(clock) + d * DAY_S if isinstance(d, int) and 1 <= d <= MAX_DAYS
        else 0,
    }


def check(conn: Any, body: Mapping[str, Any], *, account_id: str, ceiling: int,
          currency: str, now: float | None = None) -> list[str]:
    """Every reason ``body`` cannot be signed (field names); empty = it can."""
    clock = time.time() if now is None else now
    problems: list[str] = []
    shape = mandate_body_problem(dict(body))
    if shape is not None:
        return [shape]
    if body["account_id"] != account_id:
        problems.append("account_id")
    if any(g not in objectives.GOALS for g in body["objectives"]):
        problems.append("objectives")
    if len(body["countries"]) > MAX_COUNTRIES or not all(
            _COUNTRY.fullmatch(c) for c in body["countries"]):
        problems.append("countries")
    if len(body["landing_hosts"]) > MAX_HOSTS or not all(
            HOST.fullmatch(h) for h in body["landing_hosts"]):
        problems.append("landing_hosts")
    if not 1 <= body["max_daily_budget"] <= ceiling:
        problems.append("max_daily_budget")
    if body["currency"] != currency:
        problems.append("currency")
    if not 1 <= body["max_campaigns"] <= MAX_CAMPAIGNS:
        problems.append("max_campaigns")
    if not clock < body["valid_until"] <= clock + MAX_DAYS * DAY_S:
        problems.append("valid_until")
    if len(body["token_ids"]) > MAX_TOKENS or any(
            token_account(conn, t, clock) != account_id for t in body["token_ids"]):
        problems.append("token_ids")
    return problems


def request(conn: Any, ledger: Ledger, account: Any, body: Mapping[str, Any], *,
            ceiling: int, currency: str, meta_adapter: str,
            now: float | None = None) -> Pending:
    """A pending ``mandate.grant`` for the owner to sign on the studio's signing screen.
    ``MandateError`` (``mandate_not_allowed``, ``mandate_invalid`` with the fields in
    ``args``) before anything is stored."""
    clock = time.time() if now is None else now
    if account is None or not may_grant(account, meta_adapter):
        raise MandateError("mandate_not_allowed", 403)
    with conn.lock:
        problems = check(conn, body, account_id=account.id, ceiling=ceiling,
                         currency=currency, now=clock)
    if problems:
        raise MandateError("mandate_invalid", 422, problems)
    payload = payload_of(body)
    mid = new_id()
    pending = Pending.create(MANDATE_GRANT, mid, payload, ledger=ledger, run_id=mid,
                             now=clock)
    with transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (pending.id, mid, canonical(payload).decode(), clock))
    return pending


def signer_may_sign(conn: Any, approval_id: str, account_id: str | None) -> bool:
    """Only the mandate's own account signs it (checked by the signing route)."""
    row = conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                       (approval_id,)).fetchone()
    if row is None or not account_id:
        return False
    body = json.loads(row[0]).get(MANDATE_FIELD)
    return isinstance(body, dict) and body.get("account_id") == account_id


def activate(conn: Any, verified: Any, payload: Mapping[str, Any],
             now: float | None = None) -> dict[str, Any]:
    """Store a mandate once its ``mandate.grant`` approval is signed and consumed (the
    executor of that action). Idempotent; ``MandateError`` when the approval is not a
    signed grant of exactly this body."""
    clock = time.time() if now is None else now
    if not isinstance(verified, Verified) or verified.action != MANDATE_GRANT:
        raise MandateError("mandate_unsigned", 403)
    body = payload.get(MANDATE_FIELD) if isinstance(payload, Mapping) else None
    if not isinstance(body, dict) or mandate_body_problem(body) is not None:
        raise MandateError("mandate_invalid", 422)
    sha = payload_sha256(dict(payload))
    with transaction(conn):
        row = conn.execute("SELECT status, action, item_id, content_sha256 FROM approvals "
                           "WHERE id = ?", (verified.pending_id,)).fetchone()
        if row is None or row[0] != "approved" or row[1] != MANDATE_GRANT \
                or row[2] != verified.item_id or row[3] != sha \
                or sha != verified.content_sha256:
            raise MandateError("mandate_unsigned", 403)
        if body["valid_until"] <= clock:
            raise MandateError("mandate_expired", 409)
        conn.execute("INSERT OR IGNORE INTO mandates (id, account_id, body, sha256, "
                     "approval_id, created_ts) VALUES (?, ?, ?, ?, ?, ?)",
                     (verified.item_id, body["account_id"], canonical(body).decode(), sha,
                      verified.pending_id, clock))
    return {"mandate_id": verified.item_id, "active": True, "adapter": "mandate"}


def _mandate(row: tuple[Any, ...]) -> Mandate:
    mid, aid, body, sha, approval, created, revoked, used = row
    return Mandate(mid, aid, json.loads(body), sha, approval, created, revoked, int(used))


_COLUMNS = "id, account_id, body, sha256, approval_id, created_ts, revoked_ts, used"


def get(conn: Any, mandate_id: str) -> Mandate | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM mandates WHERE id = ?",  # noqa: S608
                       (mandate_id,)).fetchone()
    return _mandate(row) if row else None


def of_account(conn: Any, account_id: str) -> list[Mandate]:
    with conn.lock:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM mandates WHERE account_id = ? "  # noqa: S608
                            "ORDER BY created_ts DESC LIMIT 50", (account_id,)).fetchall()
    return [_mandate(r) for r in rows]


def revoke(conn: Any, ledger: Ledger, account: Any, mandate_id: str,
           now: float | None = None) -> bool:
    """Revoke one of the account's own mandates and leave a ``mandate.revoke`` receipt in
    its run, in the same transaction; False for anyone else's, unknown or revoked."""
    clock = time.time() if now is None else now
    if account is None or not isinstance(mandate_id, str) or not is_opaque(mandate_id):
        return False
    with transaction(conn):
        m = get(conn, mandate_id)
        if m is None or m.account_id != account.id or m.revoked_ts is not None:
            return False
        conn.execute("UPDATE mandates SET revoked_ts = ? WHERE id = ?", (clock, m.id))
        ledger.append(m.id, agent="owner", kind=MANDATE_REVOKE, conn=conn,
                      output_sha256=m.sha256,
                      details={"mandate_id": m.id, "mandate_sha256": m.sha256})
    return True


# -- does a campaign fit? ------------------------------------------------------------------


def _countries(payload: Mapping[str, Any]) -> list[str] | None:
    """Every country the campaign reaches (targeting and lookalike), or ``None`` when its
    geography is anything but a plain country list."""
    tg = payload.get("targeting")
    geo = tg.get("geo_locations") if isinstance(tg, Mapping) else None
    if not isinstance(geo, Mapping) or set(geo) != {"countries"}:
        return None
    found = geo["countries"]
    if not isinstance(found, list) or not found or not all(isinstance(c, str) for c in found):
        return None
    out = set(found)
    look = payload.get("lookalike")
    if look is not None:
        spec = look.get("lookalike_spec") if isinstance(look, Mapping) else None
        country = spec.get("country") if isinstance(spec, Mapping) else None
        if not isinstance(country, str):
            return None
        out.add(country)
    return sorted(out)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def destinations(payload: Mapping[str, Any]) -> list[str] | None:
    """The host of every address the ad sends people to (the link, a lead form's privacy
    and follow-up pages) and of every other URL written anywhere in the payload; ``None``
    when one is not a plain ``https`` URL or the creative kind's destination is not
    visible to code (app, event, catalog)."""
    try:
        spec = delivery_of(payload)
    except NotApproved:
        return None
    kind = spec.get("creative")
    if kind not in CREATIVES:
        return None
    urls: list[Any] = [] if kind == "lead_form" else [payload.get("link")]
    form = payload.get("lead_form")
    if kind == "lead_form" or form is not None:
        form = form if isinstance(form, Mapping) else {}
        policy = form.get("privacy_policy")
        urls += [policy.get("url") if isinstance(policy, Mapping) else None,
                 form.get("follow_up_action_url")]
    scanned = {k: v for k, v in payload.items() if k != "pixel_check"}
    urls += [m.group(0) for s in _strings(scanned) for m in _URL.finditer(s)]
    hosts: set[str] = set()
    for url in urls:
        if not isinstance(url, str):
            return None
        try:
            parts = urlsplit(url.rstrip(".,;:!?)"))
            port = parts.port
        except ValueError:
            return None
        if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc \
                or port is not None:
            return None
        hosts.add(parts.hostname.lower().rstrip("."))
    return sorted(hosts)


def _budget(payload: Mapping[str, Any]) -> tuple[int, str] | None:
    camp = payload.get("campaign")
    if not isinstance(camp, Mapping):
        return None
    minor, currency = camp.get("daily_budget_minor"), camp.get("currency")
    if not isinstance(minor, int) or isinstance(minor, bool) or not isinstance(currency, str):
        return None
    return minor, currency


def fits(mandate: Mandate, payload: Mapping[str, Any], *, token_id: str = "",
         now: float | None = None) -> list[str]:
    """Every reason ``payload`` (a ``campaign.create``) does not fit ``mandate``; empty =
    it fits. Reasons: ``action``, ``revoked``, ``expired``, ``count``, ``token``,
    ``not_paused``, ``no_confirmed_brief``, ``objective``, ``countries``,
    ``landing_hosts``, ``currency``, ``daily_budget``, ``pixel_check``."""
    clock = time.time() if now is None else now
    body = mandate.body
    reasons: list[str] = []
    if not isinstance(payload, Mapping) or payload.get("action") not in MANDATE_ACTIONS:
        return ["action"]
    if mandate.revoked_ts is not None:
        reasons.append("revoked")
    if clock >= body["valid_until"]:
        reasons.append("expired")
    if mandate.used >= body["max_campaigns"]:
        reasons.append("count")
    if not token_id or token_id not in body["token_ids"]:
        reasons.append("token")
    try:
        check_paused(payload)
    except PausedOnly:
        reasons.append("not_paused")
    brief = payload.get("brief")
    goal = brief.get("goal") if isinstance(brief, Mapping) else None
    spec = payload.get("delivery")
    if not payload.get("brief_sha256") or not isinstance(goal, str):
        reasons.append("no_confirmed_brief")
    elif goal not in body["objectives"] or not isinstance(spec, Mapping) \
            or spec.get("goal") != goal:
        reasons.append("objective")
    countries = _countries(payload)
    if countries is None or not set(countries) <= set(body["countries"]):
        reasons.append("countries")
    hosts = destinations(payload)
    if not hosts or not set(hosts) <= set(body["landing_hosts"]):
        reasons.append("landing_hosts")
    budget = _budget(payload)
    if budget is None or budget[1] != body["currency"]:
        reasons.append("currency")
    elif budget[0] > to_minor(body["max_daily_budget"], body["currency"]):
        reasons.append("daily_budget")
    if pixel.gate(payload):
        reasons.append("pixel_check")
    return reasons


def checked(mandate: Mandate, payload: Mapping[str, Any], n: int) -> dict[str, Any]:
    """The values compared with the limits, as the approval receipt records them."""
    budget = _budget(payload)
    brief = payload.get("brief")
    return {"objective": brief.get("goal") if isinstance(brief, Mapping) else "",
            "countries": _countries(payload) or [], "hosts": destinations(payload) or [],
            "daily_budget": from_minor(budget[0], budget[1]) if budget else None,
            "currency": budget[1] if budget else "",
            "n_of_max": [n, mandate.body["max_campaigns"]]}


def candidates(conn: Any, account_id: str, token_id: str) -> list[Mandate]:
    """The account's stored mandates that list ``token_id``, oldest first."""
    rows = conn.execute(f"SELECT {_COLUMNS} FROM mandates WHERE account_id = ? "  # noqa: S608
                        "ORDER BY created_ts, id", (account_id,)).fetchall()
    out = [_mandate(r) for r in rows]
    return [m for m in out if token_id in m.body.get("token_ids", [])]


def _signed_grant(conn: Any, m: Mandate) -> bool:
    """The stored mandate is the body its owner signed: the approval is an approved
    ``mandate.grant`` of this id whose content is exactly this body."""
    row = conn.execute("SELECT status, action, item_id, content_sha256 FROM approvals "
                       "WHERE id = ?", (m.approval_id,)).fetchone()
    return (row is not None and row[0] == "approved" and row[1] == MANDATE_GRANT
            and row[2] == m.id and row[3] == m.sha256
            and payload_sha256(payload_of(m.body)) == m.sha256)


class _NoFit(Exception):
    def __init__(self, reasons: list[str], mandate_id: str = "") -> None:
        super().__init__(",".join(reasons))
        self.reasons = reasons
        self.mandate_id = mandate_id


def _choose(conn: Any, account_id: str, token_id: str, payload: Mapping[str, Any],
            clock: float) -> tuple[Mandate, list[str]]:
    """The first of the token's mandates the payload fits, or the best refusal."""
    found = [m for m in candidates(conn, account_id, token_id) if _signed_grant(conn, m)]
    if not found:
        raise _NoFit(["no_mandate"])
    best: tuple[Mandate, list[str]] | None = None
    for m in found:
        reasons = fits(m, payload, token_id=token_id, now=clock)
        if not reasons:
            return m, []
        if best is None or len(reasons) < len(best[1]):
            best = (m, reasons)
    assert best is not None
    raise _NoFit(best[1], best[0].id)


def execute(conn: Any, ledger: Ledger, pending_id: str, token_id: str, *, account: Any,
            meta_adapter: str, executor: Callable[[Verified, dict[str, Any]], Any] | None,
            now: float | None = None) -> dict[str, Any]:
    """Approve a run's pending ``campaign.create`` under a mandate and run its executor.

    The decision for the assistant: ``{"fits": False, "reasons": [...]}`` (nothing
    changed: the run waits for the owner), or ``{"fits": True, "mandate_id", "executed",
    ...}``. Fits are checked again inside the write transaction that consumes the
    approval's nonce, marks it approved by ``mandate:<id>``, counts the use and writes the
    ``approval`` receipt; the executor (the media buyer, PAUSED only) runs after commit.
    """
    clock = time.time() if now is None else now
    if not may_grant(account, meta_adapter):
        return {"fits": False, "reasons": ["account_not_allowed"]}
    try:
        with transaction(conn):
            pending = Pending.load(conn, pending_id)
            if pending.action not in MANDATE_ACTIONS:
                raise _NoFit(["action"])
            status, superseded = conn.execute(
                "SELECT status, superseded_by FROM approvals WHERE id = ?",
                (pending.id,)).fetchone()
            if status != "pending" or superseded:
                raise _NoFit(["not_pending"])
            row = conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                               (pending.id,)).fetchone()
            if row is None:
                raise _NoFit(["not_pending"])
            payload = json.loads(row[0])
            if payload_sha256(payload) != pending.content_sha256:
                raise _NoFit(["content_changed"])
            if pending.brief_sha256 and confirmed_brief(ledger, pending.run_id) != \
                    pending.brief_sha256:
                raise _NoFit(["brief_changed"])
            if clock - pending.ts > TTL_S:
                raise _NoFit(["approval_expired"])
            if token_account(conn, token_id, clock) != account.id:
                raise _NoFit(["token"])
            if run_account(conn, pending.run_id) != account.id:
                raise _NoFit(["run_not_yours"])
            m, _ = _choose(conn, account.id, token_id, payload, clock)
            n = m.used + 1
            approver = MANDATE_APPROVER + m.id
            try:
                conn.execute("INSERT INTO nonces (nonce, consumed_ts) VALUES (?, ?)",
                             (pending.nonce, int(clock)))
            except sqlite3.IntegrityError:
                raise NonceUsed("approval was already used") from None
            conn.execute("UPDATE approvals SET status = 'approved', approver = ?, "
                         "signature = ?, decided_ts = ? WHERE id = ? AND status = 'pending'",
                         (approver, m.sha256, int(clock), pending.id))
            cur = conn.execute("UPDATE mandates SET used = used + 1 WHERE id = ? AND "
                               "used < ? AND revoked_ts IS NULL",
                               (m.id, m.body["max_campaigns"]))
            if cur.rowcount != 1:
                raise _NoFit(["count"], m.id)
            conn.execute("INSERT INTO mandate_uses (approval_id, mandate_id, token_id, "
                         "run_id, ts) VALUES (?, ?, ?, ?, ?)",
                         (pending.id, m.id, token_id, pending.run_id, clock))
            seen = checked(m, payload, n)
            ledger.append(pending.run_id, agent="mandate", kind="approval", conn=conn,
                          output_sha256=pending.content_sha256,
                          details={**pending.fields(approver), "pending_id": pending.id,
                                   "by_mandate": m.id, "mandate_sha256": m.sha256,
                                   "checked": seen})
            conn.execute("UPDATE run_state SET status = 'approved', updated_ts = ? "
                         "WHERE run_id = ?", (clock, pending.run_id))
    except _NoFit as exc:
        return {"fits": False, "reasons": exc.reasons,
                **({"mandate_id": exc.mandate_id} if exc.mandate_id else {})}
    except NonceUsed:
        return {"fits": False, "reasons": ["not_pending"]}
    verified = Verified(pending_id=pending.id, run_id=pending.run_id, action=pending.action,
                        item_id=pending.item_id, content_sha256=pending.content_sha256,
                        approver=approver, signature=m.sha256,
                        brief_sha256=pending.brief_sha256, spec_sha256=pending.spec_sha256)
    out: dict[str, Any] = {"fits": True, "mandate_id": m.id, "approval_id": pending.id,
                           "checked": seen, "executed": False}
    if executor is None:
        return out
    try:
        result = executor(verified, payload)
        out.update(executed=True, result=result)
    except Exception as exc:  # noqa: BLE001 - reported; the approval stays consumed
        out["execution_error"] = getattr(exc, "code", "") or type(exc).__name__
    return out


def execution_problem(conn: Any, verified: Verified, payload: Mapping[str, Any],
                      now: float | None = None) -> tuple[str, str] | None:
    """``executor.authorize``'s check of an approval made by a mandate (inside its
    transaction): ``(code, message)`` when it must be refused, ``None`` to proceed. Only
    ``campaign.create``; a stored, signed, unrevoked, unexpired mandate whose hash is the
    approval's; a recorded use by a token the mandate lists; and the payload still fits
    its limits."""
    clock = time.time() if now is None else now
    if verified.action not in MANDATE_ACTIONS:
        return "mandate_wrong_action", "a mandate approves campaign.create only"
    mid = verified.approver[len(MANDATE_APPROVER):]
    m = get(conn, mid)
    if m is None:
        return "mandate_unknown", "no stored mandate for this approval"
    if m.sha256 != verified.signature or not _signed_grant(conn, m):
        return "mandate_tampered", "the mandate is not the one its owner signed"
    if m.revoked_ts is not None:
        return "mandate_revoked", "the mandate was revoked"
    if clock >= m.body["valid_until"]:
        return "mandate_expired", "the mandate expired"
    use = conn.execute("SELECT mandate_id, token_id FROM mandate_uses WHERE approval_id = ?",
                       (verified.pending_id,)).fetchone()
    if use is None or use[0] != m.id or use[1] not in m.body["token_ids"]:
        return "mandate_unused", "no recorded use of the mandate for this approval"
    # the use is counted already: check the limits as they were before it
    before = Mandate(m.id, m.account_id, m.body, m.sha256, m.approval_id, m.created_ts,
                     m.revoked_ts, m.used - 1)
    reasons = fits(before, payload, token_id=use[1], now=clock)
    if reasons:
        return "mandate_limits", "outside the mandate: " + ", ".join(reasons)
    return None
