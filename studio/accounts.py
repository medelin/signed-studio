"""User accounts: argon2id passwords, profile, TOTP second factor, sessions, deletion, export.

- **No self-registration.** The operator creates accounts with ``studio account create``.
  The built-in ``judge`` account is the *demo* account: it is seeded (or its hash updated)
  from ``JUDGE_PASSWORD`` at every start and disabled when that variable is empty. Its
  password, second factor and existence are not changeable from the UI, so judges sharing
  it can never lock each other out.
- **Passwords** are hashed with argon2id (``argon2-cffi`` defaults, RFC 9106 low-memory
  profile); a login for an unknown user name verifies against a dummy hash, so the work
  (and the generic failure) is the same whether or not the account exists.
- **Second factor**: RFC 6238 TOTP (SHA-1, 6 digits, 30 s, ±1 step). The secret is sealed
  with AES-GCM under a key derived from the ledger key (HKDF), never stored in the clear; the
  last accepted step is stored, so a code is accepted once. Ten recovery codes (80 bits each)
  are shown once and stored as SHA-256 hashes; each works once.
- **Sessions** stay HMAC cookies (``studio.app.Sessions``); ``AccountSessions`` adds a row
  per session (hash of the session id, account, created, last seen, a short user-agent
  label) and refuses a cookie whose row is revoked or whose account is not active. So a
  password change, ``studio account disable`` (another process) or a deletion takes effect
  on the next request. Approver keys are bound to the session hash, so revoking a session
  revokes its keys.
- **Deletion** erases every personal field and the second factor. The account row stays as
  a tombstone with its random id: receipts are append-only and hash-chained, and they name
  the approver only by key fingerprint, which now leads to that pseudonymous id and nothing
  else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import sqlite3
import struct
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from studio.approvals import transaction
from studio.receipts import sha256_hex

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,                -- random, opaque; the pseudonym after deletion
    username TEXT UNIQUE,               -- NULL after deletion
    password_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deleted')),
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    lang TEXT NOT NULL DEFAULT '',
    theme TEXT NOT NULL DEFAULT '',
    palette TEXT NOT NULL DEFAULT '',
    advertiser_type TEXT NOT NULL DEFAULT 'person'
        CHECK (advertiser_type IN ('person', 'company')),
    legal_name TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    tax_id TEXT NOT NULL DEFAULT '',
    totp_secret TEXT NOT NULL DEFAULT '',   -- sealed (AES-GCM); '' = second factor off
    totp_pending TEXT NOT NULL DEFAULT '',  -- sealed; an enrolment not yet confirmed
    totp_last_step INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    password_changed_ts REAL NOT NULL DEFAULT 0,
    deleted_ts REAL,
    credential_gen INTEGER NOT NULL DEFAULT 0,  -- bumped by every password/2FA/status change
    code_failures INTEGER NOT NULL DEFAULT 0,   -- wrong codes since the last right one
    code_locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recovery_codes (
    account_id TEXT NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    code_sha256 TEXT NOT NULL,
    used_ts REAL,
    PRIMARY KEY (account_id, code_sha256)
);
CREATE TABLE IF NOT EXISTS account_sessions (
    sid_sha256 TEXT PRIMARY KEY,        -- = keys.session_sha256 of the keys it registered
    account_id TEXT NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    created_ts REAL NOT NULL,
    last_seen_ts REAL NOT NULL,
    expires_ts INTEGER NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',  -- short label ("Firefox · Linux"), never the raw header
    revoked_ts REAL
);
CREATE INDEX IF NOT EXISTS account_sessions_account ON account_sessions (account_id);
"""

DEMO_USER = "judge"
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 256
RECOVERY_CODES = 10
TOTP_PERIOD_S = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1  # steps either side of now (clock skew)
TOUCH_EVERY_S = 60.0  # last_seen is written at most this often per session
# Wrong second-factor codes per account, whatever the client or challenge: after
# FREE_CODE_FAILURES, every further failure locks code checks for BASE * 2**(n - FREE) s,
# capped. A right code resets the count. (The judge demo account has no second factor.)
FREE_CODE_FAILURES = 5
CODE_LOCK_BASE_S = 30.0
CODE_LOCK_MAX_S = 3600.0
ISSUER = "Muse Studio Lite"
ADVERTISER_TYPES = ("person", "company")
LIMITS = {"display_name": 80, "email": 254, "legal_name": 160, "country": 80, "tax_id": 40}
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+")
_SECRET_INFO = b"muse-studio-lite/totp-secret/v1"

# A short list of the passwords tried first (leaked-password top lists); the minimum length
# already rules out most of them. Compared case-insensitively.
COMMON_PASSWORDS = frozenset("""
123456789012 1234567890123 12345678901234 111111111111 000000000000 123123123123
password1234 password12345 passwordpassword qwertyuiop12 qwertyuiopasdf qwerty123456
1q2w3e4r5t6y 1qaz2wsx3edc zaq12wsxcde3 abcdefghijkl abc123abc123 iloveyou1234
letmein12345 welcome12345 administrator admin1234567 changeme1234 trustno1trustno1
football1234 baseball1234 superman1234 starwars1234 sunshine1234 princess1234
monkey123456 dragon123456 michael12345 jennifer1234 whatever1234 computer1234
internet1234 samsung12345 qwertyqwerty asdfghjkl123 zxcvbnm12345 passw0rd1234
p@ssw0rd1234 p@ssword1234 haslo1234567 haslohaslo12 zaq1@wsx3edc polska123456
kochamcie123 qwerty12345678 1234qwerasdf q1w2e3r4t5y6 mustang12345 shadow123456
master123456 hello1234567 freedom12345 secret123456 museStudio12 musestudio123
""".lower().split())


