"""Human-signed approvals for every external write.

A pending approval binds the exact payload (``content_sha256``), the provenance that
produced it (``chain_head`` of the run's receipt chain), a one-time ``nonce``, the issue
time ``ts`` and, for content made from a confirmed Brief v1, the brief's ``brief_sha256``
(signed too, and checked against the run's latest ``brief.confirmed`` receipt). The
approver's browser signs ``approval_message`` with a non-extractable Ed25519 key
registered for its session. ``verify_and_consume`` checks everything and
consumes the nonce inside the caller's SQLite write transaction, so the state change and
the nonce stand or fall together and a signature can be used exactly once.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from studio.receipts import (
    APPROVAL_BRIEF_FIELD,
    APPROVAL_SIGNED_FIELDS,
    APPROVAL_TTL_S,
    BRIEF_BOUND_ACTIONS,
    BRIEF_KIND,
    GENESIS,
    Ledger,
    approval_message,
    canonical,
    is_opaque,
    key_id,
    sha256_hex,
)

__all__ = [
    "ACTIONS",
    "SIGNED_FIELDS",
    "SKEW_S",
    "TTL_S",
    "Approval",
    "ApprovalError",
    "BriefChanged",
    "InvalidItemId",
    "KeyConflict",
    "KeyInactive",
    "Keys",
    "Pending",
    "Verified",
    "WrongSession",
    "approval_message",
    "confirmed_brief",
    "ensure_schema",
    "payload_sha256",
    "transaction",
    "verify_and_consume",
]

ACTIONS = frozenset({"campaign.create", "campaign.activate", "reply.post", "comment.hide"})
SIGNED_FIELDS = APPROVAL_SIGNED_FIELDS
TTL_S = APPROVAL_TTL_S  # the verifier enforces the same window between approval and write
SKEW_S = 60
KEY_TTL_S = 8 * 3600  # an approver key lives no longer than the session that registered it

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    approver TEXT PRIMARY KEY,
    public_key BLOB NOT NULL CHECK (length(public_key) = 32),
    session_sha256 TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    expires_ts INTEGER NOT NULL,
    revoked_ts INTEGER
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action TEXT NOT NULL,
    item_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    chain_head TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    ts INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved')),
    approver TEXT,
    signature TEXT,
    decided_ts INTEGER,
    brief_sha256 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS nonces (
    nonce TEXT PRIMARY KEY,
    consumed_ts INTEGER NOT NULL
);
"""


class ApprovalError(Exception):
    code = "approval_error"
    status = 400


class NotFound(ApprovalError):
    code, status = "not_found", 404


class UnknownAction(ApprovalError):
    code = "unknown_action"


class InvalidItemId(ApprovalError):
    code = "invalid_item"


class FieldMismatch(ApprovalError):
    code = "field_mismatch"


class ContentMismatch(ApprovalError):
    code = "content_mismatch"


class ChainHeadMismatch(ApprovalError):
    code, status = "chain_head_mismatch", 409


class NonceMismatch(ApprovalError):
    code = "nonce_mismatch"


class Expired(ApprovalError):
    code, status = "expired", 410


class FromFuture(ApprovalError):
    code = "from_future"


class UnknownKey(ApprovalError):
    code, status = "unknown_key", 403


class KeyInactive(ApprovalError):
    code, status = "key_inactive", 403


class KeyConflict(ApprovalError):
    """The key id exists already (revoked, or bound to another session): use a new key."""

    code, status = "key_conflict", 409


class WrongSession(ApprovalError):
    code, status = "wrong_session", 403


class BadSignature(ApprovalError):
    code, status = "bad_signature", 403


class NonceUsed(ApprovalError):
    code, status = "nonce_used", 409


class BriefChanged(ApprovalError):
    """The run's confirmed brief is not the one this approval signs."""

    code, status = "brief_changed", 409


