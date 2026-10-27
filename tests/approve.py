"""Test helper: a real human approval (registered key, signature, consumed nonce)."""

import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio import db
from studio.approvals import Approval, Keys, Pending, verify_and_consume
from studio.receipts import canonical, public_key_raw

SESSION = "test-session"


def approve(conn, ledger, action, item_id, payload, run_id, *, key=None, now=None):
    """Pending → signed → consumed; returns the ``Verified``."""
    key = key or Ed25519PrivateKey.generate()
    with db.transaction(conn):
        approver = Keys.register(conn, SESSION, public_key_raw(key.public_key()))
    pending = Pending.create(action, item_id, payload, ledger=ledger, run_id=run_id, now=now)
    with db.transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)",
                     (pending.id, run_id, canonical(payload).decode(), time.time()))
    sig = key.sign(pending.message(approver)).hex()
    approval = Approval(pending_id=pending.id, action=action, item_id=item_id,
                        content_sha256=pending.content_sha256, chain_head=pending.chain_head,
                        approver=approver, nonce=pending.nonce, ts=pending.ts, signature=sig)
    with db.transaction(conn):
        return verify_and_consume(conn, approval, payload, ledger=ledger, session_id=SESSION,
                                  now=now)