class AccountError(Exception):
    code = "account_error"


class InvalidUsername(AccountError):
    code = "invalid_username"


class UsernameTaken(AccountError):
    code = "username_taken"


class WeakPassword(AccountError):
    code = "weak_password"


class CommonPassword(AccountError):
    code = "common_password"


class PasswordMismatch(AccountError):
    code = "password_mismatch"


class WrongPassword(AccountError):
    code = "wrong_password"


class WrongCode(AccountError):
    code = "wrong_code"


class InvalidProfile(AccountError):
    code = "invalid_profile"


class DemoAccount(AccountError):
    code = "demo_account"


class NoSuchAccount(AccountError):
    code = "no_such_account"


class ConfirmationMismatch(AccountError):
    code = "confirmation_mismatch"


class TwoFactorState(AccountError):
    code = "two_factor_state"


class SecondFactorLocked(AccountError):
    code = "second_factor_locked"


@dataclass(frozen=True)
class Account:
    id: str
    username: str
    status: str
    is_demo: bool
    display_name: str
    email: str
    lang: str
    theme: str
    palette: str
    advertiser_type: str
    legal_name: str
    country: str
    tax_id: str
    totp_enabled: bool
    totp_pending: bool
    created_ts: float
    updated_ts: float
    password_changed_ts: float

    PROFILE = ("display_name", "email", "lang", "theme", "palette", "advertiser_type",
               "legal_name", "country", "tax_id")

    def profile(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.PROFILE}


_COLUMNS = ("id, username, status, is_demo, display_name, email, lang, theme, palette, "
            "advertiser_type, legal_name, country, tax_id, totp_secret != '', "
            "totp_pending != '', created_ts, updated_ts, password_changed_ts")


def _account(row: tuple[Any, ...]) -> Account:
    (aid, username, status, demo, display, email, lang, theme, palette, kind, legal, country,
     tax, totp, pending, created, updated, changed) = row
    return Account(aid, username or "", status, bool(demo), display, email, lang, theme,
                   palette, kind, legal, country, tax, bool(totp), bool(pending), created,
                   updated, changed)


# Columns added after the accounts table first shipped (``ALTER TABLE … ADD COLUMN``).
_LATER_COLUMNS = {"credential_gen": "INTEGER NOT NULL DEFAULT 0",
                  "code_failures": "INTEGER NOT NULL DEFAULT 0",
                  "code_locked_until": "REAL NOT NULL DEFAULT 0"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {row[1] for row in conn.execute("PRAGMA table_info(accounts)")}
    for name, ddl in _LATER_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {ddl}")


# -- passwords --------------------------------------------------------------------------

_HASHER = PasswordHasher()  # argon2id, t=3, m=64 MiB, p=4
_DUMMY: list[str] = []
_DUMMY_LOCK = threading.Lock()


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return bool(_HASHER.verify(stored, password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _dummy_hash() -> str:
    with _DUMMY_LOCK:
        if not _DUMMY:
            _DUMMY.append(hash_password(secrets.token_urlsafe(24)))
        return _DUMMY[0]


def check_password_policy(password: str, username: str = "") -> None:
    """Minimum length, maximum length, not a common password, not the user name."""
    if len(password) < MIN_PASSWORD_LEN or len(password) > MAX_PASSWORD_LEN:
        raise WeakPassword(f"passwords are {MIN_PASSWORD_LEN} to {MAX_PASSWORD_LEN} characters")
    if any(unicodedata.category(c) == "Cc" for c in password):
        raise WeakPassword("control characters are not allowed in passwords")
    folded = password.casefold()
    if (folded in COMMON_PASSWORDS or len(set(folded)) < 4
            or (username and username.casefold() in folded)):
        raise CommonPassword("this password is too easy to guess")


def normalize_username(value: str) -> str:
    return value.strip().lower()


# -- TOTP (RFC 6238) --------------------------------------------------------------------


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp_code(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** TOTP_DIGITS:0{TOTP_DIGITS}d}"


def totp_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // TOTP_PERIOD_S)


def match_totp(secret_b32: str, code: str, *, last_step: int, now: float | None = None
               ) -> int | None:
    """The step ``code`` belongs to, if it is within the window and newer than ``last_step``
    (a code is accepted once); every candidate is compared, in constant time."""
    if not re.fullmatch(rf"\d{{{TOTP_DIGITS}}}", code):
        return None
    current = totp_step(now)
    found: int | None = None
    for step in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp_code(secret_b32, step).encode(), code.encode()) \
                and step > last_step:
            found = step
    return found


def provisioning_uri(secret_b32: str, username: str) -> str:
    label = quote(f"{ISSUER}:{username}")
    return (f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_S}")


def qr_svg(data: str, label: str) -> str:
    """The QR code as inline SVG (server side, no external service): dark modules on a
    light square, whatever the page theme, so phone cameras read it."""
    import segno
    from markupsafe import escape

    svg = segno.make(data, error="m").svg_inline(scale=5, dark="#000", light="#fff",
                                                 svgclass="qr", lineclass=None)
    return svg.replace("<svg ", f'<svg role="img" aria-label="{escape(label)}" ', 1)


def format_secret(secret_b32: str) -> str:
    return " ".join(secret_b32[i:i + 4] for i in range(0, len(secret_b32), 4))


def _new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(10)).decode()  # 80 bits, 16 characters
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _recovery_hash(account_id: str, code: str) -> str:
    norm = re.sub(r"[\s-]", "", code).upper()
    return sha256_hex(f"recovery|{account_id}|{norm}")


class SecretBox:
    """AES-GCM for TOTP secrets, keyed from the ledger key (HKDF-SHA256), bound to the
    account id. ``None`` key (the operator CLI): sealing and opening are refused."""

    def __init__(self, ledger_key: Ed25519PrivateKey | None) -> None:
        self._aead: AESGCM | None = None
        if ledger_key is not None:
            raw = ledger_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            key = HKDF(hashes.SHA256(), 32, salt=None, info=_SECRET_INFO).derive(raw)
            self._aead = AESGCM(key)

    def seal(self, account_id: str, secret: str) -> str:
        if self._aead is None:
            raise RuntimeError("no key to seal second-factor secrets")
        nonce = secrets.token_bytes(12)
        ct = self._aead.encrypt(nonce, secret.encode(), account_id.encode())
        return "v1:" + base64.urlsafe_b64encode(nonce + ct).decode()

    def open(self, account_id: str, sealed: str) -> str | None:
        if self._aead is None or not sealed.startswith("v1:"):
            return None
        try:
            blob = base64.urlsafe_b64decode(sealed[3:])
            return self._aead.decrypt(blob[:12], blob[12:], account_id.encode()).decode()
        except (InvalidTag, ValueError):
            return None


def short_agent(header: str) -> str:
    """"Firefox · Linux": browser family and platform only, never the raw header."""
    ua = header[:512]
    browser = next((name for token, name in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
        ("Safari/", "Safari"), ("curl/", "curl"), ("python-httpx", "httpx"),
        ("testclient", "test client")) if token in ua), "")
    platform = next((name for token, name in (
        ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iPadOS"), ("Windows", "Windows"),
        ("Mac OS X", "macOS"), ("Linux", "Linux")) if token in ua), "")
    return " · ".join(p for p in (browser, platform) if p) or "?"


