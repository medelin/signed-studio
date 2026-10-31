"""Export and import of the studio's data: leads (CSV, JSON, vCard) and the monthly ad
statement (CSV in four profiles, hledger journal, JSON). Pure functions over a passed
connection; the routes (``studio.routes_export``, ``studio.routes_api``) do the I/O.

- **Masking**: ``leads_rows(conn, masked=True)`` is what a trial or demo account sees on
  ``/leads``: initials instead of the name, ``[email]`` / ``[phone]`` placeholders, every
  other string redacted. Every format is written from those rows, so no format can carry
  more than the page shows.
- **Statement**: one row per campaign per reporting day from the stored insights
  snapshots (``insights_snapshots``), each tied to the signed approval that set the daily
  budget in force on that day (approval id, short approver key id, the approval receipt's
  hash), with the model cost of the campaign's run (sum of ``cost_usd`` of its receipts in
  the month) on the run's first row, and one row per run that spent only model money.
  A snapshot covers ``date_start..date_stop`` and is booked on ``date_stop``; when two
  snapshots of a campaign overlap, the newer one wins and the older is never counted.
- **Import** (leads only): CSV with a header row, JSON (a list of objects) or vCard; at
  most ``MAX_IMPORT_BYTES`` and ``MAX_IMPORT_ROWS``; e-mail and phone normalised;
  duplicates (same e-mail or phone, in the studio or earlier in the file) skipped and
  counted; a row with neither an e-mail nor a phone is rejected with its line number.
  One ``tool_call`` receipt ``leads.import`` with counts only, never names or contacts.
  Nothing is uploaded anywhere.
"""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from studio import envelope
from studio.approvals import transaction
from studio.meta.currency import offset
from studio.receipts import Ledger, canonical, redact, sha256_hex

# -- leads ---------------------------------------------------------------------------

LEAD_COLUMNS = ("id", "name", "email", "phone", "source", "message", "company", "city",
                "country", "score", "temp", "created_at")
LEAD_FORMATS = ("csv", "json", "vcf")
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 5000
MAX_REPORTED_ERRORS = 50
IMPORT_RUN = "leads_import"
IMPORT_AGENT = "importer"
_LIMITS = {"name": 200, "email": 254, "phone": 32, "source": 40, "message": 2000}
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_SOURCE = re.compile(r"[^A-Za-z0-9_.-]+")
# Header names an import recognises (compared case-insensitively, spaces collapsed).
HEADERS: dict[str, tuple[str, ...]] = {
    "name": ("name", "full name", "full_name", "fullname", "imię i nazwisko",
             "imie i nazwisko", "fn"),
    "email": ("email", "e-mail", "e_mail", "mail", "adres e-mail"),
    "phone": ("phone", "telefon", "tel", "phone number", "numer telefonu", "mobile"),
    "source": ("source", "źródło", "zrodlo"),
    "message": ("message", "note", "notes", "notatka", "wiadomość", "wiadomosc"),
}


