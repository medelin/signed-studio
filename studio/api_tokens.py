"""Personal access tokens: how an owner's own AI assistant (or a script) reaches
``/api/v1`` and the remote MCP endpoint ``/mcp``.

- **Per account, made by the owner**: created and revoked on ``/account`` ("Assistants &
  API"), never through the API itself. At most ``MAX_ACTIVE`` live tokens per account.
- **Shown once, stored as a keyed hash**: the value is ``msl_<id>_<secret>`` (the id is
  public, the secret 256 random bits); the database keeps only
  ``HMAC-SHA256(k, value)`` with ``k`` derived from the ledger key (HKDF, like the sealed
  second-factor secrets in ``studio.accounts``). A stolen database does not yield tokens,
  and comparison is constant-time.
- **Scopes**: ``read`` and ``draft`` only. There is no ``sign`` scope and none can be
  stored (``SCOPES`` is closed, the table has a ``CHECK``): signing stays a human tap in
  the browser.
- **Expiry** (default 30 days, at most 90), **revocation**, **last used** time, a
  **label** (the receipt agent ``api:<label>``). A token is bound to its account's
  ``credential_gen`` when it is made: a password change or reset, a two-step change, a
  disable, deletion or demo-password rotation bumps it and kills every token of the
  account on its next request; "sign out other sessions" revokes them (``revoke_all``).
- **Not for the shared demo account** (``judge``): no token can be made for it, and one
  that exists is refused. Making a token needs the current password (and the two-step
  code when it is on), checked in ``routes_api``.
- **Never logged**: the value exists only in the response that shows it once; receipts
  and logs name a token by its id.
"""

from __future__ import annotations

