"""``studio verify``: a good export exits 0; every tamper class exits 2 and names the
receipt (or, when the receipt is gone, the run and the last receipt that is left).

Exit codes: 0 chain OK, 2 chain INVALID, 3 the check could not run (usage, I/O, network).
"""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio import approvals as ap
from studio import cli, db
from studio.receipts import Receipt, public_key_raw, sha256_hex


def _build(tmp_path):
    """One run: model call, a signed reply approval, its adapter step and its write."""
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    led = db.open_ledger(c, tmp_path / "var" / "ledger.key")
    led.append("run-1", agent="community", kind="model_call", model="muse-spark-1.3")
    led.append("run-1", agent="community", kind="tool_call", details={"tool": "draft"})
    key = Ed25519PrivateKey.generate()
    approver = ap.Keys.register(c, "s1", public_key_raw(key.public_key()))
    payload = {"message": "hello"}
    with db.transaction(c):
        p = ap.Pending.create("reply.post", "c-1", payload, ledger=led, run_id="run-1")
        p.save(c)
    approval = ap.Approval(pending_id=p.id, **p.fields(approver),
                           signature=key.sign(p.message(approver)).hex())
    with db.transaction(c):
        ok = ap.verify_and_consume(c, approval, payload, ledger=led, session_id="s1")
    led.append("run-1", agent="community", kind="tool_call",
               details={"tool": "reply.post", "side_effects": "external",
                        "approval_id": ok.pending_id})
    led.append("run-1", agent="community", kind="external_write",
               output_sha256=p.content_sha256, details=ok.write_details())
    led.append("run-2", agent="producer", kind="model_call", model="muse-spark-1.3")
    keys = tmp_path / "approvers.json"
    keys.write_text(json.dumps({a: public_key_raw(k).hex()
                                for a, k in ap.Keys.registry(c).items()}))
    return c, led, keys, tmp_path / "var" / "ledger.pub"


@pytest.fixture
def built(tmp_path):
    return _build(tmp_path)


def _lines(led):
    return [json.loads(line) for line in led.export_jsonl().splitlines()]


def _write(tmp_path, lines):
    path = tmp_path / "receipts.jsonl"
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
    return path


def _run(path, keys, pub, *extra):
    return cli.main(["verify", str(path), "--key-file", str(pub), "--approver-keys",
                     str(keys), *extra])


def _receipts(lines):
    return [x for x in lines if "manifest" not in x]


def test_good_export_exits_0(built, tmp_path, capsys):
    _, led, keys, pub = built
    path = _write(tmp_path, _lines(led))
    assert _run(path, keys, pub, "--min-seq", str(led.manifest()["seq"])) == 0
    assert "chain OK, 6 receipts, 1 signatures valid" in capsys.readouterr().out


def _expect_invalid(path, keys, pub, capsys, *needles, extra=()):
    rc = _run(path, keys, pub, *extra)
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "chain INVALID" in out
    for needle in needles:
        assert needle in out, (needle, out)
    return out


def test_edited_field(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    lines[0]["model"] = "forged"
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, lines[0]["id"],
                    "hash mismatch")


def test_recomputed_hash_without_the_ledger_key(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    rec = Receipt(**lines[0])
    body = {**rec.body(), "cost_usd": 0.0, "tokens_in": 1}
    forged = Receipt(**body)
    lines[0] = {**body, "hash": forged.compute_hash(), "sig": rec.sig}
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, rec.id, "bad ledger signature")


def test_deleted_receipt_in_the_middle(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    gone, after = lines[1], lines[2]
    del lines[1]
    out = _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, after["id"],
                          "broken link")
    assert gone["id"] not in out


def test_reordered_receipts(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    lines[0], lines[1] = lines[1], lines[0]
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, lines[0]["id"], "broken link")


def test_duplicated_receipt(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    lines.insert(1, dict(lines[0]))
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, lines[0]["id"],
                    "appears more than once")


def test_truncated_tail_names_the_run_and_the_last_receipt_left(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    run1 = [x for x in _receipts(lines) if x["run_id"] == "run-1"]
    lines = [x for x in lines if x.get("id") != run1[-1]["id"]]
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, "run run-1", run1[-2]["id"],
                    "truncated")


def test_deleted_whole_run(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = [x for x in _lines(led) if x.get("run_id") != "run-2"]
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, "run run-2",
                    "missing from the log")


