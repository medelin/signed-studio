import json
import threading
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from studio import approvals as ap
from studio import store
from studio.receipts import Ledger, verify

PAYLOAD = {"message": "Thanks! Our price list is on the website.", "comment_id": "c-1"}


def raw(pub):
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw)


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "studio.db"
    conn = store.connect(db)
    ap.ensure_schema(conn)
    ledger_key = Ed25519PrivateKey.generate()
    ledger = Ledger(ledger_key, store.SqliteStore(conn, key_epoch="test"))
    ledger.append("run-1", agent="community", kind="model_call", model="muse-spark-1.3")
    key = Ed25519PrivateKey.generate()
    approver = ap.Keys.register(conn, "session-1", raw(key.public_key()))
    with ap.transaction(conn):
        pending = ap.Pending.create("reply.post", "c-1", PAYLOAD, ledger=ledger, run_id="run-1")
        pending.save(conn)
    return {"db": db, "conn": conn, "ledger": ledger, "key": key, "approver": approver,
            "pending": pending, "ledger_key": ledger_key}


def sign(env, **override):
    p = env["pending"]
    fields = {
        "pending_id": p.id, "action": p.action, "item_id": p.item_id,
        "content_sha256": p.content_sha256, "chain_head": p.chain_head,
        "approver": env["approver"], "nonce": p.nonce, "ts": p.ts,
    }
    fields.update(override)
    signed = {k: v for k, v in fields.items() if k != "pending_id"}
    sig = env["key"].sign(ap.approval_message(signed)).hex()
    return ap.Approval(**fields, signature=sig)


def consume(env, approval, payload=PAYLOAD, conn=None, now=None):
    conn = conn or env["conn"]
    with ap.transaction(conn):
        return ap.verify_and_consume(conn, approval, payload, ledger=env["ledger"], now=now)


def test_pending_binds_content_and_chain_head(env):
    p = env["pending"]
    assert p.content_sha256 == ap.payload_sha256(PAYLOAD)
    assert p.chain_head == env["ledger"].chain_head("run-1")
    assert len(p.nonce) >= 32 and p.ts <= time.time() + 1
    assert ap.Pending.load(env["conn"], p.id) == p


def test_unknown_action_rejected(env):
    with pytest.raises(ap.UnknownAction):
        ap.Pending.create("campaign.delete", "x", {}, ledger=env["ledger"], run_id="run-1")


def test_valid_approval_consumed_and_receipt_signed(env, tmp_path):
    verified = consume(env, sign(env))
    assert verified.content_sha256 == ap.payload_sha256(PAYLOAD)
    row = env["conn"].execute("SELECT status, approver FROM approvals").fetchone()
    assert row == ("approved", env["approver"])
    receipt = env["ledger"].receipts("run-1")[-1]
    assert receipt.kind == "approval" and receipt.details["sig"]
    path = tmp_path / "r.jsonl"
    path.write_text(env["ledger"].export_jsonl())
    registry = {env["approver"]: env["key"].public_key()}
    report = verify(path, env["ledger"].public_key, approver_keys=registry)
    assert report.ok, report.errors
    assert report.signatures == 1


def test_replay_rejected(env):
    approval = sign(env)
    consume(env, approval)
    with pytest.raises(ap.NonceUsed) as exc:
        consume(env, approval)
    assert exc.value.code == "nonce_used"


def test_edited_payload_rejected(env):
    edited = {**PAYLOAD, "message": "Full price list attached."}
    with pytest.raises(ap.ContentMismatch):
        consume(env, sign(env), payload=edited)


def test_signed_hash_must_match_pending(env):
    with pytest.raises(ap.ContentMismatch):
        consume(env, sign(env, content_sha256="0" * 64))


def test_wrong_key_rejected(env):
    approval = sign(env)
    other = Ed25519PrivateKey.generate()
    signed = {k: getattr(approval, k) for k in ap.SIGNED_FIELDS}
    forged = ap.Approval(**{**approval.__dict__,
                            "signature": other.sign(ap.approval_message(signed)).hex()})
    with pytest.raises(ap.BadSignature):
        consume(env, forged)


