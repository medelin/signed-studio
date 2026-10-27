"""SQLite storage for the receipt ledger.

Receipts are written inside a SQLite transaction together with the durable ledger version
(``ledger_meta.seq``). When a caller passes its own connection (an open ``BEGIN IMMEDIATE``
transaction), the receipt joins that transaction: an approval's nonce, its state change and
its receipt commit together or not at all. ``UNIQUE(run_id, prev)`` makes a forked chain
impossible at the database level.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

from studio.receipts import GENESIS, Receipt

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_ts REAL NOT NULL,
    label TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS receipts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    prev TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE,
    ts REAL NOT NULL,
    body TEXT NOT NULL,
    UNIQUE (run_id, prev)
);
CREATE INDEX IF NOT EXISTS receipts_run ON receipts (run_id, seq);
CREATE TABLE IF NOT EXISTS ledger_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    key_epoch TEXT NOT NULL,
    seq INTEGER NOT NULL
);
"""


class StudioConnection(sqlite3.Connection):
    """A connection with a re-entrant lock, shared by every user of the connection."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()


def connect(path: str | Path, timeout: float = 10.0) -> StudioConnection:
    """Autocommit connection (explicit transactions), WAL, foreign keys, busy timeout."""
    conn = sqlite3.connect(
        str(path),
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False,
        factory=StudioConnection,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _lock(conn: sqlite3.Connection) -> AbstractContextManager[Any]:
    lock = getattr(conn, "lock", None)
    return lock if lock is not None else nullcontext()


def _main_file(conn: sqlite3.Connection) -> str:
    for _, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return str(file)
    return ""


def bump_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("UPDATE ledger_meta SET seq = seq + 1 WHERE id = 1 RETURNING seq").fetchone()
    if row is None:
        raise RuntimeError("ledger_meta is not initialised")
    return int(row[0])


class SqliteStore:
    """Ledger store on a SQLite database; transactional, so it can join a caller's txn."""

    transactional = True

    def __init__(self, conn: sqlite3.Connection, key_epoch: str) -> None:
        self._conn = conn
        self._file = _main_file(conn)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO ledger_meta (id, key_epoch, seq) VALUES (1, ?, 0)",
            (key_epoch,),
        )

    def accepts(self, conn: sqlite3.Connection) -> bool:
        """True when ``conn`` points at the same database, so a receipt can join its txn."""
        return conn is self._conn or (bool(self._file) and _main_file(conn) == self._file)

    def append_atomic(
        self, run_id: str, build: Callable[[str, float], Receipt], conn: Any = None
    ) -> Receipt:
        c: sqlite3.Connection = conn if conn is not None else self._conn
        if conn is not None and not self.accepts(conn):
            raise RuntimeError("connection belongs to a different database than the ledger")
        with _lock(c):
            if c.in_transaction:
                return self._write(c, run_id, build)
            c.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._write(c, run_id, build)
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
            return receipt

    @staticmethod
    def _write(
        c: sqlite3.Connection, run_id: str, build: Callable[[str, float], Receipt]
    ) -> Receipt:
        row = c.execute(
            "SELECT hash, ts FROM receipts WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,)
        ).fetchone()
        prev, last_ts = (row[0], float(row[1])) if row else (GENESIS, 0.0)
        receipt = build(prev, last_ts)
        c.execute(
            "INSERT OR IGNORE INTO runs (id, created_ts) VALUES (?, ?)", (run_id, receipt.ts)
        )
        c.execute(
            "INSERT INTO receipts (id, run_id, prev, hash, ts, body) VALUES (?, ?, ?, ?, ?, ?)",
            (receipt.id, run_id, receipt.prev, receipt.hash, receipt.ts,
             json.dumps(receipt.__dict__, sort_keys=True)),
        )
        bump_seq(c)
        return receipt

    def head(self, run_id: str) -> str:
        with _lock(self._conn):
            row = self._conn.execute(
                "SELECT hash FROM receipts WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,)
            ).fetchone()
        return str(row[0]) if row else GENESIS

    def receipts(self, run_id: str | None = None) -> list[Receipt]:
        with _lock(self._conn):
            if run_id is None:
                rows = self._conn.execute("SELECT body FROM receipts ORDER BY seq").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT body FROM receipts WHERE run_id = ? ORDER BY seq", (run_id,)
                ).fetchall()
        return [Receipt(**json.loads(r[0])) for r in rows]

    def snapshot(self) -> tuple[int, list[Receipt]]:
        """Ledger version and all receipts, read in one transaction (consistent pair)."""
        c = self._conn
        with _lock(c):
            nested = c.in_transaction
            if not nested:
                c.execute("BEGIN")
            try:
                seq = int(c.execute("SELECT seq FROM ledger_meta WHERE id = 1").fetchone()[0])
                rows = c.execute("SELECT body FROM receipts ORDER BY seq").fetchall()
            finally:
                if not nested:
                    c.execute("COMMIT")
        return seq, [Receipt(**json.loads(r[0])) for r in rows]