# -- sessions ---------------------------------------------------------------------------


class BaseSessions(Protocol):
    def issue(self, now: float | None = None) -> str: ...

    def check(self, value: str | None, now: float | None = None) -> str | None: ...

    def revoke(self, value: str | None) -> None: ...


@dataclass(frozen=True)
class SessionRow:
    ref: str  # opaque handle for the page (hash of the session hash)
    created_ts: float
    last_seen_ts: float
    expires_ts: int
    user_agent: str
    current: bool


def session_ref(sid_sha256: str) -> str:
    return sha256_hex("ref|" + sid_sha256)[:24]


# -- the account store ------------------------------------------------------------------


class Accounts:
    """Every account operation; writes run in one ``BEGIN IMMEDIATE`` transaction."""

    def __init__(self, conn: sqlite3.Connection, box: SecretBox | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.conn = conn
        self.box = box or SecretBox(None)
        self.clock = clock
        self._touched: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        lock = getattr(self.conn, "lock", None)
        if lock is None:
            yield self.conn
            return
        with lock:
            yield self.conn

    # -- lookup ----------------------------------------------------------------------

    def get(self, account_id: str) -> Account | None:
        with self._read() as c:
            row = c.execute(f"SELECT {_COLUMNS} FROM accounts WHERE id = ?",  # noqa: S608
                            (account_id,)).fetchone()
        return _account(row) if row else None

    def by_username(self, username: str) -> Account | None:
        with self._read() as c:
            row = c.execute(f"SELECT {_COLUMNS} FROM accounts WHERE username = ?",  # noqa: S608
                            (normalize_username(username),)).fetchone()
        return _account(row) if row else None

    def listing(self) -> list[Account]:
        with self._read() as c:
            rows = c.execute(f"SELECT {_COLUMNS} FROM accounts "  # noqa: S608
                             "WHERE status != 'deleted' ORDER BY username").fetchall()
        return [_account(r) for r in rows]

    def any_login(self) -> bool:
        """An active account other than the demo one exists (sign-in stays open for it)."""
        with self._read() as c:
            return c.execute("SELECT 1 FROM accounts WHERE status = 'active' AND is_demo = 0 "
                             "LIMIT 1").fetchone() is not None

    def _require(self, c: sqlite3.Connection, account_id: str) -> tuple[Any, ...]:
        row = c.execute("SELECT username, password_hash, status, is_demo, totp_secret, "
                        "totp_pending, totp_last_step FROM accounts WHERE id = ?",
                        (account_id,)).fetchone()
        if row is None or row[2] == "deleted":
            raise NoSuchAccount("no such account")
        return tuple(row)

    # -- creation, demo seeding, operator actions ---------------------------------------

    def create(self, username: str, password: str, *, display_name: str = "",
               email: str = "", demo: bool = False) -> Account:
        name = normalize_username(username)
        if not USERNAME.fullmatch(name):
            raise InvalidUsername("user names are 2-32 of a-z, 0-9, '.', '_', '-'")
        if name == DEMO_USER and not demo:
            raise UsernameTaken(f"{DEMO_USER!r} is the built-in demo account")
        if not demo:
            check_password_policy(password, name)
        profile = _clean_profile({"display_name": display_name, "email": email})
        now = self.clock()
        aid = "acct_" + secrets.token_hex(8)
        digest = hash_password(password)
        with transaction(self.conn) as c:
            try:
                c.execute("INSERT INTO accounts (id, username, password_hash, is_demo, "
                          "display_name, email, created_ts, updated_ts, password_changed_ts) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (aid, name, digest, int(demo), profile["display_name"],
                           profile["email"], now, now, now))
            except sqlite3.IntegrityError as exc:
                raise UsernameTaken("that user name is taken") from exc
        account = self.get(aid)
        assert account is not None
        return account

    def seed_demo(self, password: str) -> Account | None:
        """The judge demo account follows ``JUDGE_PASSWORD``: created or re-hashed when it is
        set (the hash is replaced only when the password changed), disabled when empty. Its
        second factor stays off."""
        current = self.by_username(DEMO_USER)
        now = self.clock()
        if not password:
            if current is not None and current.status == "active":
                with transaction(self.conn) as c:
                    c.execute("UPDATE accounts SET status = 'disabled', updated_ts = ? "
                              "WHERE id = ?", (now, current.id))
            return None
        if current is None:
            return self.create(DEMO_USER, password, display_name="Judge", demo=True)
        with self._read() as c:
            stored = c.execute("SELECT password_hash FROM accounts WHERE id = ?",
                               (current.id,)).fetchone()[0]
        fresh = "" if verify_password(stored, password) else hash_password(password)
        with transaction(self.conn) as c:
            c.execute("UPDATE accounts SET status = 'active', is_demo = 1, totp_secret = '', "
                      "totp_pending = '', credential_gen = credential_gen + CASE WHEN ? = '' "
                      "THEN 0 ELSE 1 END, password_hash = CASE WHEN ? = '' THEN password_hash "
                      "ELSE ? END, updated_ts = ? WHERE id = ?",
                      (fresh, fresh, fresh, now, current.id))
            c.execute("DELETE FROM recovery_codes WHERE account_id = ?", (current.id,))
        return self.get(current.id)

    def _operator_target(self, username: str) -> Account:
        account = self.by_username(username)
        if account is None or account.status == "deleted":
            raise NoSuchAccount(f"no account {username!r}")
        if account.is_demo:
            raise DemoAccount("the demo account follows JUDGE_PASSWORD; change it there")
        return account

    def set_status(self, username: str, status: str) -> Account:
        if status not in ("active", "disabled"):
            raise ValueError(status)
        account = self._operator_target(username)
        now = self.clock()
        with transaction(self.conn) as c:
            c.execute("UPDATE accounts SET status = ?, credential_gen = credential_gen + 1, "
                      "updated_ts = ? WHERE id = ?", (status, now, account.id))
            if status == "disabled":
                self._revoke_sessions(c, account.id, keep="", now=now)
        result = self.get(account.id)
        assert result is not None
        return result

    def reset_password(self, username: str, password: str, *, clear_2fa: bool = False
                       ) -> Account:
        """Operator reset: new password, every session and key revoked; ``clear_2fa`` also
        turns the second factor off (a user who lost the phone and the codes)."""
        account = self._operator_target(username)
        check_password_policy(password, account.username)
        digest = hash_password(password)
        now = self.clock()
        with transaction(self.conn) as c:
            c.execute("UPDATE accounts SET password_hash = ?, password_changed_ts = ?, "
                      "credential_gen = credential_gen + 1, code_failures = 0, "
                      "code_locked_until = 0, updated_ts = ? WHERE id = ?",
                      (digest, now, now, account.id))
            if clear_2fa:
                self._clear_totp(c, account.id)
            self._revoke_sessions(c, account.id, keep="", now=now)
        result = self.get(account.id)
        assert result is not None
        return result

    # -- authentication -------------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> Account | None:
        """The account for a correct user name and password, if it is active; otherwise
        None. One argon2 verification either way (a dummy hash for unknown names)."""
        name = normalize_username(username)
        row = None
        if USERNAME.fullmatch(name):
            with self._read() as c:
                row = c.execute("SELECT id, password_hash, status FROM accounts "
                                "WHERE username = ?", (name,)).fetchone()
        if len(password) > MAX_PASSWORD_LEN:
            password = password[:MAX_PASSWORD_LEN] + "\x00"  # never matches; same work
        stored = row[1] if row and row[1] else _dummy_hash()
        ok = verify_password(stored, password)
        if not (row and ok and row[2] == "active"):
            return None
        if _HASHER.check_needs_rehash(stored):
            with transaction(self.conn) as c:
                c.execute("UPDATE accounts SET password_hash = ? WHERE id = ?",
                          (hash_password(password), row[0]))
        return self.get(row[0])

    def _check_password(self, stored: str, password: str) -> None:
        if not verify_password(stored or _dummy_hash(), password[:MAX_PASSWORD_LEN + 1]):
            raise WrongPassword("the password is not correct")

    def _check_code(self, c: sqlite3.Connection, account_id: str, sealed: str,
                    last_step: int, code: str) -> None:
        """A TOTP code (once per step) or an unused recovery code; consumed on success."""
        code = code.strip().replace(" ", "")
        if re.fullmatch(r"\d+", code):
            secret = self.box.open(account_id, sealed) if sealed else None
            step = match_totp(secret, code, last_step=last_step, now=self.clock()) \
                if secret else None
            if step is None:
                raise WrongCode("the code is not correct or was used already")
            cur = c.execute("UPDATE accounts SET totp_last_step = ? WHERE id = ? AND "
                            "totp_last_step < ?", (step, account_id, step))
            if cur.rowcount != 1:  # a concurrent request used this step first
                raise WrongCode("the code is not correct or was used already")
            return
        if len(code) > 40:
            raise WrongCode("the code is not correct or was used already")
        cur = c.execute("UPDATE recovery_codes SET used_ts = ? WHERE account_id = ? AND "
                        "code_sha256 = ? AND used_ts IS NULL",
                        (self.clock(), account_id, _recovery_hash(account_id, code)))
        if cur.rowcount != 1:
            raise WrongCode("the code is not correct or was used already")

    def credential_gen(self, account_id: str) -> int:
        """Changes whenever the password, the second factor or the status changes: a pending
        second step opened before that is refused (``verify_second_factor(gen=…)``)."""
        with self._read() as c:
            row = c.execute("SELECT credential_gen FROM accounts WHERE id = ?",
                            (account_id,)).fetchone()
        return int(row[0]) if row else -1

    def _code_gate(self, account_id: str) -> None:
        """Refuse any code check while the account's code lock runs."""
        with self._read() as c:
            row = c.execute("SELECT code_locked_until FROM accounts WHERE id = ?",
                            (account_id,)).fetchone()
        if row and self.clock() < row[0]:
            raise SecondFactorLocked("too many wrong codes; wait and try again")

    def _code_failed(self, account_id: str) -> None:
        """Count a wrong code for the account (own transaction: the failed check rolled
        back) and lock code checks once the free attempts are used up."""
        now = self.clock()
        with transaction(self.conn) as c:
            row = c.execute("SELECT code_failures FROM accounts WHERE id = ?",
                            (account_id,)).fetchone()
            if row is None:
                return
            failures = int(row[0]) + 1
            locked = 0.0
            if failures > FREE_CODE_FAILURES:
                exponent = min(failures - FREE_CODE_FAILURES - 1, 16)
                locked = now + min(CODE_LOCK_BASE_S * 2 ** exponent, CODE_LOCK_MAX_S)
            c.execute("UPDATE accounts SET code_failures = ?, code_locked_until = ? "
                      "WHERE id = ?", (failures, locked, account_id))

    def code_lock_remaining(self, account_id: str) -> float:
        with self._read() as c:
            row = c.execute("SELECT code_locked_until FROM accounts WHERE id = ?",
                            (account_id,)).fetchone()
        return max(0.0, float(row[0]) - self.clock()) if row else 0.0

    def _checked_code(self, c: sqlite3.Connection, account_id: str, sealed: str,
                      last_step: int, code: str) -> None:
        """``_check_code`` that also resets the account's failure count on success."""
        self._check_code(c, account_id, sealed, last_step, code)
        c.execute("UPDATE accounts SET code_failures = 0, code_locked_until = 0 WHERE id = ?",
                  (account_id,))

    def verify_second_factor(self, account_id: str, code: str, gen: int | None = None
                             ) -> bool:
        """The login's second step: True once per TOTP step or recovery code. Refused when
        ``gen`` (the credential generation when the challenge was opened) is stale, the
        account is not active, or its code lock runs; a wrong code counts per account."""
        try:
            self._code_gate(account_id)
            with transaction(self.conn) as c:
                row = self._require(c, account_id)
                current = c.execute("SELECT credential_gen FROM accounts WHERE id = ?",
                                    (account_id,)).fetchone()[0]
                if row[2] != "active" or not row[4] or (gen is not None and gen != current):
                    raise TwoFactorState("the challenge is stale")
                self._checked_code(c, account_id, row[4], row[6], code)
        except WrongCode:
            self._code_failed(account_id)
            return False
        except AccountError:
            return False
        return True

    # -- profile and password ---------------------------------------------------------------

    def update_profile(self, account_id: str, fields: Mapping[str, str]) -> Account:
        profile = _clean_profile(fields)
        now = self.clock()
        with transaction(self.conn) as c:
            self._require(c, account_id)
            sets = ", ".join(f"{name} = ?" for name in profile)
            c.execute(f"UPDATE accounts SET {sets}, updated_ts = ? WHERE id = ?",  # noqa: S608
                      (*profile.values(), now, account_id))
        result = self.get(account_id)
        assert result is not None
        return result

    def change_password(self, account_id: str, current: str, new: str, confirm: str, *,
                        keep_sid: str) -> int:
        """Needs the current password; revokes every other session and its approver keys.
        Returns how many sessions were revoked."""
        with self._read() as c:
            row = self._require(c, account_id)
        if row[3]:
            raise DemoAccount("the demo account's password is set by the operator")
        self._check_password(row[1], current)
        if not hmac.compare_digest(new.encode(), confirm.encode()):
            raise PasswordMismatch("the new passwords differ")
        check_password_policy(new, row[0])
        digest = hash_password(new)
        now = self.clock()
        with transaction(self.conn) as c:
            c.execute("UPDATE accounts SET password_hash = ?, password_changed_ts = ?, "
                      "credential_gen = credential_gen + 1, code_failures = 0, "
                      "code_locked_until = 0, updated_ts = ? WHERE id = ?",
                      (digest, now, now, account_id))
            return self._revoke_sessions(c, account_id, keep=keep_sid, now=now)

    # -- second factor ---------------------------------------------------------------------

    def totp_begin(self, account_id: str, password: str) -> str:
        """Start an enrolment: a new secret, sealed as pending until a code confirms it."""
        with self._read() as c:
            row = self._require(c, account_id)
        if row[3]:
            raise DemoAccount("two-step sign-in stays off for the shared demo account")
        if row[4]:
            raise TwoFactorState("two-step sign-in is already on")
        self._check_password(row[1], password)
        secret = new_totp_secret()
        with transaction(self.conn) as c:
            c.execute("UPDATE accounts SET totp_pending = ?, updated_ts = ? WHERE id = ?",
                      (self.box.seal(account_id, secret), self.clock(), account_id))
        return secret

    def totp_pending_secret(self, account_id: str) -> str | None:
        with self._read() as c:
            row = self._require(c, account_id)
        return self.box.open(account_id, row[5]) if row[5] else None

    def totp_cancel(self, account_id: str) -> None:
        with transaction(self.conn) as c:
            c.execute("UPDATE accounts SET totp_pending = '' WHERE id = ?", (account_id,))

    def totp_confirm(self, account_id: str, code: str) -> list[str]:
        """Turn the second factor on with a code from the pending secret; returns the ten
        recovery codes (shown once, stored as hashes)."""
        now = self.clock()
        with transaction(self.conn) as c:
            row = self._require(c, account_id)
            if row[3]:
                raise DemoAccount("two-step sign-in stays off for the shared demo account")
            secret = self.box.open(account_id, row[5]) if row[5] else None
            if not secret or row[4]:
                raise TwoFactorState("start the set-up again")
            step = match_totp(secret, code.strip().replace(" ", ""), last_step=0, now=now)
            if step is None:
                raise WrongCode("the code is not correct")
            codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
            c.execute("UPDATE accounts SET totp_secret = totp_pending, totp_pending = '', "
                      "credential_gen = credential_gen + 1, code_failures = 0, "
                      "code_locked_until = 0, totp_last_step = ?, updated_ts = ? WHERE id = ?",
                      (step, now, account_id))
            c.execute("DELETE FROM recovery_codes WHERE account_id = ?", (account_id,))
            c.executemany("INSERT INTO recovery_codes (account_id, code_sha256) VALUES (?, ?)",
                          [(account_id, _recovery_hash(account_id, k)) for k in codes])
        return codes

    def totp_disable(self, account_id: str, password: str, code: str) -> None:
        with self._read() as c:
            row = self._require(c, account_id)
        if not row[4]:
            raise TwoFactorState("two-step sign-in is off")
        self._check_password(row[1], password)
        self._code_gate(account_id)
        try:
            with transaction(self.conn) as c:
                row = self._require(c, account_id)
                self._checked_code(c, account_id, row[4], row[6], code)
                self._clear_totp(c, account_id)
        except WrongCode:
            self._code_failed(account_id)
            raise

    def recovery_codes_left(self, account_id: str) -> int:
        with self._read() as c:
            return int(c.execute("SELECT count(*) FROM recovery_codes WHERE account_id = ? AND "
                                 "used_ts IS NULL", (account_id,)).fetchone()[0])

    @staticmethod
    def _clear_totp(c: sqlite3.Connection, account_id: str) -> None:
        c.execute("UPDATE accounts SET totp_secret = '', totp_pending = '', totp_last_step = 0, "
                  "credential_gen = credential_gen + 1, code_failures = 0, "
                  "code_locked_until = 0 WHERE id = ?", (account_id,))
        c.execute("DELETE FROM recovery_codes WHERE account_id = ?", (account_id,))

    # -- sessions --------------------------------------------------------------------------

    def record_session(self, sid: str, account_id: str, expires_ts: int, user_agent: str
                       ) -> None:
        now = self.clock()
        with transaction(self.conn) as c:
            c.execute("INSERT OR REPLACE INTO account_sessions (sid_sha256, account_id, "
                      "created_ts, last_seen_ts, expires_ts, user_agent) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      (sha256_hex(sid), account_id, now, now, expires_ts,
                       short_agent(user_agent)))

    def session_account(self, sid: str, now: float | None = None) -> str | None:
        """The account id of a live session row whose account is active, else None."""
        t = self.clock() if now is None else now
        digest = sha256_hex(sid)
        with self._read() as c:
            row = c.execute(
                "SELECT s.account_id FROM account_sessions s JOIN accounts a "
                "ON a.id = s.account_id WHERE s.sid_sha256 = ? AND s.revoked_ts IS NULL "
                "AND s.expires_ts > ? AND a.status = 'active'", (digest, t)).fetchone()
            if row is None:
                return None
            with self._lock:
                due = t - self._touched.get(digest, 0.0) >= TOUCH_EVERY_S
                if due:
                    self._touched[digest] = t
                    self._touched.move_to_end(digest)
                    while len(self._touched) > 4096:
                        self._touched.popitem(last=False)
            if due:
                c.execute("UPDATE account_sessions SET last_seen_ts = ? WHERE sid_sha256 = ?",
                          (t, digest))
        return str(row[0])

    def sessions(self, account_id: str, current_sid: str = "") -> list[SessionRow]:
        now = self.clock()
        mine = sha256_hex(current_sid) if current_sid else ""
        with self._read() as c:
            rows = c.execute("SELECT sid_sha256, created_ts, last_seen_ts, expires_ts, "
                             "user_agent FROM account_sessions WHERE account_id = ? AND "
                             "revoked_ts IS NULL AND expires_ts > ? ORDER BY created_ts DESC",
                             (account_id, now)).fetchall()
        return [SessionRow(session_ref(h), created, seen, exp, ua, h == mine)
                for h, created, seen, exp, ua in rows]

    def revoke_session(self, account_id: str, ref: str, *, current_sid: str) -> bool:
        """Revoke one of the account's other sessions by its page handle."""
        mine = sha256_hex(current_sid)
        now = self.clock()
        with transaction(self.conn) as c:
            rows = c.execute("SELECT sid_sha256 FROM account_sessions WHERE account_id = ? "
                             "AND revoked_ts IS NULL", (account_id,)).fetchall()
            for (digest,) in rows:
                if digest != mine and hmac.compare_digest(session_ref(digest), ref):
                    self._revoke_hashes(c, [digest], now)
                    return True
        return False

    def revoke_others(self, account_id: str, *, current_sid: str) -> int:
        with transaction(self.conn) as c:
            return self._revoke_sessions(c, account_id, keep=current_sid, now=self.clock())

    def revoke_sid(self, sid: str) -> None:
        with transaction(self.conn) as c:
            self._revoke_hashes(c, [sha256_hex(sid)], self.clock())

    def revoke_all_sessions(self) -> None:
        """At start: sessions live in process memory, so every earlier row is dead."""
        now = self.clock()
        with transaction(self.conn) as c:
            c.execute("UPDATE account_sessions SET revoked_ts = ? WHERE revoked_ts IS NULL",
                      (now,))
            # rows no key refers to are useless after they end: keep the table small
            c.execute("DELETE FROM account_sessions WHERE expires_ts < ? AND sid_sha256 NOT IN "
                      "(SELECT session_sha256 FROM keys)", (now - 30 * 86400,))

    def _revoke_sessions(self, c: sqlite3.Connection, account_id: str, *, keep: str,
                         now: float) -> int:
        mine = sha256_hex(keep) if keep else ""
        rows = c.execute("SELECT sid_sha256 FROM account_sessions WHERE account_id = ? AND "
                         "revoked_ts IS NULL", (account_id,)).fetchall()
        digests = [d for (d,) in rows if d != mine]
        self._revoke_hashes(c, digests, now)
        return len(digests)

    @staticmethod
    def _revoke_hashes(c: sqlite3.Connection, digests: list[str], now: float) -> None:
        for digest in digests:
            c.execute("UPDATE account_sessions SET revoked_ts = ? WHERE sid_sha256 = ? AND "
                      "revoked_ts IS NULL", (now, digest))
            c.execute("UPDATE keys SET revoked_ts = ? WHERE session_sha256 = ? AND "
                      "revoked_ts IS NULL", (int(now), digest))

    # -- deletion and export ---------------------------------------------------------------

    def delete(self, account_id: str, *, confirm: str, password: str, code: str = "") -> None:
        """Typed confirmation (the user name) + password (+ a code with 2FA on). Erases the
        personal fields and the second factor, revokes every session and approver key; the
        row stays as a tombstone so receipts keep resolving to a pseudonymous id."""
        with self._read() as c:
            row = self._require(c, account_id)
        if row[3]:
            raise DemoAccount("the shared demo account cannot be deleted")
        if not hmac.compare_digest(normalize_username(confirm).encode(), row[0].encode()):
            raise ConfirmationMismatch("type your user name to confirm")
        self._check_password(row[1], password)
        now = self.clock()
        if row[4]:
            self._code_gate(account_id)
        try:
            with transaction(self.conn) as c:
                row = self._require(c, account_id)
                if row[4]:
                    self._check_code(c, account_id, row[4], row[6], code)
                self._erase(c, account_id, now)
        except WrongCode:
            self._code_failed(account_id)
            raise

    def _erase(self, c: sqlite3.Connection, account_id: str, now: float) -> None:
        """Revoke every session and key, erase every personal field, keep the tombstone."""
        self._revoke_sessions(c, account_id, keep="", now=now)
        c.execute("UPDATE accounts SET username = NULL, password_hash = '', "
                  "status = 'deleted', display_name = '', email = '', lang = '', "
                  "theme = '', palette = '', advertiser_type = 'person', legal_name = '', "
                  "country = '', tax_id = '', totp_secret = '', totp_pending = '', "
                  "totp_last_step = 0, credential_gen = credential_gen + 1, "
                  "whatsapp_number = '', notify_whatsapp = 0, "
                  "updated_ts = ?, deleted_ts = ? WHERE id = ?",
                  (now, now, account_id))
        c.execute("DELETE FROM recovery_codes WHERE account_id = ?", (account_id,))
        c.execute("UPDATE account_sessions SET user_agent = '', last_seen_ts = created_ts "
                  "WHERE account_id = ?", (account_id,))

    def approvals(self, account_id: str) -> list[dict[str, Any]]:
        """Approvals signed with keys registered by this account's sessions."""
        with self._read() as c:
            rows = c.execute(
                "SELECT a.id, a.run_id, a.action, a.item_id, a.content_sha256, a.approver, "
                "a.decided_ts FROM approvals a JOIN keys k ON k.approver = a.approver "
                "JOIN account_sessions s ON s.sid_sha256 = k.session_sha256 "
                "WHERE s.account_id = ? AND a.status = 'approved' ORDER BY a.decided_ts",
                (account_id,)).fetchall()
        keys = ("approval_id", "run_id", "action", "item_id", "content_sha256", "approver",
                "decided_ts")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def export(self, account_id: str, receipts: list[Any], current_sid: str = ""
               ) -> dict[str, Any]:
        """Everything stored about the account, minus hashes and secrets."""
        account = self.get(account_id)
        if account is None or account.status == "deleted":
            raise NoSuchAccount("no such account")
        by_pending = {r.details.get("pending_id"): r.id for r in receipts
                      if r.kind == "approval" and isinstance(r.details, dict)}
        signed = [{**a, "receipt_id": by_pending.get(a["approval_id"], "")}
                  for a in self.approvals(account_id)]
        return {
            "format": "muse-studio-lite/account-export/v1",
            "exported_ts": self.clock(),
            "account": {"id": account.id, "username": account.username,
                        "demo": account.is_demo, "status": account.status,
                        "created_ts": account.created_ts,
                        "password_changed_ts": account.password_changed_ts,
                        "two_factor": account.totp_enabled,
                        "recovery_codes_left": self.recovery_codes_left(account_id)},
            "profile": account.profile(),
            "sessions": [{"created_ts": s.created_ts, "last_seen_ts": s.last_seen_ts,
                          "expires_ts": s.expires_ts, "user_agent": s.user_agent,
                          "current": s.current}
                         for s in self.sessions(account_id, current_sid)],
            "approvals": signed,
            "whatsapp": self._whatsapp(account_id),
        }

    def _whatsapp(self, account_id: str) -> dict[str, Any]:
        with self._read() as c:
            row = c.execute("SELECT whatsapp_number, notify_whatsapp FROM accounts "
                            "WHERE id = ?", (account_id,)).fetchone()
        return {"number": row[0] if row else "", "notifications": bool(row[1]) if row else False}