def test_unregistered_approver_rejected(env):
    with pytest.raises(ap.UnknownKey):
        consume(env, sign(env, approver="ed25519:" + "f" * 32))


def test_garbage_signature_rejected(env):
    approval = ap.Approval(**{**sign(env).__dict__, "signature": "zz"})
    with pytest.raises(ap.BadSignature):
        consume(env, approval)


def test_expired_rejected(env):
    with pytest.raises(ap.Expired):
        consume(env, sign(env), now=env["pending"].ts + ap.TTL_S + 1)


def test_future_rejected(env):
    with pytest.raises(ap.FromFuture):
        consume(env, sign(env), now=env["pending"].ts - ap.SKEW_S - 1)


def test_small_skew_tolerated(env):
    consume(env, sign(env), now=env["pending"].ts - ap.SKEW_S + 5)


def test_chain_head_mismatch_rejected(env):
    with pytest.raises(ap.ChainHeadMismatch):
        consume(env, sign(env, chain_head="1" * 64))


def test_missing_pending_rejected(env):
    with pytest.raises(ap.NotFound):
        consume(env, sign(env, pending_id="nope"))


def test_error_codes_are_distinct():
    codes = [cls.code for cls in ap.ApprovalError.__subclasses__()]
    assert len(codes) == len(set(codes)) >= 10


def test_consume_requires_open_transaction(env):
    with pytest.raises(RuntimeError, match="transaction"):
        ap.verify_and_consume(env["conn"], sign(env), PAYLOAD, ledger=env["ledger"])


def test_rollback_leaves_nonce_unconsumed(env):
    approval = sign(env)
    receipts_before = len(env["ledger"].receipts())
    with pytest.raises(RuntimeError, match="boom"), ap.transaction(env["conn"]):
        ap.verify_and_consume(env["conn"], approval, PAYLOAD, ledger=env["ledger"])
        raise RuntimeError("boom")
    assert env["conn"].execute("SELECT count(*) FROM nonces").fetchone() == (0,)
    assert len(env["ledger"].receipts()) == receipts_before  # no phantom approval receipt
    assert env["conn"].execute("SELECT status FROM approvals").fetchone() == ("pending",)
    consume(env, approval)  # still usable after the rollback
    assert env["ledger"].receipts()[-1].kind == "approval"


def test_non_transactional_ledger_refused(env):
    with pytest.raises(RuntimeError, match="transactional"), ap.transaction(env["conn"]):
        ap.verify_and_consume(env["conn"], sign(env), PAYLOAD, ledger=Ledger())
    assert env["conn"].execute("SELECT count(*) FROM nonces").fetchone() == (0,)


def test_nonce_row_already_present_wins_over_pending_status(env):
    env["conn"].execute("INSERT INTO nonces VALUES (?, ?)", (env["pending"].nonce, 1))
    with pytest.raises(ap.NonceUsed):
        consume(env, sign(env))


def test_two_concurrent_consumes_exactly_one_wins(env):
    approval = sign(env)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker():
        conn = store.connect(env["db"])
        ledger = Ledger(env["ledger_key"], store.SqliteStore(conn, key_epoch="test"))
        barrier.wait()
        try:
            with ap.transaction(conn):
                ap.verify_and_consume(conn, approval, PAYLOAD, ledger=ledger)
            results.append("ok")
        except ap.NonceUsed:
            results.append("nonce_used")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["nonce_used", "ok"]
    kinds = [r.kind for r in env["ledger"].receipts("run-1")]
    assert kinds.count("approval") == 1


def test_register_rejects_bad_key(env):
    with pytest.raises(ValueError):
        ap.Keys.register(env["conn"], "s", b"short")


def test_registry_lists_public_keys(env):
    reg = ap.Keys.registry(env["conn"])
    assert raw(reg[env["approver"]]) == raw(env["key"].public_key())
    stored = env["conn"].execute("SELECT session_sha256 FROM keys").fetchone()[0]
    assert "session-1" not in stored