def _iso(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _initials(name: str) -> str:
    from studio.routes_inbox import initials  # the page's own rule, one definition

    return initials(name)


def leads_rows(conn: Any, *, masked: bool) -> list[dict[str, Any]]:
    """Every lead, newest first, as export rows (``LEAD_COLUMNS``). ``masked``: what a
    trial or demo account sees on ``/leads`` (initials, ``[email]``, ``[phone]``, every
    string redacted)."""
    with conn.lock:
        rows = conn.execute(
            "SELECT id, name, email, phone, source, message, company, city, country, score, "
            "temp, created_ts FROM leads ORDER BY created_ts DESC, id").fetchall()
    out = []
    for r in rows:
        lead: dict[str, Any] = dict(zip(LEAD_COLUMNS, r, strict=True))
        for key in ("name", "email", "phone", "source", "message", "company", "city",
                    "country", "temp"):
            lead[key] = envelope.clean(str(lead[key] or ""))
        lead["created_at"] = _iso(r[11])
        if masked:
            lead["name"] = _initials(lead["name"])
            lead["email"] = "[email]" if lead["email"] else ""
            lead["phone"] = "[phone]" if lead["phone"] else ""
            for key in ("name", "source", "message", "company", "city", "country", "temp"):
                lead[key] = redact(lead[key])  # ids and dates stay readable
        out.append(lead)
    return out


# -- writers ---------------------------------------------------------------------------

BOM = "\ufeff"
_QUOTE_IF = re.compile(r'[,;"\r\n\t]|^\s|\s$')
_FORMULA = re.compile(r"^[=@\t\r]|^[+-].*[^0-9 ().+-]")


def _cell(value: Any) -> str:
    """One CSV field: numbers as they are; a string that a spreadsheet would run as a
    formula gets a leading ``'`` (OWASP CSV injection); a field with a comma, semicolon,
    quote, line break or edge space is quoted (RFC 4180, safe for ``;`` locales too)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float):
        text = str(value)
    else:
        text = str(value)
        if _FORMULA.match(text):
            text = "'" + text
    if _QUOTE_IF.search(text):
        return '"' + text.replace('"', '""') + '"'
    return text


def to_csv(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """UTF-8 text with a BOM (spreadsheets then read Polish letters right), a header row,
    CRLF line ends (RFC 4180)."""
    lines = [",".join(_cell(c) for c in columns)]
    lines += [",".join(_cell(row.get(c)) for c in columns) for row in rows]
    return BOM + "\r\n".join(lines) + "\r\n"


def to_json(rows: Any) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _vescape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def _fold(line: str) -> list[str]:
    """RFC 6350 §3.2: lines over 75 octets continue on the next line after one space."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        if size + n > 75:
            out.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    out.append(cur)
    return out


def leads_to_vcf(rows: Iterable[Mapping[str, Any]]) -> str:
    """vCard 4.0 (RFC 6350): FN, EMAIL, TEL (``tel:`` URI), NOTE with source and score,
    then the message. A masked row has no EMAIL or TEL."""
    lines: list[str] = []
    for row in rows:
        props = ["BEGIN:VCARD", "VERSION:4.0", "FN:" + _vescape(str(row.get("name") or ""))]
        email = str(row.get("email") or "")
        if email and EMAIL.fullmatch(email):
            props.append("EMAIL:" + _vescape(email))
        phone = normalize_phone(str(row.get("phone") or ""))
        if phone:
            props.append("TEL;VALUE=uri:tel:" + phone)
        score = row.get("score")
        note = f"source: {row.get('source') or ''}"
        if score is not None and score != "":
            note += f"; score: {score}" + (f" ({row['temp']})" if row.get("temp") else "")
        if row.get("message"):
            note += "\n" + str(row["message"])
        props.append("NOTE:" + _vescape(note))
        props.append("END:VCARD")
        for p in props:
            lines += _fold(p)
    return "".join(line + "\r\n" for line in lines)


# -- import ----------------------------------------------------------------------------


class ImportRefused(ValueError):
    """The whole file is refused (too large, too many rows, unreadable, unknown kind)."""

    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass
class ImportResult:
    kind: str
    rows: int = 0
    added: int = 0
    skipped: int = 0  # duplicates
    rejected: list[tuple[int, str]] = field(default_factory=list)  # (line, code)
    receipt_id: str = ""

    def view(self) -> dict[str, Any]:
        return {"kind": self.kind, "rows": self.rows, "added": self.added,
                "skipped": self.skipped, "rejected": len(self.rejected),
                "errors": [{"line": n, "code": c}
                           for n, c in self.rejected[:MAX_REPORTED_ERRORS]],
                "receipt_id": self.receipt_id}


def normalize_email(value: str) -> str:
    """Trimmed, lower case; ``""`` when empty; ``ValueError`` when not an address."""
    text = value.strip().strip("<>").strip()
    if text.lower().startswith("mailto:"):
        text = text[7:]
    if not text:
        return ""
    if len(text) > _LIMITS["email"] or not EMAIL.fullmatch(text):
        raise ValueError("invalid_email")
    return text.lower()


def normalize_phone(value: str) -> str:
    """``+`` (when given) and digits only: ``"+48 000 000 123"`` → ``"+48000000123"``;
    ``""`` when empty or not 7–15 digits (an invalid number is reported by the import)."""
    text = value.strip()
    if text.lower().startswith("tel:"):
        text = text[4:]
    plus = text.startswith(("+", "00"))
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        digits = digits[2:]
    if not 7 <= len(digits) <= 15 or re.search(r"[^0-9+ ().\-/]", text):
        return ""
    return ("+" if plus else "") + digits


def _norm_header(name: str) -> str:
    return " ".join(str(name).replace("\ufeff", "").strip().lower().split())


def _field_of(header: str) -> str | None:
    h = _norm_header(header)
    for name, aliases in HEADERS.items():
        if h in aliases:
            return name
    return None


def _uncell(value: Any) -> str:
    """Undo the export's formula guard (``'=x`` → ``=x``)."""
    text = "" if value is None else str(value)
    if text.startswith("'") and _FORMULA.match(text[1:]):
        return text[1:]
    return text


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportRefused("not_utf8") from None


def _parse_csv(text: str) -> list[tuple[int, dict[str, str]]]:
    first = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise ImportRefused("empty") from None
    except csv.Error:
        raise ImportRefused("unreadable") from None
    fields = [_field_of(h) for h in header]
    if "email" not in fields and "phone" not in fields:
        raise ImportRefused("no_contact_column")
    out: list[tuple[int, dict[str, str]]] = []
    try:
        for cells in reader:
            line = reader.line_num
            if not any(c.strip() for c in cells):
                continue
            row: dict[str, str] = {}
            for name, cell in zip(fields, cells, strict=False):
                if name and name not in row:
                    row[name] = _uncell(cell)
            out.append((line, row))
            if len(out) > MAX_IMPORT_ROWS:
                raise ImportRefused("too_many_rows", limit=MAX_IMPORT_ROWS)
    except csv.Error:
        raise ImportRefused("unreadable") from None
    return out


def _parse_json(text: str) -> list[tuple[int, dict[str, str]]]:
    try:
        data = json.loads(text)
    except ValueError:
        raise ImportRefused("unreadable") from None
    if isinstance(data, dict) and isinstance(data.get("leads"), list):
        data = data["leads"]  # what GET /api/leads and the JSON export of an API return
    if not isinstance(data, list):
        raise ImportRefused("unreadable")
    if len(data) > MAX_IMPORT_ROWS:
        raise ImportRefused("too_many_rows", limit=MAX_IMPORT_ROWS)
    out = []
    for n, item in enumerate(data, start=1):
        row: dict[str, str] = {}
        if isinstance(item, dict):
            for key, value in item.items():
                name = _field_of(str(key))
                if name and name not in row and isinstance(value, str | int | float):
                    row[name] = str(value)
        out.append((n, row))  # the line is the item's position in the list (1-based)
    return out


def _vunescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_vcf(text: str) -> list[tuple[int, dict[str, str]]]:
    lines: list[tuple[int, str]] = []
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] = (lines[-1][0], lines[-1][1] + raw[1:])
        elif raw.strip():
            lines.append((n, raw))
    out: list[tuple[int, dict[str, str]]] = []
    card: dict[str, str] | None = None
    start = 0
    for n, line in lines:
        name, sep, value = line.partition(":")
        if not sep:
            continue
        prop = name.split(";", 1)[0].split(".")[-1].strip().upper()
        if prop == "BEGIN" and value.strip().upper() == "VCARD":
            card, start = {}, n
        elif prop == "END" and value.strip().upper() == "VCARD" and card is not None:
            out.append((start, card))
            card = None
            if len(out) > MAX_IMPORT_ROWS:
                raise ImportRefused("too_many_rows", limit=MAX_IMPORT_ROWS)
        elif card is not None:
            key = {"FN": "name", "EMAIL": "email", "TEL": "phone", "NOTE": "message"}.get(prop)
            if key and key not in card:
                card[key] = _vunescape(value)
    if not out and card is None:
        raise ImportRefused("unreadable")
    return out


