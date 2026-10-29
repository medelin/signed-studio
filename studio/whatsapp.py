"""WhatsApp Cloud API (Graph v25, plain httpx): notifications out, voice briefs in.

- **Configured only by environment**: ``STUDIO_WHATSAPP=1`` (Graph) or ``mock`` (sends kept
  in ``MockTransport.sent``, media served from ``MockTransport.media``), with
  ``META_WHATSAPP_TOKEN`` (a system-user token: ``whatsapp_business_messaging`` +
  ``whatsapp_business_management``), ``META_WHATSAPP_PHONE_ID`` (the sending number),
  ``META_WHATSAPP_WABA_ID`` (templates; optional for sending), ``META_APP_SECRET`` (the
  webhook's ``X-Hub-Signature-256`` and ``appsecret_proof``) and
  ``STUDIO_WHATSAPP_VERIFY_TOKEN`` (the webhook's GET handshake). Unset = off.
- **Only links travel**: a template carries a count and a link to the studio. Nothing is
  ever signed, approved or run from a WhatsApp message; signing stays in the browser.
- **Inbound**: ``POST /webhooks/whatsapp`` is authenticated by the HMAC of the raw body
  with the app secret (constant-time compare; unsigned, badly signed, oversized bodies are
  refused), handled once per message id, rate-limited per sender. A message from a number
  no account linked gets no reply and no processing: one receipt, that is all.
- **Receipts without phone numbers**: one ``whatsapp.send`` receipt per send (template
  name, language, status, ``recipient_sha256``, ``message_sha256``) and one
  ``whatsapp.inbound`` per received message (``sender_sha256``, type, outcome). Numbers
  are keyed hashes (HMAC-SHA256 with the app secret), never in clear; message ids are
  hashed too (a ``wamid`` encodes the phone number). ``side_effects`` is ``notice``, as
  for e-mail: ``external`` is reserved for adapter steps that ride on a signed approval
  (``studio.receipts.verify`` would rightly call an unapproved one unauthorised).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any
from urllib.parse import urlsplit

import httpx

from studio.approvals import transaction
from studio.receipts import Ledger

LOG = logging.getLogger("studio.whatsapp")

GRAPH_VERSION = "v25.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
TIMEOUT_S = 10.0
MEDIA_TIMEOUT_S = 30.0
MAX_WEBHOOK_BODY = 64 * 1024
MAX_MEDIA_BYTES = 16 * 1024 * 1024  # WhatsApp's own audio limit
MAX_TEXT_BRIEF = 4000
WINDOW_S = 24 * 3600 - 300  # the customer-service window, with a margin
RATE_MAX = 6  # inbound messages processed per sender ...
RATE_WINDOW_S = 600.0  # ... per ten minutes
RUN_ID = "whatsapp"
AGENT = "whatsapp"
WEBHOOK_PATH = "/webhooks/whatsapp"
MEDIA_HOSTS = (".fbsbx.com", ".facebook.com", ".whatsapp.net", ".fbcdn.net")
E164 = re.compile(r"\+[1-9][0-9]{6,14}")  # ASCII digits only (\d is Unicode)
DIGITS = re.compile(r"[0-9]{5,20}")
TOKEN_OK = re.compile(r"[A-Za-z0-9_.~-]{16,128}")
WAMID = re.compile(r"[A-Za-z0-9_.:=+/-]{1,256}")
LANGS = {"pl": "pl", "en": "en_US"}
AUDIO_TYPES = ("audio",)

CODE_TTL_S = 900  # a verification code is valid for 15 minutes
CODE_RE = re.compile(r"\bMUSE-([0-9]{6})\b", re.IGNORECASE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS whatsapp_inbound (
    message_sha256 TEXT PRIMARY KEY,  -- a wamid encodes the sender's number: kept hashed
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS whatsapp_codes (
    account_id TEXT PRIMARY KEY,  -- one live code per account
    number TEXT NOT NULL,  -- the number it proves (E.164)
    code_sha256 TEXT NOT NULL,  -- sha256(account | number | code): never the code itself
    expires_ts REAL NOT NULL
);
-- one account per verified number
CREATE UNIQUE INDEX IF NOT EXISTS accounts_whatsapp_verified ON accounts (whatsapp_number)
    WHERE whatsapp_verified_ts > 0 AND whatsapp_number != '';
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# -- configuration ---------------------------------------------------------------------


@dataclass(frozen=True)
class WhatsAppConfig:
    transport: str = ""  # "" = off, "graph" or "mock"
    token: str = field(default="", repr=False)
    phone_id: str = ""
    waba_id: str = ""
    app_secret: str = field(default="", repr=False)
    verify_token: str = field(default="", repr=False)
    display_number: str = ""  # the studio's own number, shown with the verification code

    @property
    def enabled(self) -> bool:
        return bool(self.transport)

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> WhatsAppConfig:
        """Raises ``ValueError`` naming the variable."""
        flag = e.get("STUDIO_WHATSAPP", "").strip().lower()
        if flag in ("", "0", "false", "no", "off"):
            return cls()
        if flag not in ("1", "true", "yes", "on", "mock"):
            raise ValueError("STUDIO_WHATSAPP must be 1 (Cloud API), mock or unset")
        transport = "mock" if flag == "mock" else "graph"
        token = e.get("META_WHATSAPP_TOKEN", "")
        phone_id = e.get("META_WHATSAPP_PHONE_ID", "")
        waba_id = e.get("META_WHATSAPP_WABA_ID", "")
        secret = e.get("META_APP_SECRET", "")
        verify = e.get("STUDIO_WHATSAPP_VERIFY_TOKEN", "")
        if transport == "graph":
            if not token or any(c.isspace() for c in token):
                raise ValueError("META_WHATSAPP_TOKEN is required with STUDIO_WHATSAPP=1")
            if not DIGITS.fullmatch(phone_id):
                raise ValueError("META_WHATSAPP_PHONE_ID must be the numeric phone number id")
            if not secret:
                raise ValueError("META_APP_SECRET is required with STUDIO_WHATSAPP=1 (it "
                                 "authenticates the webhook)")
            if not TOKEN_OK.fullmatch(verify):
                raise ValueError("STUDIO_WHATSAPP_VERIFY_TOKEN must be 16-128 characters of "
                                 "[A-Za-z0-9_.~-]")
        elif phone_id and not DIGITS.fullmatch(phone_id):
            raise ValueError("META_WHATSAPP_PHONE_ID must be the numeric phone number id")
        if verify and not TOKEN_OK.fullmatch(verify):
            raise ValueError("STUDIO_WHATSAPP_VERIFY_TOKEN must be 16-128 characters of "
                             "[A-Za-z0-9_.~-]")
        display = e.get("STUDIO_WHATSAPP_NUMBER", "")
        if display:
            try:
                display = normalize_number(display)
            except ValueError:
                raise ValueError("STUDIO_WHATSAPP_NUMBER must be the studio's number in "
                                 "E.164, e.g. +15551982857") from None
        if waba_id and not DIGITS.fullmatch(waba_id):
            raise ValueError("META_WHATSAPP_WABA_ID must be the numeric WhatsApp Business "
                             "Account id")
        return cls(transport, token=token, phone_id=phone_id or "0" * 15, waba_id=waba_id,
                   app_secret=secret, verify_token=verify, display_number=display)


# -- numbers and hashes ----------------------------------------------------------------


def normalize_number(value: str) -> str:
    """An owner's number in E.164 (``+48783306696``); spaces, dashes, dots and brackets
    are dropped, a leading ``00`` becomes ``+``. ``ValueError`` otherwise."""
    raw = (value or "").strip()
    if len(raw) > 32:
        raise ValueError("number too long")
    cleaned = re.sub(r"[\s\-().]", "", raw)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not E164.fullmatch(cleaned):
        raise ValueError("not an E.164 number")
    return cleaned


def wa_id(number: str) -> str:
    """The Cloud API's form of a number: digits only, no ``+``."""
    return number.lstrip("+")