def test_rolled_back_export(built, tmp_path, capsys):
    _, led, keys, pub = built
    path = _write(tmp_path, _lines(led))
    _expect_invalid(path, keys, pub, capsys, "older than the latest known",
                    extra=("--min-seq", str(led.manifest()["seq"] + 1)))


def test_missing_or_extra_manifest(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    _expect_invalid(_write(tmp_path, _receipts(lines)), keys, pub, capsys,
                    "exactly one signed manifest")
    _expect_invalid(_write(tmp_path, lines + [lines[-1]]), keys, pub, capsys,
                    "exactly one signed manifest")


def test_forged_manifest(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    lines[-1]["manifest"]["seq"] += 100
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, "bad manifest signature")


def test_approval_signed_by_another_key(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    approval = next(x for x in lines if x.get("kind") == "approval")
    registry = json.loads(keys.read_text())
    other = Ed25519PrivateKey.generate().public_key()
    keys.write_text(json.dumps({a: public_key_raw(other).hex() for a in registry}))
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, approval["id"],
                    "bad approver signature")


def test_approval_by_an_unknown_approver(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    approval = next(x for x in lines if x.get("kind") == "approval")
    keys.write_text("{}")
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, approval["id"],
                    "unknown approver")


# The server holds the ledger key, so a compromised server can sign anything; these
# classes are what verify still catches in a log signed by the real key.

def _server_write(led, run, **details):
    return led.append(run, agent="media_buyer", kind="external_write",
                      output_sha256=sha256_hex(b"x"), details=details)


def test_external_write_without_approval(tmp_path, capsys):
    _, led, keys, pub = _build(tmp_path)
    bad = _server_write(led, "run-2", action="reply.post", item_id="c-9")
    _expect_invalid(_write(tmp_path, _lines(led)), keys, pub, capsys, bad.id,
                    "without approval")


def test_second_write_on_one_approval(tmp_path, capsys):
    _, led, keys, pub = _build(tmp_path)
    first = next(r for r in led.receipts("run-1") if r.kind == "external_write")
    again = led.append("run-1", agent="community", kind="external_write",
                       output_sha256=first.output_sha256, details=first.details)
    _expect_invalid(_write(tmp_path, _lines(led)), keys, pub, capsys, again.id,
                    "already used")


def test_unauthorised_external_step(tmp_path, capsys):
    _, led, keys, pub = _build(tmp_path)
    step = led.append("run-2", agent="media_buyer", kind="tool_call",
                      details={"tool": "campaign", "side_effects": "external"})
    _expect_invalid(_write(tmp_path, _lines(led)), keys, pub, capsys, step.id,
                    "unauthorised external step")


def test_receipt_dated_after_the_manifest(tmp_path, capsys, monkeypatch):
    import time as _time

    _, led, keys, pub = _build(tmp_path)
    real = _time.time
    monkeypatch.setattr("studio.receipts.time.time", lambda: real() + 3600)
    future = led.append("run-2", agent="producer", kind="model_call", model="muse-spark-1.3")
    monkeypatch.setattr("studio.receipts.time.time", real)
    _expect_invalid(_write(tmp_path, _lines(led)), keys, pub, capsys, future.id,
                    "after the manifest")


def test_unreadable_receipt_names_its_id(built, tmp_path, capsys):
    _, led, keys, pub = built
    lines = _lines(led)
    lines[0]["tokens_in"] = "many"
    _expect_invalid(_write(tmp_path, lines), keys, pub, capsys, lines[0]["id"],
                    "unreadable receipt")


def test_garbage_line(built, tmp_path, capsys):
    _, led, keys, pub = built
    path = tmp_path / "receipts.jsonl"
    path.write_text(led.export_jsonl() + "{not json\n")
    _expect_invalid(path, keys, pub, capsys, "line 8", "unreadable line")


def test_usage_errors_exit_3(built, tmp_path, capsys):
    _, led, keys, pub = built
    path = _write(tmp_path, _lines(led))
    assert cli.main(["verify", str(tmp_path / "missing.jsonl"), "--key-file", str(pub),
                     "--approver-keys", str(keys)]) == 3
    assert cli.main(["verify", str(path), "--key-file", str(pub)]) == 3
    assert capsys.readouterr().err.count("error:") == 2