import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from studio.approvals import transaction
from studio.receipts import is_opaque

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,                 -- public part of the value (tok_<hex>)
    account_id TEXT NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    scopes TEXT NOT NULL CHECK (scopes IN ('read', 'read draft')),
    token_hmac TEXT NOT NULL,            -- HMAC-SHA256 of the whole value; never the value
    credential_gen INTEGER NOT NULL DEFAULT -1,  -- the account's when made; must still match
    created_ts REAL NOT NULL,
    expires_ts REAL NOT NULL,
    last_used_ts REAL,
    revoked_ts REAL
);
CREATE INDEX IF NOT EXISTS api_tokens_account ON api_tokens (account_id);
-- a brief drafted through /api/v1 or /mcp belongs to the account whose token drafted it
-- (with its run, once started); ``confirmation`` holds the checked resolutions
CREATE TABLE IF NOT EXISTS api_briefs (
    brief_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    created_ts REAL NOT NULL,
    confirmation TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_briefs_account ON api_briefs (account_id);
"""

PREFIX = "msl_"
SCOPES = ("read", "draft")  # closed: never "sign"
SCOPE_SETS = {"read": ("read",), "draft": ("read", "draft")}  # the two choices on /account
DEFAULT_DAYS = 30
DAY_CHOICES = (7, 30, 90)
MAX_ACTIVE = 10
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")
TOKEN = re.compile(r"msl_(tok_[0-9a-f]{12})_[A-Za-z0-9_-]{43}")
TOUCH_EVERY_S = 30.0
_INFO = b"muse-studio-lite/api-token/v1"


class TokenError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Token:
    id: str
    account_id: str
    label: str
    scopes: tuple[str, ...]
    created_ts: float
    expires_ts: float
    last_used_ts: float | None
    revoked_ts: float | None

    @property
    def agent(self) -> str:
        """The receipt agent: ``api:<label>`` folded to an opaque id."""
        return agent_for(self.label)

    def active(self, now: float | None = None) -> bool:
        return self.revoked_ts is None and self.expires_ts > (time.time() if now is None
                                                              else now)

    def view(self) -> dict[str, Any]:
        """What ``/account`` and the API show of a token: never its value or hash."""
        return {"id": self.id, "label": self.label, "scopes": list(self.scopes),
                "created_ts": self.created_ts, "expires_ts": self.expires_ts,
                "last_used_ts": self.last_used_ts, "revoked_ts": self.revoked_ts,
                "active": self.active()}


def agent_for(label: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", label or "").strip("-.")[:60]
    agent = "api:" + (name or "token")
    return agent if is_opaque(agent) else "api:token"


def derive_key(ledger_key: Ed25519PrivateKey) -> bytes:
    raw = ledger_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return HKDF(hashes.SHA256(), 32, salt=None, info=_INFO).derive(raw)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {row[1] for row in conn.execute("PRAGMA table_info(api_tokens)")}
    if "credential_gen" not in have:  # a table made before the binding: those tokens die
        conn.execute("ALTER TABLE api_tokens ADD COLUMN credential_gen INTEGER NOT NULL "
                     "DEFAULT -1")


_COLUMNS = ("id, account_id, label, scopes, created_ts, expires_ts, last_used_ts, "
            "revoked_ts")


def _row(row: tuple[Any, ...]) -> Token:
    tid, aid, label, scopes, created, expires, used, revoked = row
    return Token(tid, aid, label, tuple(str(scopes).split()), created, expires, used,
                 revoked)


class Tokens:
    """The token store on the studio database, keyed by ``key`` (``derive_key``)."""

    def __init__(self, conn: Any, key: bytes, clock: Any = time.time) -> None:
        if len(key) < 32:
            raise ValueError("token key too short")
        self.conn = conn
        self._key = key
        self.clock = clock

    def _mac(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), sha256).hexdigest()

    def create(self, account_id: str, label: str, scope: str = "read",
               days: int = DEFAULT_DAYS) -> tuple[Token, str]:
        """A new token and its value (shown once, never stored)."""
        label = " ".join(str(label or "").split())
        if not LABEL.fullmatch(label):
            raise TokenError("token_label")
        if scope not in SCOPE_SETS:
            raise TokenError("token_scope")
        if days not in DAY_CHOICES:
            raise TokenError("token_expiry")
        now = self.clock()
        tid = "tok_" + secrets.token_hex(6)
        value = f"{PREFIX}{tid}_{secrets.token_urlsafe(32)}"
        with transaction(self.conn) as c:
            acct = c.execute("SELECT status, is_demo, credential_gen FROM accounts WHERE "
                             "id = ?", (account_id,)).fetchone()
            if acct is None or acct[0] != "active":
                raise TokenError("unknown_token")
            if acct[1]:
                raise TokenError("token_demo")
            live = c.execute("SELECT count(*) FROM api_tokens WHERE account_id = ? AND "
                             "revoked_ts IS NULL AND expires_ts > ?",
                             (account_id, now)).fetchone()[0]
            if live >= MAX_ACTIVE:
                raise TokenError("token_limit")
            c.execute("INSERT INTO api_tokens (id, account_id, label, scopes, token_hmac, "
                      "credential_gen, created_ts, expires_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (tid, account_id, label, " ".join(SCOPE_SETS[scope]), self._mac(value),
                       int(acct[2]), now, now + days * 86400.0))
        token = self.get(account_id, tid)
        assert token is not None
        return token, value

    def get(self, account_id: str, token_id: str) -> Token | None:
        with self.conn.lock:
            row = self.conn.execute(f"SELECT {_COLUMNS} FROM api_tokens "  # noqa: S608
                                    "WHERE id = ? AND account_id = ?",
                                    (token_id, account_id)).fetchone()
        return _row(row) if row else None

    def listing(self, account_id: str) -> list[Token]:
        with self.conn.lock:
            rows = self.conn.execute(f"SELECT {_COLUMNS} FROM api_tokens "  # noqa: S608
                                     "WHERE account_id = ? ORDER BY created_ts DESC LIMIT 50",
                                     (account_id,)).fetchall()
        return [_row(r) for r in rows]

    def revoke(self, account_id: str, token_id: str) -> bool:
        """Revoke one of the account's own tokens; False for anyone else's or unknown."""
        with transaction(self.conn) as c:
            cur = c.execute("UPDATE api_tokens SET revoked_ts = ? WHERE id = ? AND "
                            "account_id = ? AND revoked_ts IS NULL",
                            (self.clock(), token_id, account_id))
        return cur.rowcount == 1

    def revoke_all(self, account_id: str) -> int:
        """Revoke every live token of the account ("sign out other sessions")."""
        with transaction(self.conn) as c:
            cur = c.execute("UPDATE api_tokens SET revoked_ts = ? WHERE account_id = ? AND "
                            "revoked_ts IS NULL", (self.clock(), account_id))
        return int(cur.rowcount)

    def verify(self, value: str | None) -> Token | None:
        """The live token for a presented value: right shape, known id, matching keyed
        hash, not revoked, not expired, its account active, not the demo account, and
        the account's credentials unchanged since it was made. ``None`` otherwise."""
        m = TOKEN.fullmatch(value or "")
        if m is None:
            return None
        now = self.clock()
        with self.conn.lock:
            row = self.conn.execute(
                "SELECT t.id, t.account_id, t.label, t.scopes, t.created_ts, t.expires_ts, "
                "t.last_used_ts, t.revoked_ts, t.token_hmac, a.status, a.is_demo, "
                "t.credential_gen, a.credential_gen "
                "FROM api_tokens t JOIN accounts a ON "
                "a.id = t.account_id WHERE t.id = ?", (m.group(1),)).fetchone()
        if row is None:
            self._mac(value or "")  # the same work whether or not the id exists
            return None
        token, digest, status = _row(row[:8]), row[8], row[9]
        if not hmac.compare_digest(digest, self._mac(value or "")):
            return None
        if status != "active" or row[10] or row[11] != row[12] or not token.active(now):
            return None
        if token.last_used_ts is None or now - token.last_used_ts >= TOUCH_EVERY_S:
            with transaction(self.conn) as c:
                c.execute("UPDATE api_tokens SET last_used_ts = ? WHERE id = ?",
                          (now, token.id))
            token = replace(token, last_used_ts=now)
        return token


def bearer(headers: Any) -> str | None:
    """The token in ``Authorization: Bearer <token>``, or ``None``. Cookies never count."""
    raw = headers.get("authorization") if headers is not None else None
    if not raw or not raw[:7].lower() == "bearer ":
        return None
    return str(raw[7:].strip()) or None