def number_hash(number: str, key: str) -> str:
    """A keyed hash of a number (digits only), safe for a public receipt: without the key a
    phone number's small space cannot be enumerated back from it."""
    digits = re.sub(r"[^0-9]", "", number or "")
    return hmac.new((key or "muse-studio-lite").encode(), b"wa:" + digits.encode(),
                    hashlib.sha256).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """``X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body>``; refuses
    everything without an app secret. Constant-time compare."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[7:].strip().lower())


def sign(body: bytes, app_secret: str) -> str:
    """The header value Meta would send (tests, local checks)."""
    return "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()


def check_challenge(params: Mapping[str, str], verify_token: str) -> str | None:
    """The ``hub.challenge`` to echo, or ``None`` (wrong mode, token or challenge)."""
    challenge = params.get("hub.challenge", "")
    if (params.get("hub.mode") != "subscribe" or not verify_token
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", challenge)
            or not hmac.compare_digest(params.get("hub.verify_token", "").encode(),
                                       verify_token.encode())):
        return None
    return challenge


# -- transports --------------------------------------------------------------------------


class WhatsAppError(Exception):
    """A refusal or failure; ``code`` is safe to log (no number, no token)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail  # Graph's message for the operator (scripts); never logged


class GraphTransport:
    """Bearer token + ``appsecret_proof``; no redirects; 10 s timeout."""

    def __init__(self, cfg: WhatsAppConfig) -> None:
        self.cfg = cfg

    def _params(self) -> dict[str, str]:
        if not self.cfg.app_secret:
            return {}
        return {"appsecret_proof": hmac.new(self.cfg.app_secret.encode(),
                                            self.cfg.token.encode(), hashlib.sha256).hexdigest()}

    def _client(self, timeout: float = TIMEOUT_S) -> httpx.Client:
        return httpx.Client(headers={"Authorization": f"Bearer {self.cfg.token}"},
                            timeout=timeout, follow_redirects=False)

    @staticmethod
    def _json(r: httpx.Response) -> dict[str, Any]:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or not isinstance(data, dict):
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            code = err.get("code") if isinstance(err, dict) else None
            detail = str(err.get("error_user_msg") or err.get("message") or "")[:300] \
                if isinstance(err, dict) else ""
            raise WhatsAppError(f"graph_{code}" if isinstance(code, int) else
                                f"http_{r.status_code}", detail)
        return data

    def post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._client() as c:
            return self._json(c.post(f"{GRAPH}/{path}", params=self._params(), json=payload))

    def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        with self._client() as c:
            return self._json(c.get(f"{GRAPH}/{path}", params={**self._params(),
                                                                 **(params or {})}))

    def download(self, url: str, max_bytes: int) -> bytes:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host.endswith(MEDIA_HOSTS):
            raise WhatsAppError("media_url_refused")
        out = bytearray()
        with self._client(MEDIA_TIMEOUT_S) as c, c.stream("GET", url) as r:
            if r.status_code != 200:
                raise WhatsAppError(f"media_http_{r.status_code}")
            for chunk in r.iter_bytes():
                out += chunk
                if len(out) > max_bytes:
                    raise WhatsAppError("media_too_large")
        return bytes(out)