def confirmed_brief(ledger: Ledger, run_id: str) -> str:
    """``brief_sha256`` of the run's latest ``brief.confirmed`` receipt, or ``""``."""
    for r in reversed(ledger.receipts(run_id)):
        if r.kind == BRIEF_KIND:
            return str(r.details.get("brief_sha256", ""))
    return ""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` … ``COMMIT`` (``ROLLBACK`` on error). Needs ``isolation_level=None``.

    IMMEDIATE takes the write lock up front, so concurrent consumers serialize instead of
    racing on stale snapshots.
    """
    lock: AbstractContextManager[Any] = getattr(conn, "lock", None) or nullcontext()
    with lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def payload_sha256(payload: bytes | Mapping[str, Any]) -> str:
    """Hash of the exact bytes to send (canonical JSON for structured payloads)."""
    return sha256_hex(payload if isinstance(payload, bytes) else canonical(dict(payload)))


class Keys:
    """Approver public keys, one id per key (its fingerprint), bound to a login session.

    A key can sign approvals only while its session is active: logout revokes it and it
    expires with the session. Revoked keys stay published (``registry``) so that approvals
    signed earlier remain verifiable. A key id is registered exactly once: it is never
    revived after revocation and never moves to another session (``KeyConflict``), so the
    browser generates a fresh key pair for every session.
    """

    @staticmethod
    def register(
        conn: sqlite3.Connection,
        session_id: str,
        public_key: bytes,
        *,
        expires_ts: int | None = None,
        now: float | None = None,
    ) -> str:
        if len(public_key) != 32:
            raise ValueError("Ed25519 public key must be 32 raw bytes")
        pub = Ed25519PublicKey.from_public_bytes(public_key)
        approver = key_id(pub)
        created = int(time.time() if now is None else now)
        expires = int(expires_ts if expires_ts is not None else created + KEY_TTL_S)
        session_hash = sha256_hex(session_id)
        cur = conn.execute(
            "INSERT INTO keys (approver, public_key, session_sha256, created_ts, expires_ts) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (approver) DO NOTHING",
            (approver, public_key, session_hash, created, expires),
        )
        if cur.rowcount == 1:
            return approver
        row = conn.execute(
            "SELECT session_sha256, revoked_ts, expires_ts FROM keys WHERE approver = ?",
            (approver,),
        ).fetchone()
        if row[0] == session_hash and row[1] is None and created < row[2]:
            return approver  # same live session registering the same key again: no change
        raise KeyConflict("this key is revoked or belongs to another session; use a new key")

    @staticmethod
    def revoke_session(
        conn: sqlite3.Connection, session_id: str, now: float | None = None
    ) -> int:
        cur = conn.execute(
            "UPDATE keys SET revoked_ts = ? WHERE session_sha256 = ? AND revoked_ts IS NULL",
            (int(time.time() if now is None else now), sha256_hex(session_id)),
        )
        return cur.rowcount

    @staticmethod
    def revoke_all(conn: sqlite3.Connection, now: float | None = None) -> int:
        cur = conn.execute("UPDATE keys SET revoked_ts = ? WHERE revoked_ts IS NULL",
                           (int(time.time() if now is None else now),))
        return cur.rowcount

    @staticmethod
    def get(conn: sqlite3.Connection, approver: str) -> Ed25519PublicKey | None:
        row = conn.execute("SELECT public_key FROM keys WHERE approver = ?", (approver,)).fetchone()
        return Ed25519PublicKey.from_public_bytes(row[0]) if row else None

    @staticmethod
    def is_active(conn: sqlite3.Connection, approver: str, now: float | None = None) -> bool:
        row = conn.execute(
            "SELECT expires_ts, revoked_ts FROM keys WHERE approver = ?", (approver,)
        ).fetchone()
        clock = time.time() if now is None else now
        return bool(row) and row[1] is None and clock < row[0]

    @staticmethod
    def session_matches(conn: sqlite3.Connection, approver: str, session_id: str) -> bool:
        row = conn.execute(
            "SELECT session_sha256 FROM keys WHERE approver = ?", (approver,)
        ).fetchone()
        return bool(row) and secrets.compare_digest(row[0], sha256_hex(session_id))

    @staticmethod
    def registry(conn: sqlite3.Connection) -> dict[str, Ed25519PublicKey]:
        rows = conn.execute("SELECT approver, public_key FROM keys ORDER BY approver")
        return {a: Ed25519PublicKey.from_public_bytes(k) for a, k in rows}


@dataclass(frozen=True)
class Pending:
    id: str
    run_id: str
    action: str
    item_id: str
    content_sha256: str
    chain_head: str
    nonce: str
    ts: int
    brief_sha256: str = ""

    @classmethod
    def create(
        cls,
        action: str,
        item_id: str,
        payload: bytes | Mapping[str, Any],
        *,
        ledger: Ledger,
        run_id: str,
        now: float | None = None,
        brief_sha256: str = "",
    ) -> Pending:
        if action not in ACTIONS:
            raise UnknownAction(f"unknown action {action!r}")
        if not isinstance(item_id, str) or not is_opaque(item_id):
            # The signed message is stored un-redacted in receipts: ids only, never text.
            raise InvalidItemId("item_id must be an internal opaque id")
        current = confirmed_brief(ledger, run_id)
        if brief_sha256 and brief_sha256 != current:
            raise BriefChanged("the brief is not the run's confirmed brief")
        if current and not brief_sha256 and action in BRIEF_BOUND_ACTIONS:
            raise BriefChanged("this run has a confirmed brief; the approval must sign it")
        return cls(
            id=uuid.uuid4().hex,
            run_id=run_id,
            action=action,
            item_id=item_id,
            content_sha256=payload_sha256(payload),
            chain_head=ledger.chain_head(run_id),
            nonce="n_" + secrets.token_hex(16),
            ts=int(time.time() if now is None else now),
            brief_sha256=brief_sha256,
        )

    def save(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO approvals (id, run_id, action, item_id, content_sha256, chain_head, "
            "nonce, ts, brief_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.id, self.run_id, self.action, self.item_id, self.content_sha256,
             self.chain_head, self.nonce, self.ts, self.brief_sha256),
        )

    @classmethod
    def load(cls, conn: sqlite3.Connection, pending_id: str) -> Pending:
        row = conn.execute(
            "SELECT id, run_id, action, item_id, content_sha256, chain_head, nonce, ts, "
            "brief_sha256 FROM approvals WHERE id = ?",
            (pending_id,),
        ).fetchone()
        if row is None:
            raise NotFound(f"no pending approval {pending_id!r}")
        return cls(*row)

    def fields(self, approver: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action": self.action,
            "item_id": self.item_id,
            "content_sha256": self.content_sha256,
            "chain_head": self.chain_head,
            "approver": approver,
            "nonce": self.nonce,
            "ts": self.ts,
        }
        if self.brief_sha256:
            out[APPROVAL_BRIEF_FIELD] = self.brief_sha256
        return out

    def message(self, approver: str) -> bytes:
        """Canonical bytes served at ``/api/approvals/{id}/payload`` for the browser to sign."""
        return approval_message(self.fields(approver))


@dataclass(frozen=True)
class Approval:
    """What the browser submits: the signed fields plus the hex Ed25519 signature."""

    pending_id: str
    action: str
    item_id: str
    content_sha256: str
    chain_head: str
    approver: str
    nonce: str
    ts: int
    signature: str
    brief_sha256: str = ""


@dataclass(frozen=True)
class Verified:
    """Proof that a human signed exactly this content; the only thing executors accept."""

    pending_id: str
    run_id: str
    action: str
    item_id: str
    content_sha256: str
    approver: str
    signature: str
    brief_sha256: str = ""

    def write_details(self) -> dict[str, str]:
        """``details`` for the ``external_write`` receipt this approval authorises."""
        return {"approval_id": self.pending_id, "action": self.action, "item_id": self.item_id}


def verify_and_consume(
    conn: sqlite3.Connection,
    approval: Approval,
    payload: bytes | Mapping[str, Any],
    *,
    ledger: Ledger,
    session_id: str,
    now: float | None = None,
) -> Verified:
    """Verify ``approval`` for ``payload`` and consume its nonce in the caller's transaction.

    ``session_id`` is the caller's login session: the approver key must have been
    registered by that same session (``WrongSession`` otherwise), so a signature cannot be
    submitted from someone else's session. The caller must hold an open write transaction
    (see ``transaction``); nothing is committed here. Raises a distinct ``ApprovalError``
    subclass for every failure.
    """
    if not conn.in_transaction:
        raise RuntimeError("verify_and_consume must run inside the caller's write transaction")
    if not ledger.joins(conn):
        raise RuntimeError(
            "the ledger must be transactional on this connection (SqliteStore), so the "
            "approval receipt commits or rolls back with the nonce"
        )
    pending = Pending.load(conn, approval.pending_id)
    if approval.action not in ACTIONS:
        raise UnknownAction(f"unknown action {approval.action!r}")
    if approval.action != pending.action or approval.item_id != pending.item_id:
        raise FieldMismatch("action or item does not match the pending approval")
    if approval.content_sha256 != pending.content_sha256:
        raise ContentMismatch("signed content hash differs from the pending content")
    if payload_sha256(payload) != pending.content_sha256:
        raise ContentMismatch("payload was changed after the approval was requested")
    if approval.chain_head != pending.chain_head:
        raise ChainHeadMismatch("signed chain head differs from the provenance of the content")
    if pending.chain_head == GENESIS and ledger.chain_head(pending.run_id) != GENESIS:
        # verify accepts a GENESIS-bound approval only as the run's first receipt
        raise ChainHeadMismatch("approval bound to an empty run, but the run has grown since")
    if approval.nonce != pending.nonce:
        raise NonceMismatch("nonce does not belong to this approval")
    if approval.ts != pending.ts:
        raise FieldMismatch("timestamp does not match the pending approval")
    if approval.brief_sha256 != pending.brief_sha256:
        raise FieldMismatch("brief hash does not match the pending approval")
    if pending.brief_sha256 and confirmed_brief(ledger, pending.run_id) != pending.brief_sha256:
        raise BriefChanged("the run's confirmed brief changed after the approval was requested")
    clock = time.time() if now is None else now
    if clock - pending.ts > TTL_S:
        raise Expired(f"approval older than {TTL_S} s")
    if pending.ts - clock > SKEW_S:
        raise FromFuture("approval timestamp is in the future")
    key = Keys.get(conn, approval.approver)
    if key is None:
        raise UnknownKey("approver key is not registered")
    if not Keys.is_active(conn, approval.approver, now=clock):
        raise KeyInactive("approver key was revoked (logout) or its session expired")
    if not Keys.session_matches(conn, approval.approver, session_id):
        raise WrongSession("approver key belongs to another session")
    try:
        key.verify(bytes.fromhex(approval.signature), pending.message(approval.approver))
    except (InvalidSignature, ValueError) as exc:
        raise BadSignature("signature does not verify for this approver") from exc

    decided = int(clock)
    try:
        conn.execute("INSERT INTO nonces (nonce, consumed_ts) VALUES (?, ?)",
                     (pending.nonce, decided))
    except sqlite3.IntegrityError as exc:
        raise NonceUsed("approval was already used") from exc
    cur = conn.execute(
        "UPDATE approvals SET status = 'approved', approver = ?, signature = ?, decided_ts = ? "
        "WHERE id = ? AND status = 'pending'",
        (approval.approver, approval.signature, decided, pending.id),
    )
    if cur.rowcount != 1:
        raise NonceUsed("approval was already decided")

    ledger.append(
        pending.run_id,
        agent="human",
        kind="approval",
        conn=conn,  # same transaction as the nonce: both commit or neither does
        output_sha256=pending.content_sha256,
        details={
            **pending.fields(approval.approver),
            "sig": approval.signature,
            "pending_id": pending.id,
        },
    )
    return Verified(
        pending_id=pending.id,
        run_id=pending.run_id,
        action=pending.action,
        item_id=pending.item_id,
        content_sha256=pending.content_sha256,
        approver=approval.approver,
        signature=approval.signature,
        brief_sha256=pending.brief_sha256,
    )