_PARSERS = {"csv": _parse_csv, "json": _parse_json, "vcf": _parse_vcf}


def _contacts(conn: Any) -> tuple[set[str], set[str]]:
    emails, phones = set(), set()
    for email, phone in conn.execute("SELECT email, phone FROM leads"):
        if email:
            emails.add(str(email).strip().lower())
        p = normalize_phone(str(phone or ""))
        if p:
            phones.add(p.lstrip("+"))
    return emails, phones


def _text(value: str, limit: int) -> str:
    return envelope.clean(" ".join(value.split()) if limit < 1000 else value.strip(),
                          max_chars=limit)


def import_leads(conn: Any, data: bytes, kind: str, *, ledger: Ledger | None = None,
                 now: float | None = None) -> ImportResult:
    """Add the leads in ``data`` (``kind``: ``csv``, ``json`` or ``vcf``); one transaction.
    Raises ``ImportRefused`` for a file refused as a whole."""
    if kind not in _PARSERS:
        raise ImportRefused("unknown_format")
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportRefused("too_large", limit=MAX_IMPORT_BYTES)
    parsed = _PARSERS[kind](_decode(data))
    result = ImportResult(kind, rows=len(parsed))
    ts = time.time() if now is None else now
    with transaction(conn):
        emails, phones = _contacts(conn)
        for line, row in parsed:
            try:
                email = normalize_email(row.get("email", ""))
            except ValueError:
                result.rejected.append((line, "invalid_email"))
                continue
            raw_phone = row.get("phone", "").strip()
            phone = normalize_phone(raw_phone)
            if raw_phone and not phone:
                result.rejected.append((line, "invalid_phone"))
                continue
            if not email and not phone:
                result.rejected.append((line, "no_contact"))
                continue
            if (email and email in emails) or (phone and phone.lstrip("+") in phones):
                result.skipped += 1
                continue
            source = _SOURCE.sub("-", row.get("source", "").strip())[:_LIMITS["source"]]
            conn.execute(
                "INSERT INTO leads (id, name, email, phone, source, message, created_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("imp_" + secrets.token_hex(8), _text(row.get("name", ""), _LIMITS["name"]),
                 email, phone, source.strip("-.") or "import",
                 _text(row.get("message", ""), _LIMITS["message"]), ts))
            if email:
                emails.add(email)
            if phone:
                phones.add(phone.lstrip("+"))
            result.added += 1
        counts = {"rows": result.rows, "added": result.added, "skipped": result.skipped,
                  "rejected": len(result.rejected)}
        if ledger is not None:  # counts only: never a name or a contact
            receipt = ledger.append(
                IMPORT_RUN, agent=IMPORT_AGENT, kind="tool_call", conn=conn,
                input_sha256=[sha256_hex(data)], output_sha256=sha256_hex(canonical(counts)),
                details={"tool": "leads.import", "format": kind, "side_effects": "local",
                         **counts})
            result.receipt_id = receipt.id
    return result