class MockTransport:
    """No network: sends are kept, media comes from ``media`` (id → (bytes, mime))."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.media: dict[str, tuple[bytes, str]] = {}
        self.fail: WhatsAppError | None = None

    def post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        self.sent.append({"path": path, "payload": json.loads(json.dumps(payload))})
        return {"messaging_product": "whatsapp",
                "messages": [{"id": "wamid.mock" + secrets.token_hex(8)}]}

    def get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        if path not in self.media:
            raise WhatsAppError("graph_100")
        data, mime = self.media[path]
        return {"url": f"https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid={path}",
                "mime_type": mime, "file_size": len(data), "id": path}

    def download(self, url: str, max_bytes: int) -> bytes:
        mid = url.rsplit("mid=", 1)[-1]
        data = self.media[mid][0]
        if len(data) > max_bytes:
            raise WhatsAppError("media_too_large")
        return data


# -- client ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    name: str
    event: str
    button: str  # "dynamic" (URL suffix = a reference) or "static"
    path: str  # the studio page the button opens
    body: dict[str, str]  # lang -> body text ({{1}} = the count, when present)
    button_text: dict[str, str]
    example: str = ""  # the {{1}} example for the body


TEMPLATES: dict[str, Template] = {
    "approval_waiting": Template(
        "muse_approval_waiting", "approval_waiting", "dynamic", "/approvals/",
        {"en": "Muse Studio Lite: a campaign step is waiting for your approval. Review it "
               "and sign it in your browser; nothing is sent or spent until you do.",
         "pl": "Muse Studio Lite: krok kampanii czeka na Twoją akceptację. Sprawdź go i "
               "podpisz w przeglądarce; nic nie zostanie wysłane ani wydane, dopóki tego "
               "nie zrobisz."},
        {"en": "Review and sign", "pl": "Sprawdź i podpisz"}),
    "lead_new": Template(
        "muse_new_leads", "lead_new", "static", "/leads",
        {"en": "Muse Studio Lite: new leads arrived from your campaign: {{1}}. Open the list "
               "in the studio to follow up.",
         "pl": "Muse Studio Lite: nowe leady z Twojej kampanii: {{1}}. Otwórz listę w "
               "studiu, aby się nimi zająć."},
        {"en": "Open leads", "pl": "Otwórz leady"}, example="3"),
    "ad_disapproved": Template(
        "muse_ad_disapproved", "ad_disapproved", "static", "/studio",
        {"en": "Muse Studio Lite: Meta did not approve one of your ads. Open the studio to "
               "see the reason and the suggested fix.",
         "pl": "Muse Studio Lite: Meta nie zatwierdziła jednej z Twoich reklam. Otwórz "
               "studio, aby zobaczyć powód i proponowaną poprawkę."},
        {"en": "Open the studio", "pl": "Otwórz studio"}),
}


def template_definition(t: Template, lang: str, base_url: str) -> dict[str, Any]:
    """The ``POST /{WABA_ID}/message_templates`` body of one template in one language."""
    base = base_url.rstrip("/")
    body: dict[str, Any] = {"type": "BODY", "text": t.body[lang]}
    if "{{1}}" in t.body[lang]:
        body["example"] = {"body_text": [[t.example or "1"]]}
    button: dict[str, Any] = {"type": "URL", "text": t.button_text[lang]}
    if t.button == "dynamic":
        button["url"] = base + t.path + "{{1}}"
        button["example"] = [base + t.path + "0123456789abcdef0123456789abcdef"]
    else:
        button["url"] = base + t.path
    return {"name": t.name, "language": LANGS[lang], "category": "UTILITY",
            "components": [body, {"type": "BUTTONS", "buttons": [button]}]}


def template_message(to: str, t: Template, lang: str, *, count: int = 0,
                     ref: str = "") -> dict[str, Any]:
    """A template message (Graph docs: messaging_product, to, type=template,
    template{name, language{code}, components})."""
    components: list[dict[str, Any]] = []
    if "{{1}}" in t.body[lang]:
        components.append({"type": "body",
                           "parameters": [{"type": "text", "text": str(int(count))}]})
    if t.button == "dynamic":
        components.append({"type": "button", "sub_type": "url", "index": "0",
                           "parameters": [{"type": "text", "text": ref}]})
    template: dict[str, Any] = {"name": t.name, "language": {"code": LANGS[lang]}}
    if components:
        template["components"] = components
    return {"messaging_product": "whatsapp", "recipient_type": "individual",
            "to": wa_id(to), "type": "template", "template": template}


class Client:
    """Sends (each with a receipt) and media downloads."""

    def __init__(self, cfg: WhatsAppConfig, ledger: Ledger,
                 transport: GraphTransport | MockTransport | None = None) -> None:
        self.cfg = cfg
        self.ledger = ledger
        self.transport = transport or (MockTransport() if cfg.transport == "mock"
                                       else GraphTransport(cfg))

    def _send(self, to: str, payload: dict[str, Any], *, kind: str, template: str = "",
              lang: str = "", account: str = "", event: str = "") -> str:
        status, detail, message_id = "ok", "", ""
        try:
            out = self.transport.post(f"{self.cfg.phone_id}/messages", payload)
            messages = out.get("messages") or [{}]
            message_id = str((messages[0] or {}).get("id", ""))
        except WhatsAppError as exc:
            status, detail = "failed", exc.code
        except Exception as exc:  # noqa: BLE001 - recorded, raised as a WhatsAppError
            status, detail = "failed", type(exc).__name__
        try:
            self.ledger.append(RUN_ID, agent=AGENT, kind="tool_call", details={
                "tool": "whatsapp.send", "status": status, "side_effects": "notice",
                "message_type": kind, "template": template, "lang": lang, "event": event,
                "account": account,
                "recipient_sha256": number_hash(to, self.cfg.app_secret),
                **({"message_sha256": sha256_text(message_id)} if message_id else {}),
                **({"error": detail} if detail else {})})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("whatsapp receipt failed: %s", type(exc).__name__)
        if status != "ok":
            LOG.warning("whatsapp send failed: %s", detail)
            raise WhatsAppError(detail)
        return message_id

    def send_template(self, to: str, t: Template, lang: str, *, count: int = 0,
                      ref: str = "", account: str = "") -> str:
        lang = lang if lang in LANGS else "en"
        return self._send(to, template_message(to, t, lang, count=count, ref=ref),
                          kind="template", template=t.name, lang=LANGS[lang],
                          account=account, event=t.event)

    def send_text(self, to: str, body: str, *, account: str = "", lang: str = "") -> str:
        """A free-form text: only inside the 24 h window the owner opened by writing."""
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual",
                   "to": wa_id(to), "type": "text",
                   "text": {"body": body[:4096], "preview_url": False}}
        return self._send(to, payload, kind="text", account=account, lang=lang,
                          event="reply")

    def download_media(self, media_id: str, max_bytes: int = MAX_MEDIA_BYTES
                       ) -> tuple[bytes, str]:
        """``GET /{media-id}`` → a short-lived URL → the bytes (capped)."""
        if not re.fullmatch(r"[0-9]{1,32}|[A-Za-z0-9_-]{1,64}", media_id or ""):
            raise WhatsAppError("bad_media_id")
        meta = self.transport.get(media_id)
        size = meta.get("file_size")
        if isinstance(size, int) and size > max_bytes:
            raise WhatsAppError("media_too_large")
        return self.transport.download(str(meta.get("url", "")), max_bytes), \
            str(meta.get("mime_type", ""))


# -- accounts ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Linked:
    account_id: str
    number: str
    lang: str


def preferences(conn: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    """``number``, ``on`` (the opt-in) and ``verified`` (the owner proved control of the
    number by sending a code from it). A number that is not verified is *pending*: nothing
    is sent to it and nothing it sends is processed."""
    with _locked(conn):
        row = conn.execute("SELECT whatsapp_number, notify_whatsapp, whatsapp_verified_ts "
                           "FROM accounts WHERE id = ?", (account_id,)).fetchone()
    number = row[0] if row else ""
    return {"number": number, "on": bool(row[1]) if row else False,
            "verified": bool(row and number and row[2] > 0)}


def set_preferences(conn: sqlite3.Connection, account_id: str, *, number: str,
                    on: bool) -> str:
    """Store the owner's number (validated E.164, '' clears it) and the opt-in; the stored
    number. ``ValueError`` for an invalid number. On without a number is refused. A
    changed number starts unverified (pending) and its old code is dropped."""
    number = normalize_number(number) if number.strip() else ""
    if on and not number:
        raise ValueError("a number is required to turn WhatsApp on")
    with transaction(conn) as c:
        row = c.execute("SELECT whatsapp_number FROM accounts WHERE id = ?",
                        (account_id,)).fetchone()
        if row is not None and row[0] != number:
            c.execute("UPDATE accounts SET whatsapp_verified_ts = 0 WHERE id = ?",
                      (account_id,))
            c.execute("DELETE FROM whatsapp_codes WHERE account_id = ?", (account_id,))
        c.execute("UPDATE accounts SET whatsapp_number = ?, notify_whatsapp = ? WHERE id = ?",
                  (number, int(on), account_id))
    return number


def _code_hash(account_id: str, number: str, code: str) -> str:
    return sha256_text(f"{account_id}|{number}|{code.upper()}")


def issue_code(conn: sqlite3.Connection, account_id: str, *,
               now: float | None = None) -> str:
    """A one-time ``MUSE-123456`` for the account's pending number (15 min; only its hash
    is stored; a new code replaces the old). ``ValueError`` without a pending number."""
    now = time.time() if now is None else now
    prefs = preferences(conn, account_id)
    if not prefs["number"] or prefs["verified"]:
        raise ValueError("no pending number to verify")
    code = f"MUSE-{secrets.randbelow(10**6):06d}"
    with transaction(conn) as c:
        c.execute("INSERT INTO whatsapp_codes (account_id, number, code_sha256, expires_ts) "
                  "VALUES (?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE SET "
                  "number = excluded.number, code_sha256 = excluded.code_sha256, "
                  "expires_ts = excluded.expires_ts",
                  (account_id, prefs["number"],
                   _code_hash(account_id, prefs["number"], code), now + CODE_TTL_S))
    return code


@dataclass(frozen=True)
class Verified:
    account_id: str
    lang: str
    unlinked: tuple[str, ...]  # accounts whose verified link to this number was cleared


def verify_code(conn: sqlite3.Connection, sender: str, text: str, *,
                now: float | None = None) -> Verified | None:
    """Link ``sender`` to the account whose live code for exactly this number is in
    ``text``; any other account's verified link to the number is cleared (it moves only by
    the same proof). ``None`` when no code, a wrong, expired or other-number code."""
    now = time.time() if now is None else now
    found = CODE_RE.search(text or "")
    digits = re.sub(r"[^0-9]", "", sender or "")
    if found is None or not DIGITS.fullmatch(digits):
        return None
    code, number = "MUSE-" + found.group(1), "+" + digits
    with transaction(conn) as c:
        rows = c.execute("SELECT w.account_id, w.code_sha256, a.lang FROM whatsapp_codes w "
                         "JOIN accounts a ON a.id = w.account_id WHERE w.number = ? AND "
                         "w.expires_ts > ? AND a.whatsapp_number = w.number AND "
                         "a.status = 'active' AND a.is_demo = 0",
                         (number, now)).fetchall()
        match = next((r for r in rows if hmac.compare_digest(
            r[1], _code_hash(r[0], number, code))), None)
        if match is None:
            return None
        account_id = str(match[0])
        others = tuple(str(r[0]) for r in c.execute(
            "SELECT id FROM accounts WHERE whatsapp_number = ? AND whatsapp_verified_ts > 0 "
            "AND id != ?", (number, account_id)).fetchall())
        for other in others:
            c.execute("UPDATE accounts SET whatsapp_number = '', notify_whatsapp = 0, "
                      "whatsapp_verified_ts = 0 WHERE id = ?", (other,))
        c.execute("UPDATE accounts SET whatsapp_verified_ts = ? WHERE id = ?",
                  (now, account_id))
        c.execute("DELETE FROM whatsapp_codes WHERE account_id = ? OR expires_ts < ?",
                  (account_id, now))
    return Verified(account_id, str(match[2] or "en"), others)


def account_by_sender(conn: sqlite3.Connection, sender: str) -> Linked | None:
    """The active, non-demo account that verified this number (``from`` of a webhook);
    a pending (unverified) claim never counts."""
    digits = re.sub(r"[^0-9]", "", sender or "")
    if not DIGITS.fullmatch(digits):
        return None
    with _locked(conn):
        row = conn.execute("SELECT id, whatsapp_number, lang FROM accounts WHERE "
                           "whatsapp_number = ? AND whatsapp_verified_ts > 0 AND "
                           "status = 'active' AND is_demo = 0 "
                           "ORDER BY created_ts LIMIT 1", ("+" + digits,)).fetchone()
    return Linked(row[0], row[1], row[2] or "en") if row else None


def _locked(conn: Any) -> Any:
    return getattr(conn, "lock", None) or threading.RLock()


# -- the notification channel -------------------------------------------------------------


_OPTED_IN = ("SELECT id, whatsapp_number, lang FROM accounts WHERE status = 'active' AND "
             "is_demo = 0 AND notify_whatsapp = 1 AND whatsapp_number != '' AND "
             "whatsapp_verified_ts > 0")
_RECIPIENTS = {"campaigns": _OPTED_IN + " AND notify_campaigns = 1",
               "leads": _OPTED_IN + " AND notify_leads = 1"}


class Channel:
    """WhatsApp next to e-mail in ``studio.notify``: the same events, to the accounts that
    opted in with a number and kept the category on. Runs on the notifier's worker."""

    def __init__(self, conn: sqlite3.Connection, client: Client) -> None:
        self.conn = conn
        self.client = client

    def recipients(self, category: str) -> list[Linked]:
        sql = _RECIPIENTS.get(category)
        if sql is None:
            return []
        with _locked(self.conn):
            rows = self.conn.execute(sql).fetchall()
        return [Linked(r[0], r[1], r[2] or "en") for r in rows]

    def latest_pending_approval(self) -> str:
        with _locked(self.conn):
            row = self.conn.execute("SELECT id FROM approvals WHERE status = 'pending' AND "
                                    "superseded_by = '' ORDER BY ts DESC LIMIT 1").fetchone()
        return str(row[0]) if row else ""

    def deliver(self, name: str, category: str, count: int, *,
                due: Callable[[Linked, str, int], int], coalesce: bool) -> int:
        """Send ``name`` to every opted-in account; the number of sends attempted."""
        t = TEMPLATES.get(name)
        if t is None:
            return 0
        ref = self.latest_pending_approval() if t.button == "dynamic" else ""
        if t.button == "dynamic" and not ref:
            return 0
        sent = 0
        for who in self.recipients(category):
            n = count
            if coalesce:
                n = due(who, "whatsapp." + name, count)
                if not n:
                    continue
            try:
                self.client.send_template(who.number, t, who.lang, count=n, ref=ref,
                                          account=who.account_id)
            except WhatsAppError:
                pass  # receipted by the client
            sent += 1
        return sent