def test_verify_rejects_forged_approval_receipt(env, tmp_path):
    consume(env, sign(env))
    path = tmp_path / "r.jsonl"
    path.write_text(env["ledger"].export_jsonl())
    other = Ed25519PrivateKey.generate().public_key()
    report = verify(path, env["ledger"].public_key, approver_keys={env["approver"]: other})
    assert not report.ok
    assert "bad approver signature" in " ".join(report.errors)
    report = verify(path, env["ledger"].public_key, approver_keys={})
    assert "unknown approver" in " ".join(report.errors)


def test_verify_requires_signature_when_registry_given(tmp_path):
    led = Ledger()
    led.append("run-1", agent="human", kind="approval", details={"content_sha256": "abc"})
    path = tmp_path / "r.jsonl"
    path.write_text(led.export_jsonl())
    assert verify(path, led.public_key).ok  # legacy mode, no registry
    report = verify(path, led.public_key, approver_keys={})
    assert not report.ok and "not signed" in report.errors[0]


def test_verify_rejects_chain_head_not_in_run(env, tmp_path):
    # A validly signed approval whose chain_head is not a receipt of this run.
    p = env["pending"]
    fields = {"action": p.action, "item_id": p.item_id, "content_sha256": p.content_sha256,
              "chain_head": "2" * 64, "approver": env["approver"], "nonce": p.nonce, "ts": p.ts}
    sig = env["key"].sign(ap.approval_message(fields)).hex()
    env["ledger"].append("run-1", agent="human", kind="approval", details={**fields, "sig": sig})
    path = tmp_path / "r.jsonl"
    path.write_text(env["ledger"].export_jsonl())
    report = verify(path, env["ledger"].public_key,
                    approver_keys={env["approver"]: env["key"].public_key()})
    assert not report.ok and "chain_head" in " ".join(report.errors)
    assert json.loads(path.read_text().splitlines()[-2])["kind"] == "approval"


def test_signed_fields_are_not_redacted(env, tmp_path):
    """PII-looking signed fields stay byte-exact, so the approver signature still verifies."""
    payload = {"message": "Write to jan.kowalski@example.org or call +48 601 234 567"}
    with ap.transaction(env["conn"]):
        p = ap.Pending.create("reply.post", "lead jan@example.org 600 123 456", payload,
                              ledger=env["ledger"], run_id="run-1")
        p.save(env["conn"])
    env["pending"] = p
    consume(env, sign(env), payload=payload)
    receipt = env["ledger"].receipts("run-1")[-1]
    assert receipt.details["item_id"] == "lead jan@example.org 600 123 456"
    path = tmp_path / "r.jsonl"
    path.write_text(env["ledger"].export_jsonl())
    registry = {env["approver"]: env["key"].public_key()}
    report = verify(path, env["ledger"].public_key, approver_keys=registry)
    assert report.ok, report.errors
    assert report.signatures == 1


@pytest.mark.parametrize("field", ap.SIGNED_FIELDS)
def test_editing_any_signed_field_fails_verification(env, tmp_path, field):
    consume(env, sign(env))
    lines = env["ledger"].export_jsonl().splitlines()
    idx = next(i for i, line in enumerate(lines) if '"kind": "approval"' in line)
    rec = json.loads(lines[idx])
    value = rec["details"][field]
    rec["details"][field] = value + 1 if isinstance(value, int) else value[:-1] + "x"
    lines[idx] = json.dumps(rec)
    path = tmp_path / "r.jsonl"
    path.write_text("\n".join(lines) + "\n")
    registry = {env["approver"]: env["key"].public_key()}
    assert not verify(path, env["ledger"].public_key, approver_keys=registry).ok


def test_approver_signature_checked_even_with_ledger_key(env, tmp_path):
    """Even someone holding the ledger key cannot forge what the human signed."""
    consume(env, sign(env))
    receipts = env["ledger"].receipts("run-1")
    forged = Ledger(env["ledger_key"])
    for r in receipts:
        details = dict(r.details)
        if r.kind == "approval":
            details["item_id"] = "c-2"
        forged.append("run-1", agent=r.agent, kind=r.kind, details=details)
    path = tmp_path / "r.jsonl"
    path.write_text(forged.export_jsonl())
    registry = {env["approver"]: env["key"].public_key()}
    report = verify(path, env["ledger"].public_key, approver_keys=registry)
    assert any("bad approver signature" in e for e in report.errors)