# -- monthly statement -------------------------------------------------------------------

STATEMENT_COLUMNS = ("date", "campaign_item_id", "campaign_name", "meta_campaign_id",
                     "currency", "spend", "approved_daily_budget", "approved_by",
                     "approved_at", "approval_id", "approval_receipt_hash", "model_cost_usd",
                     "run_id")
PROFILES: dict[str, tuple[str, ...]] = {
    "generic": STATEMENT_COLUMNS,
    "xero": ("Date", "Amount", "Payee", "Description", "Reference"),
    "quickbooks": ("Date", "Description", "Amount"),
    "wfirma": ("Data", "Kontrahent", "Opis", "Kwota", "Waluta"),
}
STATEMENT_FORMATS = ("csv", "json", "hledger")
MONTH = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
BUDGET_ACTIONS = ("campaign.create", "campaign.activate", "campaign.update")
META_PAYEE = "Meta Platforms"
MODELS_PAYEE = "Muse Studio models"


def month_bounds(month: str) -> tuple[str, str, float, float]:
    """First and last day (``YYYY-MM-DD``) and the UTC timestamps bounding the month."""
    m = MONTH.fullmatch(month or "")
    if m is None:
        raise ValueError("invalid_month")
    first = datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=UTC)
    nxt = (first + timedelta(days=32)).replace(day=1)
    last = nxt - timedelta(days=1)
    return (first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"), first.timestamp(),
            nxt.timestamp())