# -- inbound -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Inbound:
    message_id: str
    sender: str  # digits, as the webhook gives it
    kind: str  # "audio", "text", or another WhatsApp type
    ts: float
    text: str = ""
    media_id: str = ""


def messages(event: Any, phone_id: str) -> list[Inbound]:
    """The user messages of a ``whatsapp_business_account`` webhook for our number;
    statuses and anything malformed are skipped."""
    out: list[Inbound] = []
    if not isinstance(event, Mapping) or event.get("object") != "whatsapp_business_account":
        return out
    for entry in event.get("entry") or []:
        if not isinstance(entry, Mapping):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, Mapping) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, Mapping):
                continue
            meta = value.get("metadata") or {}
            if not isinstance(meta, Mapping) or str(meta.get("phone_number_id", "")) != phone_id:
                continue
            for m in value.get("messages") or []:
                if not isinstance(m, Mapping):
                    continue
                mid, sender, kind = str(m.get("id", "")), str(m.get("from", "")), \
                    str(m.get("type", ""))
                if not WAMID.fullmatch(mid) or not DIGITS.fullmatch(sender):
                    continue
                try:
                    ts = float(m.get("timestamp", 0))
                except (TypeError, ValueError):
                    ts = 0.0
                text, media = "", ""
                if kind == "text" and isinstance(m.get("text"), Mapping):
                    text = str(m["text"].get("body", ""))[:MAX_TEXT_BRIEF + 1]
                if kind in AUDIO_TYPES and isinstance(m.get(kind), Mapping):
                    media = str(m[kind].get("id", ""))
                out.append(Inbound(mid, sender, kind[:20], ts, text, media))
    return out


