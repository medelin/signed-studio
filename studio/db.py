"""SQLite schema, synthetic seed, demo reset and the persistent, signed receipt ledger.

The ledger key lives in a 0600 file (never argv, never the repo). Its fingerprint is the
key epoch, stored in ``ledger_meta`` and signed into every manifest; opening a database
whose receipts were signed by another key fails closed. ``ledger_meta.seq`` is the durable
ledger version, bumped in the same transaction as every receipt write (and by
``reset_demo(keep_receipts=False)``), so it never goes back.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

from studio import (
    accounts,
    api_tokens,
    approvals,
    disapprovals,
    executor,
    leadads,
    notify,
    store,
    whatsapp,
)
from studio.approvals import transaction
from studio.google import store as google_store
from studio.meta import measurement
from studio.receipts import Ledger, key_id, public_key_raw
from studio.settings import ConfigError
from studio.store import StudioConnection, connect

__all__ = [
    "SEED_DIR",
    "StudioConnection",
    "connect",
    "init",
    "load_or_create_ledger_key",
    "open_ledger",
    "reset_demo",
    "seed",
    "transaction",
]

SEED_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "seed"

DEMO_SCHEMA = """
CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text' CHECK (source IN ('text', 'voice')),
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS creatives (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    placement TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PAUSED' CHECK (status IN ('PAUSED', 'ACTIVE')),
    daily_budget_minor INTEGER NOT NULL CHECK (daily_budget_minor > 0),
    adapter TEXT NOT NULL CHECK (adapter IN ('mock', 'graph')),
    external_id TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    text TEXT NOT NULL,
    created_ts REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'drafted', 'replied', 'hidden')),
    intent TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    score INTEGER,
    temp TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    lead_id TEXT NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    due_ts REAL NOT NULL,
    done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1))
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_payloads (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    payload TEXT NOT NULL,  -- canonical JSON of exactly what the approval authorises
    created_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS run_state (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'awaiting_approval', 'approved', 'done', 'failed')),
    state TEXT NOT NULL DEFAULT '{}',  -- plan, copy, creatives, review (JSON, for the UI)
    updated_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sandbox_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Seed order respects foreign keys; reset deletes in reverse.
SEED_TABLES = ("briefs", "campaigns", "comments", "leads", "tasks")
DEMO_TABLES = ("tasks", "leads", "comments", "campaigns", "creatives", "briefs", "reports",
               "pending_payloads", "run_state", "lead_forms", "recommendations",
               "insights_snapshots", "google_insights", "google_campaigns")
_COLUMNS: dict[str, tuple[str, ...]] = {
    "briefs": ("id", "text", "source", "created_ts"),
    "campaigns": ("id", "run_id", "name", "objective", "status", "daily_budget_minor",
                  "adapter", "external_id", "created_ts"),
    "comments": ("id", "post_id", "author_name", "text", "created_ts", "status"),
    "leads": ("id", "name", "email", "phone", "source", "message", "city", "country",
              "company", "kind", "created_ts"),
    "tasks": ("id", "lead_id", "title", "due_ts", "done"),
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d \-()]{7,}\d")
_SAFE_PHONE = re.compile(r"\+48 000 000 \d{3}")