def current_month(now: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if now is None else now, UTC).strftime("%Y-%m")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), UTC).strftime("%Y-%m-%d")


def _day_end(day: str) -> float:
    return (datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
            + timedelta(days=1)).timestamp()


def _short_key(approver: str) -> str:
    """``ed25519:<32 hex>`` → ``ed25519:<first 8 hex>`` (enough to find it on /receipts)."""
    return approver[:16] if approver else ""


def _budget_after(action: str, payload: Mapping[str, Any], before: int | None) -> int | None:
    try:
        if action == "campaign.create":
            return int(payload["campaign"]["daily_budget_minor"])
        if action == "campaign.activate":
            return int(payload["daily_budget_minor"])
        change = payload.get("change") or {}
        if change.get("kind") == "budget":
            return int(change["to_minor"])
    except (KeyError, TypeError, ValueError):
        pass
    return before


def _approvals(conn: Any, item_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """Per campaign: its approved budget-bearing approvals whose execution is ``done``
    (a signed change that failed or never ran changed nothing), oldest first, each with
    the daily budget in force after it and the hash of its ``approval`` receipt."""
    ids = list(item_ids)
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT a.id, a.run_id, a.action, a.item_id, a.approver, a.decided_ts, "  # noqa: S608
        "coalesce(p.payload, '{}') FROM approvals a "
        "LEFT JOIN pending_payloads p ON p.approval_id = a.id "
        "JOIN executions e ON e.approval_id = a.id AND e.status = 'done' "
        f"WHERE a.status = 'approved' AND a.item_id IN ({marks}) "
        f"AND a.action IN ({', '.join('?' for _ in BUDGET_ACTIONS)}) "
        "ORDER BY a.decided_ts, a.id", (*ids, *BUDGET_ACTIONS)).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for aid, run_id, action, item, approver, decided, payload in rows:
        try:
            body = json.loads(payload)
        except ValueError:
            body = {}
        chain = out.setdefault(item, [])
        budget = _budget_after(action, body if isinstance(body, dict) else {},
                               chain[-1]["budget_minor"] if chain else None)
        receipt = conn.execute(
            "SELECT hash FROM receipts WHERE run_id = ? AND json_extract(body, '$.kind') = "
            "'approval' AND json_extract(body, '$.details.pending_id') = ? LIMIT 1",
            (run_id, aid)).fetchone()
        chain.append({"approval_id": aid, "approver": str(approver or ""),
                      "decided_ts": float(decided or 0), "budget_minor": budget,
                      "receipt_hash": receipt[0] if receipt else ""})
    return out


def _window(body: Mapping[str, Any], created_ts: float) -> tuple[str, str]:
    day = _day(created_ts)
    start, stop = body.get("date_start"), body.get("date_stop")
    ok = re.compile(r"\d{4}-\d{2}-\d{2}")
    stop = stop if isinstance(stop, str) and ok.fullmatch(stop) else day
    start = start if isinstance(start, str) and ok.fullmatch(start) and start <= stop else stop
    return start, stop


