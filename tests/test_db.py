import json
import os
import re
import sqlite3
import stat
import threading

import pytest

from studio import approvals as ap
from studio import db
from studio.receipts import key_id, verify
from studio.settings import ConfigError

TABLES = {
    "runs", "receipts", "approvals", "nonces", "keys", "briefs", "creatives", "campaigns",
    "comments", "leads", "tasks", "reports", "sandbox_meta", "ledger_meta",
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"\+?\d[\d \-()]{7,}\d")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    yield c
    c.close()


def demo_counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("briefs", "comments", "leads", "tasks", "campaigns")}


def test_schema_has_all_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_init_is_idempotent(conn):
    db.init(conn)
    db.init(conn)


def test_seed_idempotent(conn):
    db.seed(conn)
    first = demo_counts(conn)
    db.seed(conn)
    assert demo_counts(conn) == first
    assert all(v > 0 for v in first.values())


def test_seed_files_synthetic_only():
    files = sorted(db.SEED_DIR.glob("*.json"))
    assert files
    for path in files:
        text = "\n".join(db._strings(json.loads(path.read_text())))
        for email in EMAIL.findall(text):
            assert email.endswith("@example.com"), (path.name, email)
        for phone in PHONE.findall(text):
            assert re.fullmatch(r"\+48 000 000 \d{3}", phone), (path.name, phone)


def test_seed_refuses_real_looking_contacts(conn, tmp_path):
    bad = tmp_path / "seed"
    bad.mkdir()
    (bad / "leads.json").write_text(json.dumps([{
        "id": "l-x", "name": "X", "email": "x@gmail.com", "phone": "+48 000 000 001",
        "source": "form", "message": "", "created_ts": 1,
    }]))
    with pytest.raises(ValueError, match="example.com"):
        db.seed(conn, seed_dir=bad)


