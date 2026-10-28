"""Executors run an external write only for a consumed, human-signed approval.

``authorize`` is the gate in front of every adapter write, checked before the adapter is
touched: the ``Verified`` must name an approval that the database shows as ``approved``
with the same action, item, content hash, approver and signature, whose nonce was
consumed, still inside the TTL, for exactly this payload; and the approval must not have
been executed before (``executions`` row claimed in the same transaction as the check, so
a replay — even concurrent — is refused). ``record_write`` leaves the ``external_write``
receipt that ``studio verify`` links back to the approval (``approval_id``, ``action``,
``item_id``, content hash).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Mapping
from typing import Any

from studio.approvals import TTL_S, Verified, payload_sha256, transaction
from studio.receipts import Ledger, Receipt

LOG = logging.getLogger("studio.executor")

SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    approval_id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    item_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    result TEXT NOT NULL DEFAULT '{}',
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);
"""


class ExecutionRefused(Exception):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


# One campaign per approved item: a renewed approval reuses its item id, so at most one
# ``campaign.create`` for an item may run or be done (a failed one may be retried).
ONCE_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS executions_campaign_once ON executions "
              "(action, item_id) WHERE action = 'campaign.create' "
              "AND status IN ('running', 'done')")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    try:
        conn.execute(ONCE_INDEX)
    except sqlite3.IntegrityError:  # an older database already holds duplicates
        LOG.warning("executions: duplicate campaign.create rows; unique index not created")


def authorize(conn: sqlite3.Connection, verified: Any, action: str,
              payload: Mapping[str, Any], now: float | None = None) -> Verified:
    """Check everything and claim the execution; ``ExecutionRefused`` otherwise."""
    if not isinstance(verified, Verified):
        raise ExecutionRefused("no verified approval", "not_approved")
    if verified.action != action:
        raise ExecutionRefused("approval is for another action", "wrong_action")
    if payload_sha256(dict(payload)) != verified.content_sha256:
        raise ExecutionRefused("payload differs from the signed content", "content_mismatch")
    clock = time.time() if now is None else now
    with transaction(conn):
        row = conn.execute(
            "SELECT status, action, item_id, content_sha256, approver, signature, nonce, ts, "
            "run_id FROM approvals WHERE id = ?", (verified.pending_id,)).fetchone()
        if row is None or row[0] != "approved":
            raise ExecutionRefused("approval is not signed", "not_approved")
        if (row[1], row[2], row[3], row[4], row[5], row[8]) != (
                verified.action, verified.item_id, verified.content_sha256, verified.approver,
                verified.signature, verified.run_id):
            raise ExecutionRefused("approval does not match the signed record", "not_approved")
        if conn.execute("SELECT 1 FROM nonces WHERE nonce = ?", (row[6],)).fetchone() is None:
            raise ExecutionRefused("approval nonce was never consumed", "not_approved")
        if clock - row[7] > TTL_S:
            raise ExecutionRefused(f"approval older than {TTL_S} s", "expired")
        try:
            conn.execute(
                "INSERT INTO executions (approval_id, action, item_id, run_id, status, "
                "created_ts, updated_ts) VALUES (?, ?, ?, ?, 'running', ?, ?)",
                (verified.pending_id, action, verified.item_id, verified.run_id, clock, clock))
        except sqlite3.IntegrityError:
            raise ExecutionRefused("approval was already executed", "replay") from None
    return verified


def finish(conn: sqlite3.Connection, verified: Verified, status: str,
           result: Mapping[str, Any]) -> None:
    with transaction(conn):
        conn.execute("UPDATE executions SET status = ?, result = ?, updated_ts = ? "
                     "WHERE approval_id = ?",
                     (status, json.dumps(dict(result), ensure_ascii=False), time.time(),
                      verified.pending_id))


def record_write(ledger: Ledger, verified: Verified, *, agent: str, adapter: str,
                 details: Mapping[str, Any], channel: str = "meta") -> Receipt:
    return ledger.append(verified.run_id, agent=agent, kind="external_write",
                         provider=f"{channel}-{adapter}", output_sha256=verified.content_sha256,
                         details={**verified.write_details(), "adapter": adapter,
                                  **dict(details)})


def record_step(ledger: Ledger, verified: Verified, *, agent: str, adapter: str, step: str,
                status: str, details: Mapping[str, Any] | None = None,
                channel: str = "meta") -> Receipt:
    """One adapter call inside an approved write (``tool_call``, side effects external)."""
    return ledger.append(verified.run_id, agent=agent, kind="tool_call",
                         provider=f"{channel}-{adapter}",
                         details={"tool": step, "status": status, "side_effects": "external",
                                  "adapter": adapter, "approval_id": verified.pending_id,
                                  **dict(details or {})})


def safe_id(value: str) -> str:
    """An external object id as it may appear in a public receipt: kept when opaque, else
    its hash (long numeric Graph ids would be mistaken for phone numbers by redaction)."""
    from studio.receipts import is_opaque, sha256_hex

    return value if is_opaque(value) else "sha256:" + sha256_hex(value)[:32]