def _days(start: str, stop: str) -> list[str]:
    first = datetime.strptime(start, "%Y-%m-%d")
    n = (datetime.strptime(stop, "%Y-%m-%d") - first).days
    return [(first + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(min(n, 366) + 1)]


def _daily_spend(snaps: Iterable[tuple[str, Mapping[str, Any], float]]
                 ) -> dict[str, dict[str, float]]:
    """``campaign → day → spend`` from snapshots given newest first. Every pull covers the
    last 7 days, so windows overlap: each day is taken from the newest snapshot that has
    it. A snapshot's daily rows (``spend_by_day``) are used as they are; a window without
    them books what its total leaves after the days newer snapshots already booked,
    spread evenly (to the cent) over the days only it covers. No day is dropped or booked
    twice."""
    out: dict[str, dict[str, float]] = {}
    for cid, body, created in snaps:
        booked = out.setdefault(cid, {})
        by_day = body.get("spend_by_day")
        if isinstance(by_day, Mapping) and by_day:
            for day, value in by_day.items():
                if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) \
                        and day not in booked:
                    try:
                        booked[day] = round(float(value or 0.0), 2)
                    except (TypeError, ValueError):
                        continue
            continue
        start, stop = _window(body, created)
        days = _days(start, stop)
        free = [d for d in days if d not in booked]
        if not free:
            continue
        try:
            total = float((body.get("totals") or {}).get("spend") or 0.0)
        except (TypeError, ValueError):
            total = 0.0
        cents = max(0, round((total - sum(booked[d] for d in days if d in booked)) * 100))
        share, extra = divmod(cents, len(free))
        for i, day in enumerate(free):
            booked[day] = (share + (1 if i >= len(free) - extra else 0)) / 100
    return out


def _run_costs(conn: Any, lo: float, hi: float) -> dict[str, tuple[float, float]]:
    """``run_id → (model cost in USD, last receipt ts)`` for receipts written in the month."""
    rows = conn.execute(
        "SELECT run_id, sum(coalesce(json_extract(body, '$.cost_usd'), 0)), max(ts) "
        "FROM receipts WHERE ts >= ? AND ts < ? GROUP BY run_id", (lo, hi)).fetchall()
    return {r[0]: (float(r[1] or 0.0), float(r[2])) for r in rows}


def statement(conn: Any, month: str, *, exclude_runs: Iterable[str] = ()
              ) -> list[dict[str, Any]]:
    """The month's statement rows (``STATEMENT_COLUMNS``), by date. ``exclude_runs``:
    runs that belong to another account (its API-drafted briefs), never shown."""
    first, last, lo, hi = month_bounds(month)
    skip = set(exclude_runs)
    with conn.lock:
        campaigns = {r[0]: r for r in conn.execute(
            "SELECT id, run_id, name, external_id, currency, daily_budget_minor "
            "FROM campaigns").fetchall() if r[1] not in skip}
        snaps = conn.execute("SELECT campaign_id, body, created_ts FROM insights_snapshots "
                             "ORDER BY created_ts DESC, id DESC").fetchall()
        chains = _approvals(conn, campaigns)
        costs = {run: c for run, c in _run_costs(conn, lo, hi).items() if run not in skip}
    parsed = []
    for cid, raw, created in snaps:
        if cid not in campaigns:
            continue
        try:
            body = json.loads(raw)
        except ValueError:
            continue
        if isinstance(body, dict):
            parsed.append((cid, body, float(created)))
    rows: list[dict[str, Any]] = []
    for cid, days in _daily_spend(parsed).items():
        _, run_id, name, external, currency, budget_now = campaigns[cid]
        currency = str(currency or "PLN")
        chain = chains.get(cid, [])
        for day, spend in sorted(days.items()):
            if not first <= day <= last or spend <= 0:
                continue  # each day in its own month; a day with no spend is no expense
            end = _day_end(day)
            approval = next((a for a in reversed(chain) if a["decided_ts"] < end),
                            chain[0] if chain else None)
            budget_minor = approval["budget_minor"] if approval and approval[
                "budget_minor"] is not None else int(budget_now)
            rows.append({
                "date": day, "campaign_item_id": cid,
                "campaign_name": envelope.clean(str(name)),
                "meta_campaign_id": str(external or ""), "currency": currency,
                "spend": round(spend, 2),
                "approved_daily_budget": round(budget_minor / _offset(currency), 2),
                "approved_by": _short_key(approval["approver"]) if approval else "",
                "approved_at": _iso(approval["decided_ts"]) if approval else "",
                "approval_id": approval["approval_id"] if approval else "",
                "approval_receipt_hash": approval["receipt_hash"] if approval else "",
                "model_cost_usd": 0.0, "run_id": str(run_id or "")})
    rows.sort(key=lambda r: (r["date"], r["campaign_item_id"]))
    booked: set[str] = set()
    for row in rows:  # a run's model cost goes on its first row of the month
        run = row["run_id"]
        if run and run not in booked and run in costs:
            row["model_cost_usd"] = round(costs[run][0], 6)
            booked.add(run)
    for run, (cost, last_ts) in sorted(costs.items()):
        if run in booked or round(cost, 6) <= 0:
            continue
        rows.append({"date": _day(last_ts), "campaign_item_id": "", "campaign_name": "",
                     "meta_campaign_id": "", "currency": "", "spend": 0.0,
                     "approved_daily_budget": "", "approved_by": "", "approved_at": "",
                     "approval_id": "", "approval_receipt_hash": "",
                     "model_cost_usd": round(cost, 6), "run_id": run})
    rows.sort(key=lambda r: (r["date"], r["campaign_item_id"] == "", r["campaign_item_id"],
                             r["run_id"]))
    return rows


