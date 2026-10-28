"""Renewing an expired approval (no time limit for the human) keeps one human decision one
signable approval: the old approval is superseded, a second renewal of it, signing it, or
renewing once the item is approved (or executed) is refused, and at most one
``campaign.create`` per item can run."""

import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio import approvals as ap
from studio import db
from studio.receipts import canonical, public_key_raw

PAYLOAD = {"action": "campaign.create", "ad": {"headline": "x"}}


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    led = db.open_ledger(conn, tmp_path / "ledger.key")
    led.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3")
    key = Ed25519PrivateKey.generate()
    approver = ap.Keys.register(conn, "s1", public_key_raw(key.public_key()))
    with db.transaction(conn):
        p = ap.Pending.create("campaign.create", "cmp_1a2b3c", PAYLOAD, ledger=led,
                              run_id="run-1")
        p.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (p.id, "run-1", canonical(PAYLOAD).decode(), 0))
    return conn, led, key, approver, p


def sign(env, p, now):
    conn, led, key, approver, _ = env
    a = ap.Approval(pending_id=p.id, **p.fields(approver),
                    signature=key.sign(p.message(approver)).hex())
    with db.transaction(conn):
        return ap.verify_and_consume(conn, a, PAYLOAD, ledger=led, session_id="s1", now=now)


def late(p):
    return p.ts + ap.TTL_S + 5


def test_an_open_window_is_not_renewed(env):
    conn, led, *_, p = env
    same, renewed = ap.renew(conn, p.id, PAYLOAD, ledger=led, now=p.ts + 10)
    assert same.id == p.id and renewed is False


def test_renewal_supersedes_the_old_approval(env):
    conn, led, *_, p = env
    fresh, renewed = ap.renew(conn, p.id, PAYLOAD, ledger=led, now=late(p))
    assert renewed and fresh.id != p.id and fresh.nonce != p.nonce
    assert (fresh.action, fresh.item_id, fresh.content_sha256) == (
        p.action, p.item_id, p.content_sha256)
    assert conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                        (fresh.id,)).fetchone()[0] == canonical(PAYLOAD).decode()
    with pytest.raises(ap.Superseded):  # renew twice from the same old id
        ap.renew(conn, p.id, PAYLOAD, ledger=led, now=late(p) + 1)
    with pytest.raises(ap.Superseded):  # approve the old id after the renewal
        sign(env, p, now=p.ts + 1)
    assert sign(env, fresh, now=fresh.ts + 1).pending_id == fresh.id


def test_no_renewal_once_the_item_is_approved_or_executed(env):
    conn, led, *_, p = env
    fresh, _ = ap.renew(conn, p.id, PAYLOAD, ledger=led, now=late(p))
    sign(env, fresh, now=fresh.ts + 1)
    with pytest.raises(ap.NotPending):  # the approved one itself
        ap.renew(conn, fresh.id, PAYLOAD, ledger=led, now=late(fresh))
    with db.transaction(conn):  # a second, stale approval for the same item
        other = ap.Pending.create("campaign.create", "cmp_1a2b3c", PAYLOAD, ledger=led,
                                  run_id="run-1", now=p.ts)
        other.save(conn)
        conn.execute("INSERT INTO executions (approval_id, action, item_id, run_id, status, "
                     "created_ts, updated_ts) VALUES (?, 'campaign.create', 'cmp_1a2b3c', "
                     "'run-1', 'done', 0, 0)", (fresh.id,))
    with pytest.raises(ap.NotPending):  # renew after execution
        ap.renew(conn, other.id, PAYLOAD, ledger=led, now=late(fresh))


def test_no_renewal_while_another_approval_for_the_item_is_open(env):
    conn, led, *_, p = env
    with db.transaction(conn):
        other = ap.Pending.create("campaign.create", "cmp_1a2b3c", PAYLOAD, ledger=led,
                                  run_id="run-1", now=late(p) - 10)
        other.save(conn)
    with pytest.raises(ap.NotPending):
        ap.renew(conn, p.id, PAYLOAD, ledger=led, now=late(p))


def test_one_campaign_per_item_can_run(env):
    conn, *_ = env
    insert = ("INSERT INTO executions (approval_id, action, item_id, run_id, status, "
              "created_ts, updated_ts) VALUES (?, 'campaign.create', 'cmp_9', 'run-1', ?, 0, 0)")
    with db.transaction(conn):
        conn.execute(insert, ("a1", "failed"))
        conn.execute(insert, ("a2", "running"))  # a failed one may be retried
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute(insert, ("a3", "running"))