def test_reset_keeps_receipts_nonces_and_keys(conn, tmp_path):
    db.seed(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    ledger.append("run-1", agent="producer", kind="model_call")
    conn.execute("INSERT INTO nonces VALUES ('n_x', 1)")
    conn.execute("DELETE FROM leads")
    seq_before = ledger.manifest()["seq"]
    db.reset_demo(conn)
    assert conn.execute("SELECT count(*) FROM receipts").fetchone() == (1,)
    assert conn.execute("SELECT count(*) FROM nonces").fetchone() == (1,)
    assert demo_counts(conn)["leads"] > 0  # re-seeded
    assert ledger.manifest()["seq"] == seq_before
    marker = conn.execute("SELECT value FROM sandbox_meta WHERE key='last_reset'").fetchone()
    assert marker is not None


def test_reset_without_receipts_still_advances_seq(conn, tmp_path):
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    ledger.append("run-1", agent="a", kind="model_call")
    seq = ledger.manifest()["seq"]
    db.reset_demo(conn, keep_receipts=False)
    assert ledger.receipts() == []
    assert ledger.manifest()["seq"] > seq  # durable counter never goes back


def test_ledger_key_file_created_0600(tmp_path):
    path = tmp_path / "keys" / "ledger.key"
    key = db.load_or_create_ledger_key(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    again = db.load_or_create_ledger_key(path)
    assert key_id(again.public_key()) == key_id(key.public_key())
    pub = (path.parent / "ledger.pub").read_text().strip()
    assert len(pub) == 64  # raw public key, hex


def test_ledger_key_with_loose_mode_refused(tmp_path):
    path = tmp_path / "ledger.key"
    db.load_or_create_ledger_key(path)
    os.chmod(path, 0o640)
    with pytest.raises(ConfigError, match="permissions"):
        db.load_or_create_ledger_key(path)


def test_receipts_persist_across_reopen(tmp_path):
    path = tmp_path / "studio.db"
    key = tmp_path / "ledger.key"
    c1 = db.connect(path)
    db.init(c1)
    led = db.open_ledger(c1, key)
    r1 = led.append("run-1", agent="a", kind="model_call", model="muse-spark-1.3")
    c1.close()
    c2 = db.connect(path)
    led2 = db.open_ledger(c2, key)
    assert led2.chain_head("run-1") == r1.hash
    r2 = led2.append("run-1", agent="b", kind="model_call")
    assert r2.prev == r1.hash
    out = tmp_path / "r.jsonl"
    out.write_text(led2.export_jsonl())
    report = verify(out, led2.public_key, min_seq=2)
    assert report.ok, report.errors
    assert report.count == 2


def test_seq_is_durable_and_monotonic(tmp_path):
    path, key = tmp_path / "studio.db", tmp_path / "ledger.key"
    c = db.connect(path)
    db.init(c)
    led = db.open_ledger(c, key)
    led.append("run-1", agent="a", kind="model_call")
    led.append("run-1", agent="a", kind="model_call")
    s1 = led.manifest()["seq"]
    c.close()
    c = db.connect(path)
    assert db.open_ledger(c, key).manifest()["seq"] == s1


def test_manifest_carries_signed_key_epoch(tmp_path, conn):
    led = db.open_ledger(conn, tmp_path / "ledger.key")
    led.append("run-1", agent="a", kind="model_call")
    man = led.manifest()
    assert man["epoch"] == key_id(led.public_key)
    out = tmp_path / "r.jsonl"
    lines = led.export_jsonl().splitlines()
    m = json.loads(lines[-1])
    m["manifest"]["epoch"] = "ed25519:" + "0" * 32
    out.write_text("\n".join([*lines[:-1], json.dumps(m)]) + "\n")
    assert "bad manifest signature" in verify(out, led.public_key).errors


def test_changed_ledger_key_refused_when_receipts_exist(tmp_path, conn):
    led = db.open_ledger(conn, tmp_path / "a.key")
    led.append("run-1", agent="a", kind="model_call")
    with pytest.raises(ConfigError, match="ledger key"):
        db.open_ledger(conn, tmp_path / "b.key")


def test_forked_chain_impossible(conn, tmp_path):
    led = db.open_ledger(conn, tmp_path / "ledger.key")
    r1 = led.append("run-1", agent="a", kind="model_call")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO receipts (id, run_id, prev, hash, ts, body) VALUES (?,?,?,?,?,?)",
            ("x", "run-1", r1.prev, "f" * 64, 0.0, "{}"),
        )


def test_concurrent_appends_keep_one_chain(tmp_path):
    path, key = tmp_path / "studio.db", tmp_path / "ledger.key"
    c = db.connect(path)
    db.init(c)
    led = db.open_ledger(c, key)

    def work():
        for _ in range(20):
            led.append("run-1", agent="a", kind="model_call")

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = tmp_path / "r.jsonl"
    out.write_text(led.export_jsonl())
    report = verify(out, led.public_key)
    assert report.ok and report.count == 80


def test_approval_and_receipt_commit_atomically(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    path = tmp_path / "studio.db"
    c = db.connect(path)
    db.init(c)
    led = db.open_ledger(c, tmp_path / "ledger.key")
    led.append("run-1", agent="community", kind="model_call")
    key = Ed25519PrivateKey.generate()
    approver = ap.Keys.register(c, "s1", key.public_key().public_bytes(Encoding.Raw,
                                                                      PublicFormat.Raw))
    payload = {"message": "hello"}
    with db.transaction(c):
        p = ap.Pending.create("reply.post", "c-1", payload, ledger=led, run_id="run-1")
        p.save(c)
    approval = ap.Approval(pending_id=p.id, **p.fields(approver),
                           signature=key.sign(p.message(approver)).hex())
    with pytest.raises(RuntimeError, match="boom"), db.transaction(c):
        ap.verify_and_consume(c, approval, payload, ledger=led, session_id="s1")
        raise RuntimeError("boom")
    assert c.execute("SELECT count(*) FROM receipts").fetchone() == (1,)  # rolled back too
    with db.transaction(c):
        ap.verify_and_consume(c, approval, payload, ledger=led, session_id="s1")
    assert led.receipts("run-1")[-1].kind == "approval"
    out = tmp_path / "r.jsonl"
    out.write_text(led.export_jsonl())
    report = verify(out, led.public_key, approver_keys=ap.Keys.registry(c))
    assert report.ok and report.signatures == 1


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def test_seeded_run_export_has_no_contact_data(tmp_path):
    """A run over seeded comments (with e-mails and phones) exports no contact data."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from studio.receipts import _EMAIL, _PHONE, public_key_raw

    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    db.seed(c)
    led = db.open_ledger(c, tmp_path / "ledger.key")
    key = Ed25519PrivateKey.generate()
    approver = ap.Keys.register(c, "s1", public_key_raw(key.public_key()))
    for cid, text in c.execute("SELECT id, text FROM comments ORDER BY id").fetchall():
        led.append("run-inbox", agent="community", kind="model_call",
                   details={"comment_id": cid, "comment": text})
        payload = {"comment_id": cid, "message": f"Thanks! Reply to: {text}"}
        with db.transaction(c):
            p = ap.Pending.create("reply.post", cid, payload, ledger=led, run_id="run-inbox")
            p.save(c)
        approval = ap.Approval(pending_id=p.id, **p.fields(approver),
                               signature=key.sign(p.message(approver)).hex())
        with db.transaction(c):
            ok = ap.verify_and_consume(c, approval, payload, ledger=led, session_id="s1")
        led.append("run-inbox", agent="media_buyer", kind="external_write",
                   output_sha256=p.content_sha256,
                   details={"comment_id": cid, **ok.write_details()})
    export = led.export_jsonl()
    for line in export.splitlines():
        for value in _strings(json.loads(line)):
            if re.fullmatch(r"[0-9a-f]{64}|[0-9a-f]{128}", value):
                continue  # digests and signatures are opaque by construction
            assert not _EMAIL.search(value), value
            assert not _PHONE.search(value), value
    out = tmp_path / "r.jsonl"
    out.write_text(export)
    report = verify(out, led.public_key, approver_keys=ap.Keys.registry(c))
    assert report.ok, report.errors
    assert report.signatures == 5


def test_daily_spend_survives_a_restart(tmp_path):
    """I6: the budget is rebuilt from the durable model_call receipts of the day."""
    from studio.muse.client import MuseClient
    from studio.settings import Settings

    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    led = db.open_ledger(c, tmp_path / "ledger.key")
    led.append("run-1", agent="producer", kind="model_call", cost_usd=0.4)
    c.close()
    c2 = db.connect(tmp_path / "studio.db")
    led2 = db.open_ledger(c2, tmp_path / "ledger.key")
    client = MuseClient.from_settings(Settings.load({"STUDIO_DAILY_BUDGET_USD": "1"}), led2)
    assert client.budget is not None
    assert client.budget.spent() == pytest.approx(0.4)