def _offset(currency: str) -> int:
    try:
        return offset(currency)
    except ValueError:
        return 100


def totals(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Spend per currency and the model cost in USD (exact sums of the rows)."""
    spend: dict[str, float] = {}
    model = 0.0
    n = 0
    for r in rows:
        n += 1
        if r.get("currency"):
            spend[r["currency"]] = round(spend.get(r["currency"], 0.0) + float(r["spend"]), 2)
        model = round(model + float(r.get("model_cost_usd") or 0.0), 6)
    return {"rows": n, "spend": dict(sorted(spend.items())), "model_cost_usd": model}


def _money(amount: float, places: int = 2) -> str:
    return f"{amount:.{places}f}"


def _lines(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One expense line per amount: the ad spend (campaign currency) and the model cost
    (USD, to the cent) of each row, zero amounts left out."""
    out = []
    for r in rows:
        if r.get("currency") and float(r["spend"]) > 0:
            out.append({"date": r["date"], "amount": float(r["spend"]), "currency":
                        r["currency"], "payee": META_PAYEE,
                        "description": f"Meta ads: {r['campaign_name']} "
                                       f"({r['campaign_item_id']})",
                        "reference": r["approval_id"] or r["run_id"]})
        cost = round(float(r.get("model_cost_usd") or 0.0), 2)
        if cost > 0:
            out.append({"date": r["date"], "amount": cost, "currency": "USD",
                        "payee": MODELS_PAYEE, "description": f"Model calls: run {r['run_id']}",
                        "reference": r["run_id"]})
    return out


def statement_csv(rows: Sequence[Mapping[str, Any]], profile: str = "generic") -> str:
    """CSV in one of ``PROFILES``. ``generic``: every column, amounts positive, model cost
    to 6 places. ``xero`` and ``quickbooks`` (bank-statement imports): expenses negative.
    ``wfirma``: amounts positive with their currency."""
    if profile not in PROFILES:
        raise ValueError("invalid_profile")
    if profile == "generic":
        return to_csv(rows, STATEMENT_COLUMNS)
    out = []
    for line in _lines(rows):
        amount = _money(line["amount"])
        if profile == "xero":
            out.append({"Date": line["date"], "Amount": "-" + amount, "Payee": line["payee"],
                        "Description": f"{line['description']} [{line['currency']}]",
                        "Reference": line["reference"]})
        elif profile == "quickbooks":
            out.append({"Date": line["date"], "Amount": "-" + amount,
                        "Description": f"{line['payee']}: {line['description']} "
                                       f"[{line['currency']}] {line['reference']}"})
        else:
            out.append({"Data": line["date"], "Kontrahent": line["payee"],
                        "Opis": f"{line['description']} {line['reference']}",
                        "Kwota": amount, "Waluta": line["currency"]})
    return to_csv(out, PROFILES[profile])


def _hl_text(text: str) -> str:
    """Description / tag text: one line, no ``;`` (a comment) and no ``,`` (tag ends)."""
    return " ".join(re.sub(r"[;,\r\n\t]", " ", text).split())


def statement_hledger(rows: Iterable[Mapping[str, Any]]) -> str:
    """An hledger / ledger journal: one transaction per row, every commodity balanced;
    the approval id, the approval receipt hash, the run and the approver key are tags, so
    each line can be traced to its signature and checked with ``studio verify``."""
    out = ["; Muse Studio Lite monthly statement (hledger journal)", ""]
    for r in rows:
        if r.get("campaign_item_id"):
            title = f"Meta ads | {_hl_text(r['campaign_name']) or r['campaign_item_id']}"
        else:
            title = f"Muse models | run {r['run_id']}"
        tags = [f"run:{r['run_id']}"] if r.get("run_id") else []
        if r.get("campaign_item_id"):
            tags += [f"campaign:{r['campaign_item_id']}"]
            if r.get("meta_campaign_id"):
                tags.append(f"meta:{r['meta_campaign_id']}")
        out.append(f"{r['date']} {title}" + (f"  ; {', '.join(tags)}" if tags else ""))
        if r.get("approval_id"):
            out.append(f"    ; approval:{r['approval_id']}, approver:{r['approved_by']}")
            out.append(f"    ; receipt:{r['approval_receipt_hash']}")
        if r.get("currency") and float(r["spend"]):
            amount = _money(float(r["spend"]))
            out.append(f"    expenses:advertising:meta    {amount} {r['currency']}")
            out.append(f"    liabilities:meta    -{amount} {r['currency']}")
        cost = float(r.get("model_cost_usd") or 0.0)
        if cost:
            amount = _money(cost, 6)
            out.append(f"    expenses:software:muse-models    {amount} USD")
            out.append(f"    liabilities:muse-models    -{amount} USD")
        out.append("")
    return "\n".join(out)


def statement_json(month: str, rows: list[dict[str, Any]]) -> str:
    return to_json({"month": month, "columns": list(STATEMENT_COLUMNS), "rows": rows,
                    "totals": totals(rows)})


def lead_file(rows: list[dict[str, Any]], fmt: str) -> tuple[str, str, str]:
    """``(body, media type, file name)`` of a leads export."""
    if fmt == "csv":
        return to_csv(rows, LEAD_COLUMNS), "text/csv; charset=utf-8", "leads.csv"
    if fmt == "vcf":
        return leads_to_vcf(rows), "text/vcard; charset=utf-8", "leads.vcf"
    if fmt == "json":
        return to_json(rows), "application/json", "leads.json"
    raise ValueError("invalid_format")


def statement_file(month: str, rows: list[dict[str, Any]], fmt: str,
                   profile: str = "generic") -> tuple[str, str, str]:
    """``(body, media type, file name)`` of a statement download."""
    if fmt == "csv":
        return (statement_csv(rows, profile), "text/csv; charset=utf-8",
                f"statement-{month}-{profile}.csv")
    if fmt == "hledger":
        return (statement_hledger(rows), "text/plain; charset=utf-8",
                f"statement-{month}.journal")
    if fmt == "json":
        return statement_json(month, rows), "application/json", f"statement-{month}.json"
    raise ValueError("invalid_format")