def _clean_profile(fields: Mapping[str, str]) -> dict[str, str]:
    """Validated profile fields (only those given). Free text is trimmed, without control
    characters, within ``LIMITS``; company fields are cleared for a person."""
    from studio import prefs  # the allowed language/theme/palette values
    from studio.ui import i18n

    allowed: dict[str, tuple[str, ...]] = {
        "lang": ("", *i18n.LANGS), "theme": ("", *prefs.THEMES),
        "palette": ("", *prefs.PALETTES), "advertiser_type": ADVERTISER_TYPES}
    out: dict[str, str] = {}
    for name in Account.PROFILE:
        if name not in fields:
            continue
        value = str(fields[name]).strip()
        if name in allowed:
            if value not in allowed[name]:
                raise InvalidProfile(name)
        else:
            if len(value) > LIMITS[name] or any(
                    unicodedata.category(ch) in ("Cc", "Cf") for ch in value):
                raise InvalidProfile(name)
            if name == "email" and value and not _EMAIL.fullmatch(value):
                raise InvalidProfile(name)
        out[name] = value
    if out.get("advertiser_type") == "person":
        out.update(legal_name="", country="", tax_id="")
    return out


# -- sessions bound to accounts --------------------------------------------------------


class AccountSessions:
    """``studio.app.Sessions`` plus the account rows: a cookie is valid only while its row
    is live and its account active. Same interface (``issue``, ``check``, ``revoke``)."""

    def __init__(self, base: BaseSessions, accounts: Accounts) -> None:
        self.base = base
        self.accounts = accounts

    def issue(self, now: float | None = None, *, account_id: str = "", user_agent: str = ""
              ) -> str:
        value = self.base.issue(now)
        if account_id:
            sid, exp, _ = value.split(".")
            self.accounts.record_session(sid, account_id, int(exp), user_agent)
        return value

    def check(self, value: str | None, now: float | None = None) -> str | None:
        sid = self.base.check(value, now)
        if sid is None or self.accounts.session_account(sid, now) is None:
            return None
        return sid

    def account_of(self, value: str | None) -> Account | None:
        sid = self.base.check(value)
        aid = self.accounts.session_account(sid) if sid else None
        return self.accounts.get(aid) if aid else None

    def revoke(self, value: str | None) -> None:
        self.base.revoke(value)
        if value and value.count(".") == 2:
            self.accounts.revoke_sid(value.split(".")[0])