class SenderLimiter:
    """At most ``RATE_MAX`` messages per sender per ``RATE_WINDOW_S`` (process memory)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._seen.setdefault(key, deque())
            while q and now - q[0] > RATE_WINDOW_S:
                q.popleft()
            if len(q) >= RATE_MAX:
                return False
            q.append(now)
            if len(self._seen) > 10_000:  # forget idle senders
                for k in [k for k, v in self._seen.items() if not v]:
                    del self._seen[k]
            return True


@dataclass(frozen=True)
class BriefResult:
    brief_id: str
    facts: int
    questions: int


# (account, inbound, audio bytes or None) -> the stored draft
BriefMaker = Callable[[Linked, Inbound, bytes | None], BriefResult]


class Inbox:
    """The webhook's work: dedupe, rate-limit, receipt, then one worker thread does the
    slow part (download, transcribe, draft, reply) so Meta gets its 200 at once."""

    def __init__(self, conn: sqlite3.Connection, ledger: Ledger, client: Client,
                 make_brief: BriefMaker, reply: Callable[[Linked, str, dict[str, Any]], str],
                 *, clock: Callable[[], float] = time.time,
                 accepts_work: Callable[[], str] = lambda: "") -> None:
        self.conn = conn
        self.ledger = ledger
        self.client = client
        self.make_brief = make_brief
        self.reply = reply  # (account, key, values) -> the text in the account's language
        self.clock = clock
        self.accepts_work = accepts_work  # "" or the reason no work is done (read only…)
        self.limiter = SenderLimiter(clock)
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start = threading.Lock()

    @property
    def key(self) -> str:
        return self.client.cfg.app_secret

    def _first_time(self, message_id: str) -> bool:
        digest = sha256_text(message_id)
        try:
            with transaction(self.conn) as c:
                c.execute("INSERT INTO whatsapp_inbound (message_sha256, ts) VALUES (?, ?)",
                          (digest, self.clock()))
                c.execute("DELETE FROM whatsapp_inbound WHERE ts < ?",
                          (self.clock() - 30 * 86400,))
        except sqlite3.IntegrityError:
            return False
        return True

    def _receipt(self, m: Inbound, outcome: str, account: str = "", **extra: Any) -> None:
        try:
            self.ledger.append(RUN_ID, agent=AGENT, kind="tool_call", details={
                "tool": "whatsapp.inbound", "status": outcome, "side_effects": "local",
                "message_type": m.kind, "account": account,
                "sender_sha256": number_hash(m.sender, self.key),
                "message_sha256": sha256_text(m.message_id), **extra})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("whatsapp inbound receipt failed: %s", type(exc).__name__)

    def handle(self, event: Any) -> int:
        """Every new message of a verified webhook; the number queued for work."""
        queued = 0
        for m in messages(event, self.client.cfg.phone_id):
            if not self._first_time(m.message_id):
                continue  # a retry of a message already handled
            if m.kind == "text" and CODE_RE.search(m.text):
                self._verify(m)
                continue
            who = account_by_sender(self.conn, m.sender)
            if who is None:
                self._receipt(m, "ignored_unknown_sender")
                continue
            if not self.limiter.allow(number_hash(m.sender, self.key)):
                self._receipt(m, "rate_limited", who.account_id)
                continue
            if m.kind not in (*AUDIO_TYPES, "text") or (m.kind == "text" and not m.text.strip()):
                self._receipt(m, "ignored_type", who.account_id)
                continue
            refused = self.accepts_work()
            if refused:
                self._receipt(m, "refused", who.account_id, error=refused)
                continue
            self._receipt(m, "accepted", who.account_id)
            self._submit(partial(self._work, who, m))
            queued += 1
        return queued

    def _verify(self, m: Inbound) -> None:
        """A verification code: link the sender's number to the code's account."""
        if not self.limiter.allow(number_hash(m.sender, self.key)):
            self._receipt(m, "rate_limited")
            return
        got = verify_code(self.conn, m.sender, m.text, now=self.clock())
        if got is None:
            self._receipt(m, "verification_failed")  # no reply: the number proved nothing
            return
        self._receipt(m, "verified", got.account_id)
        for other in got.unlinked:
            self._receipt(m, "unlinked", other)
        who = Linked(got.account_id, "+" + re.sub(r"[^0-9]", "", m.sender), got.lang)
        self._submit(partial(self._answer, who, m, "whatsapp.reply.verified", {}))

    def _work(self, who: Linked, m: Inbound) -> None:
        audio: bytes | None = None
        try:
            if m.kind in AUDIO_TYPES:
                audio, _ = self.client.download_media(m.media_id)
            elif len(m.text) > MAX_TEXT_BRIEF:
                raise WhatsAppError("brief_too_long")
            result = self.make_brief(who, m, audio)
        except Exception as exc:  # noqa: BLE001 - told to the owner, receipted, never raised
            code = str(getattr(exc, "code", "") or type(exc).__name__)[:60]
            self._receipt(m, "brief_failed", who.account_id, error=code)
            self._answer(who, m, "whatsapp.reply.failed", {"code": code})
            return
        self._receipt(m, "brief_drafted", who.account_id, brief_id=result.brief_id,
                      facts=result.facts, questions=result.questions)
        self._answer(who, m, "whatsapp.reply.ready", {
            "facts": result.facts, "questions": result.questions,
            "brief_id": result.brief_id})

    def _answer(self, who: Linked, m: Inbound, key: str, values: dict[str, Any]) -> None:
        if m.ts and self.clock() - m.ts > WINDOW_S:
            return  # outside the 24 h window: a free-form reply is not allowed
        try:
            self.client.send_text(who.number, self.reply(who, key, values),
                                  account=who.account_id, lang=who.lang)
        except WhatsAppError:
            pass  # receipted by the client

    # -- worker ----------------------------------------------------------------------

    def _submit(self, job: Callable[[], None]) -> None:
        with self._start:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="whatsapp",
                                                daemon=True)
                self._thread.start()
        self._queue.put(job)

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("whatsapp job failed: %s", type(exc).__name__)

    def flush(self, timeout: float = 30.0) -> None:
        if self._thread is None:
            return
        done = threading.Event()
        self._queue.put(done.set)
        done.wait(timeout)


def iter_templates(base_url: str) -> Iterable[dict[str, Any]]:
    for t in TEMPLATES.values():
        for lang in ("pl", "en"):
            yield template_definition(t, lang, base_url)


__all__ = ["Channel", "Client", "GraphTransport", "Inbox", "MockTransport", "TEMPLATES",
           "WhatsAppConfig", "WhatsAppError", "account_by_sender", "check_challenge",
           "ensure_schema", "normalize_number", "number_hash", "set_preferences",
           "template_message", "verify_signature"]
