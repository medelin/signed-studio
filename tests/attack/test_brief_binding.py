"""The confirmed Brief v1 is part of what the human signs.

A ``campaign.create`` approval made from a confirmed brief signs its ``brief_sha256``. A
brief changed after signing is refused by the server (``BriefChanged``) and by
``studio verify`` (the write no longer matches the run's brief); an approval that leaves
the brief out, or names another brief, fails verify; so does a brief confirmed with an
open ``[FACT]`` question.
"""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio import approvals as ap
from studio import db
from studio.receipts import (
    BRIEF_KIND,
    Receipt,
    approval_message,
    public_key_raw,
    sha256_hex,
    verify,
)

BRIEF_A = sha256_hex("brief a")
BRIEF_B = sha256_hex("brief b")
PAYLOAD = {"action": "campaign.create", "brief_sha256": BRIEF_A, "ad": {"headline": "x"}}


class Log:
    def __init__(self, tmp_path):
        self.conn = db.connect(tmp_path / "studio.db")
        db.init(self.conn)
        self.led = db.open_ledger(self.conn, tmp_path / "ledger.key")
        self.key = Ed25519PrivateKey.generate()
        self.approver = ap.Keys.register(self.conn, "s1", public_key_raw(self.key.public_key()))
        self.tmp = tmp_path

    def confirm(self, digest, open_questions=0):
        return self.led.append("run-1", agent="owner", kind=BRIEF_KIND, output_sha256=digest,
                               details={"schema": "brief/v1", "brief_sha256": digest,
                                        "facts": 3, "open_questions": open_questions,
                                        "answered": 1, "struck": 0, "voice": ""})

    def pending(self, brief=BRIEF_A, payload=PAYLOAD):
        with db.transaction(self.conn):
            p = ap.Pending.create("campaign.create", "cmp_1a2b3c", payload, ledger=self.led,
                                  run_id="run-1", brief_sha256=brief)
            p.save(self.conn)
        return p

    def approve(self, p, payload=PAYLOAD):
        a = ap.Approval(pending_id=p.id, **{k: v for k, v in p.fields(self.approver).items()
                                            if k != "brief_sha256"},
                        brief_sha256=p.brief_sha256,
                        signature=self.key.sign(p.message(self.approver)).hex())
        with db.transaction(self.conn):
            return ap.verify_and_consume(self.conn, a, payload, ledger=self.led,
                                         session_id="s1")

    def write(self, v):
        self.led.append("run-1", agent="media_buyer", kind="external_write",
                        output_sha256=v.content_sha256, details=v.write_details())

    def check(self):
        path = self.tmp / "export.jsonl"
        path.write_text(self.led.export_jsonl())
        return verify(path, self.led.public_key, approver_keys=ap.Keys.registry(self.conn))


def test_a_brief_bound_approval_verifies(tmp_path):
    log = Log(tmp_path)
    log.led.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3")
    log.confirm(BRIEF_A)
    p = log.pending()
    assert p.fields(log.approver)["brief_sha256"] == BRIEF_A
    assert json.loads(p.message(log.approver))["brief_sha256"] == BRIEF_A  # signed
    log.write(log.approve(p))
    report = log.check()
    assert report.ok, report.errors
    (approval,) = [r for r in log.led.receipts("run-1") if r.kind == "approval"]
    assert approval.details["brief_sha256"] == BRIEF_A


def test_pending_refuses_a_brief_the_run_never_confirmed(tmp_path):
    log = Log(tmp_path)
    log.confirm(BRIEF_A)
    with pytest.raises(ap.BriefChanged):
        log.pending(brief=BRIEF_B)


def test_server_refuses_to_consume_after_the_brief_changed(tmp_path):
    log = Log(tmp_path)
    log.confirm(BRIEF_A)
    p = log.pending()
    log.confirm(BRIEF_B)  # a fact edited after the approval was requested
    with pytest.raises(ap.BriefChanged):
        log.approve(p)


def test_verify_rejects_a_write_after_the_brief_changed(tmp_path):
    log = Log(tmp_path)
    log.confirm(BRIEF_A)
    v = log.approve(log.pending())
    log.confirm(BRIEF_B)  # signed by the real ledger key: the server itself lies
    log.write(v)
    report = log.check()
    assert not report.ok
    assert any("brief changed after the approval" in e for e in report.errors)


def test_verify_rejects_an_approval_that_leaves_the_brief_out(tmp_path):
    log = Log(tmp_path)
    bare = {"action": "campaign.create"}
    with pytest.raises(ap.BriefChanged):  # the server refuses it once a brief exists ...
        log.confirm(BRIEF_A)
        log.pending(brief="", payload=bare)
    (tmp_path / "b").mkdir()
    log = Log(tmp_path / "b")
    log.led.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3")
    p = log.pending(brief="", payload=bare)  # ... so it must come before the brief
    log.confirm(BRIEF_A)
    v = log.approve(p, payload=bare)
    log.write(v)
    report = log.check()
    assert any("does not sign the run's confirmed brief" in e for e in report.errors)


def test_verify_rejects_an_approval_for_another_brief(tmp_path):
    log = Log(tmp_path)
    log.confirm(BRIEF_A)
    p = log.pending()
    log.led.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3")
    # a server that rewrites the approval's brief: re-signed by the ledger, not the human
    log.led.append("run-1", agent="human", kind="approval", details={
        **p.fields(log.approver), "brief_sha256": BRIEF_B,
        "sig": log.key.sign(approval_message({**p.fields(log.approver),
                                              "brief_sha256": BRIEF_B})).hex(),
        "pending_id": "forged"})
    report = log.check()
    assert any("another brief than the run's confirmed brief" in e for e in report.errors)


def test_the_approver_signature_covers_the_brief_hash(tmp_path):
    log = Log(tmp_path)
    log.confirm(BRIEF_A)
    p = log.pending()
    log.approve(p)
    lines = [json.loads(x) for x in log.led.export_jsonl().splitlines()]
    for x in lines:
        if x.get("kind") == "approval":
            x["details"]["brief_sha256"] = BRIEF_B
            body = Receipt(**x).body()
            x["hash"] = Receipt(**body).compute_hash()  # re-hashed, old ledger signature
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    report = verify(path, log.led.public_key, approver_keys=ap.Keys.registry(log.conn))
    assert any("bad approver signature" in e or "bad ledger signature" in e
               for e in report.errors)


def test_a_brief_confirmed_with_open_questions_is_invalid(tmp_path):
    log = Log(tmp_path)
    log.confirm(BRIEF_A, open_questions=1)
    report = log.check()
    assert any("open [FACT] questions" in e for e in report.errors)


def test_brief_receipt_must_carry_its_own_hash(tmp_path):
    log = Log(tmp_path)
    log.led.append("run-1", agent="owner", kind=BRIEF_KIND, output_sha256=BRIEF_A,
                   details={"schema": "brief/v1", "brief_sha256": BRIEF_B,
                            "open_questions": 0})
    assert any("is not its output hash" in e for e in log.check().errors)


def test_receipt_keeps_the_digest_byte_exact():
    digit_heavy = "1234567890123" + "a" * 51  # a long digit run that redaction would hit
    from studio.receipts import Ledger
    led = Ledger()
    r = led.append("run-1", agent="human", kind=BRIEF_KIND, output_sha256=digit_heavy,
                   details={"schema": "brief/v1", "brief_sha256": digit_heavy,
                            "open_questions": 0, "note": "call 1234567890123"})
    assert r.details["brief_sha256"] == digit_heavy and "[phone]" in r.details["note"]