# -- the login's second step -------------------------------------------------------------


@dataclass
class Challenge:
    account_id: str
    next: str
    expires: float
    gen: int = 0  # the account's credential generation when the password step passed
    failures: int = 0


class Challenges:
    """Pending second steps, in process memory: a random token (cookie) → the account whose
    password was correct. Five minutes, three wrong codes, then the password step again."""

    TTL_S = 300.0
    MAX_FAILURES = 3
    MAX = 2000

    def __init__(self) -> None:
        self._items: OrderedDict[str, Challenge] = OrderedDict()
        self._lock = threading.Lock()

    def open(self, account_id: str, nxt: str, now: float | None = None, *, gen: int = 0
             ) -> str:
        t = time.time() if now is None else now
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._items[sha256_hex(token)] = Challenge(account_id, nxt, t + self.TTL_S, gen)
            while len(self._items) > self.MAX:
                self._items.popitem(last=False)
        return token

    def get(self, token: str | None, now: float | None = None) -> Challenge | None:
        if not token or len(token) > 100:
            return None
        t = time.time() if now is None else now
        with self._lock:
            item = self._items.get(sha256_hex(token))
            if item is not None and item.expires <= t:
                self._items.pop(sha256_hex(token), None)
                return None
            return item

    def fail(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            item = self._items.get(sha256_hex(token))
            if item is not None:
                item.failures += 1
                if item.failures >= self.MAX_FAILURES:
                    self._items.pop(sha256_hex(token), None)

    def drop_account(self, account_id: str) -> None:
        """End every pending second step of an account (its password or 2FA changed)."""
        with self._lock:
            for key in [k for k, v in self._items.items() if v.account_id == account_id]:
                del self._items[key]

    def close(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._items.pop(sha256_hex(token), None)