# Columns added after day 2 (``ALTER TABLE … ADD COLUMN`` on databases created earlier).
MIGRATIONS: dict[str, dict[str, str]] = {
    "accounts": {  # notification categories the owner can switch off (security cannot be)
        "notify_campaigns": "INTEGER NOT NULL DEFAULT 1",
        "notify_leads": "INTEGER NOT NULL DEFAULT 1",
        # WhatsApp (studio.whatsapp): the owner's number in E.164 and the opt-in
        "whatsapp_number": "TEXT NOT NULL DEFAULT ''",
        "notify_whatsapp": "INTEGER NOT NULL DEFAULT 0",
        "whatsapp_verified_ts": "REAL NOT NULL DEFAULT 0",  # 0 = pending (not proven)
    },
    "approvals": {"brief_sha256": "TEXT NOT NULL DEFAULT ''",
                  "superseded_by": "TEXT NOT NULL DEFAULT ''",
                  "spec_sha256": "TEXT NOT NULL DEFAULT ''"},
    "briefs": {
        "draft_run_id": "TEXT NOT NULL DEFAULT ''",  # the run the draft's receipts are in
        "draft": "TEXT NOT NULL DEFAULT ''",  # Brief v1 as drafted (JSON)
        "brief_v1": "TEXT NOT NULL DEFAULT ''",  # Brief v1 as confirmed (JSON)
        "brief_sha256": "TEXT NOT NULL DEFAULT ''",
        "channel": "TEXT NOT NULL DEFAULT ''",  # 'whatsapp' for a brief sent from WhatsApp
        "account_id": "TEXT NOT NULL DEFAULT ''",  # who sent it (WhatsApp briefs)
    },
    "campaigns": {
        "adset_external_id": "TEXT NOT NULL DEFAULT ''",
        "ad_external_id": "TEXT NOT NULL DEFAULT ''",
        "more_ad_ids": "TEXT NOT NULL DEFAULT '[]'",  # further ads of the ad set (JSON)
        "currency": "TEXT NOT NULL DEFAULT 'PLN'",  # the ad account's, as signed
    },
    "comments": {
        "source": "TEXT NOT NULL DEFAULT 'seed'",
        "external_id": "TEXT NOT NULL DEFAULT ''",
        "sentiment": "TEXT NOT NULL DEFAULT ''",
        "risk": "TEXT NOT NULL DEFAULT ''",
        "requires_human": "INTEGER NOT NULL DEFAULT 0",
        "reason": "TEXT NOT NULL DEFAULT ''",
        "confidence": "REAL NOT NULL DEFAULT 0",
        "flags": "TEXT NOT NULL DEFAULT '[]'",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "pending_id": "TEXT NOT NULL DEFAULT ''",
        "lead_id": "TEXT NOT NULL DEFAULT ''",
    },
    "leads": {
        "city": "TEXT NOT NULL DEFAULT ''",
        "country": "TEXT NOT NULL DEFAULT ''",
        "company": "TEXT NOT NULL DEFAULT ''",
        "kind": "TEXT NOT NULL DEFAULT ''",
        "persona": "TEXT NOT NULL DEFAULT ''",
        "owner": "TEXT NOT NULL DEFAULT ''",
        "reasons": "TEXT NOT NULL DEFAULT '[]'",
        "next_action": "TEXT NOT NULL DEFAULT ''",
        "comment_id": "TEXT NOT NULL DEFAULT ''",
        "run_id": "TEXT NOT NULL DEFAULT ''",
        "external_id": "TEXT NOT NULL DEFAULT ''",  # Meta lead id (instant forms)
        "form_id": "TEXT NOT NULL DEFAULT ''",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init(conn: sqlite3.Connection) -> None:
    """Create every table (idempotent) and add columns introduced later."""
    conn.executescript(store.SCHEMA)
    approvals.ensure_schema(conn)
    executor.ensure_schema(conn)
    conn.executescript(DEMO_SCHEMA)
    accounts.ensure_schema(conn)
    with transaction(conn):
        _migrate(conn)
    leadads.ensure_schema(conn)
    notify.ensure_schema(conn)
    whatsapp.ensure_schema(conn)
    measurement.ensure_schema(conn)
    disapprovals.ensure_schema(conn)
    google_store.ensure_schema(conn)
    api_tokens.ensure_schema(conn)  # assistants: personal access tokens, API-drafted briefs


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _check_synthetic(table: str, rows: Any) -> None:
    """Only @example.com e-mails and +48 000 000 xxx phones may appear in seed strings."""
    text = "\n".join(_strings(rows))
    for email in _EMAIL.findall(text):
        if not email.endswith("@example.com"):
            raise ValueError(f"seed {table}: e-mail {email!r} is not @example.com")
    for phone in _PHONE.findall(text):
        if not _SAFE_PHONE.fullmatch(phone):
            raise ValueError(f"seed {table}: phone {phone!r} is not +48 000 000 xxx")


def _load_seed(seed_dir: Path) -> dict[str, list[dict[str, Any]]]:
    data: dict[str, list[dict[str, Any]]] = {}
    for table in SEED_TABLES:
        path = seed_dir / f"{table}.json"
        if not path.exists():
            continue
        rows = json.loads(path.read_text())
        _check_synthetic(table, rows)
        if not isinstance(rows, list):
            raise ValueError(f"seed {table}: expected a list")
        data[table] = rows
    return data


def _insert_seed(conn: sqlite3.Connection, data: dict[str, list[dict[str, Any]]]) -> None:
    for table in SEED_TABLES:
        cols = _COLUMNS[table]
        sql = (
            f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) "  # noqa: S608 - fixed names
            f"VALUES ({', '.join('?' for _ in cols)})"
        )
        for row in data.get(table, []):
            conn.execute(sql, tuple(row.get(c, "") for c in cols))


def seed(conn: sqlite3.Connection, seed_dir: Path = SEED_DIR) -> None:
    """Insert the synthetic demo data (idempotent: fixed ids, ``INSERT OR IGNORE``)."""
    data = _load_seed(seed_dir)  # validate everything before writing anything
    with transaction(conn):
        _insert_seed(conn, data)


def reset_demo(
    conn: sqlite3.Connection,
    keep_receipts: bool = True,
    seed_dir: Path = SEED_DIR,
    now: float | None = None,
) -> None:
    """Wipe demo data and re-seed, in one transaction; the marker is written last.

    Always kept: consumed nonces (replays stay impossible) and approver keys (historical
    approval signatures stay verifiable). Receipts and runs are kept unless
    ``keep_receipts=False``; deleting them still advances the durable ledger version.
    """
    data = _load_seed(seed_dir)
    with transaction(conn):
        for table in DEMO_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        conn.execute("DELETE FROM approvals WHERE status = 'pending'")
        if not keep_receipts:
            conn.execute("DELETE FROM receipts")
            conn.execute("DELETE FROM runs")
            store.bump_seq(conn)
        _insert_seed(conn, data)
        conn.execute(
            "INSERT INTO sandbox_meta (key, value) VALUES ('last_reset', ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (str(time.time() if now is None else now),),
        )


def load_or_create_ledger_key(path: str | Path) -> Ed25519PrivateKey:
    """Read the ledger key from a 0600 PEM file, creating it (and ``ledger.pub``) if absent."""
    p = Path(path)
    if p.exists():
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & 0o077:
            raise ConfigError(f"{p}: permissions {oct(mode)} too open for the ledger key (600)")
        key = load_pem_private_key(p.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ConfigError(f"{p}: not an Ed25519 private key")
    else:
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
    pub = p.parent / "ledger.pub"
    pub_hex = public_key_raw(key.public_key()).hex()
    if not pub.exists() or pub.read_text().strip() != pub_hex:
        pub.write_text(pub_hex + "\n")
    return key


def open_ledger(conn: sqlite3.Connection, key_path: str | Path) -> Ledger:
    """A persistent ledger on ``conn``; refuses a key that did not sign the stored receipts."""
    init(conn)
    key = load_or_create_ledger_key(key_path)
    epoch = key_id(key.public_key())
    with transaction(conn):
        row = conn.execute("SELECT key_epoch FROM ledger_meta WHERE id = 1").fetchone()
        if row is not None and row[0] != epoch:
            if conn.execute("SELECT count(*) FROM receipts").fetchone()[0]:
                raise ConfigError(
                    f"ledger key {epoch} differs from the key that signed the stored receipts "
                    f"({row[0]}); restore the original ledger key file"
                )
            conn.execute("UPDATE ledger_meta SET key_epoch = ? WHERE id = 1", (epoch,))
    return Ledger(key, store.SqliteStore(conn, key_epoch=epoch))
