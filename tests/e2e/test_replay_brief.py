"""The recorded Brief v1 loop offline: owner's words → draft → one [FACT] answered, the
rest struck → confirmed brief → run → signed campaign that binds ``brief_sha256`` →
``studio verify`` OK, and a fact edited afterwards breaks it."""

import json
import sqlite3

import httpx
import pytest

from studio import brief as bv
from studio import demo, replay
from studio.receipts import Ledger, verify


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("replay-brief")

    def no_network(request):
        raise AssertionError(f"network call in replay: {request.url}")

    real = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda self, request: no_network(request)
    try:
        summary = demo.run("brief", data_dir=tmp, log=lambda _: None)
    finally:
        httpx.HTTPTransport.handle_request = real
    return summary, tmp


def receipts(tmp):
    return [json.loads(x) for x in (tmp / "receipts.jsonl").read_text().splitlines()
            if '"manifest"' not in x]


def test_brief_loop_replays_and_verifies(loop):
    summary, _ = loop
    assert summary["campaign_status"] == "PAUSED" and summary["verify_exit"] == 0
    assert summary["replay"]["miss"] == 0
    steps = [s["step"] for s in summary["steps"]]
    assert steps[1:3] == ["brief drafted", "brief confirmed"]


def test_a_set_recorded_before_the_sam_check_degrades_to_not_recorded(loop):
    _, tmp = loop
    rs = receipts(tmp)
    checks = [r["details"] for r in rs if r["details"].get("tool") == "sam.check"]
    assert checks and {c["status"] for c in checks} == {"not_recorded"}
    reviews = [r["details"] for r in rs if r["kind"] == "review"]
    assert reviews and all(d["sam"] == {"status": "not_recorded", "findings": ["S1:info"]}
                           for d in reviews)


def test_the_campaign_approval_signs_the_confirmed_brief(loop):
    summary, tmp = loop
    run = [r for r in receipts(tmp) if r["run_id"] == summary["run_id"]]
    kinds = [r["kind"] for r in run]
    assert kinds[:2] == ["model_call", "brief.confirmed"]  # draft, then the owner
    confirmed = run[1]
    assert confirmed["details"]["schema"] == "brief/v1"
    assert confirmed["details"]["open_questions"] == 0
    assert confirmed["details"]["answered"] == 1
    (approval,) = [r for r in run if r["kind"] == "approval"
                   and r["details"]["action"] == "campaign.create"]
    assert approval["details"]["brief_sha256"] == confirmed["details"]["brief_sha256"]
    conn = sqlite3.connect(tmp / "studio.db")
    stored = json.loads(conn.execute("SELECT brief_v1 FROM briefs WHERE run_id = ?",
                                     (summary["run_id"],)).fetchone()[0])
    brief = bv.BriefV1.model_validate({k: v for k, v in stored.items() if k != "schema"})
    assert bv.brief_sha256(brief) == confirmed["details"]["brief_sha256"]
    assert brief.open_questions == []
    payload = json.loads(conn.execute("SELECT payload FROM pending_payloads WHERE run_id = ? "
                                      "ORDER BY created_ts LIMIT 1",
                                      (summary["run_id"],)).fetchone()[0])
    assert payload["brief_sha256"] == confirmed["details"]["brief_sha256"]
    assert payload["campaign"]["daily_budget_minor"] == int(brief.budget.amount) * 100


def _copy_until_approval(tmp, run_id):
    """The export as a live ledger with the same key, the campaign run cut right after
    its approval (original receipts, hashes and signatures kept)."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    from studio.receipts import Receipt

    led = Ledger(load_pem_private_key((tmp / "ledger.key").read_bytes(), password=None))
    cut = False
    write = None
    for r in receipts(tmp):
        if r["run_id"] == run_id and cut:
            if r["kind"] == "external_write" and write is None:
                write = r
            continue
        led._store.append_atomic(r["run_id"], lambda prev, ts, r=r: Receipt(**r))
        if r["run_id"] == run_id and r["kind"] == "approval":
            cut = True
    return led, write


def _approvers(tmp):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = json.loads((tmp / "approver-keys.json").read_text())
    return {a: Ed25519PublicKey.from_public_bytes(bytes.fromhex(h)) for a, h in keys.items()}


@pytest.mark.parametrize("edit", [False, True])
def test_a_fact_edited_after_signing_fails_verify(loop, tmp_path, edit):
    """A server that re-confirms an edited brief between the signature and the write
    (signed with the real ledger key): verify names the broken binding; without the edit
    the same log verifies."""
    summary, tmp = loop
    led, write = _copy_until_approval(tmp, summary["run_id"])
    if edit:
        led.append(summary["run_id"], agent="owner", kind="brief.confirmed",
                   output_sha256="ab" * 32,
                   details={"schema": "brief/v1", "brief_sha256": "ab" * 32, "facts": 1,
                            "open_questions": 0})
    led.append(summary["run_id"], agent="media_buyer", kind="external_write",
               output_sha256=write["output_sha256"], details=write["details"])
    path = tmp_path / "t.jsonl"
    path.write_text(led.export_jsonl())
    report = verify(path, led.public_key, approver_keys=_approvers(tmp))
    assert report.ok is not edit, report.errors
    if edit:
        assert any("brief changed after the approval" in e for e in report.errors)


def test_the_brief_set_is_a_real_recording():
    data = replay.load_set("brief")
    assert data["_provenance"] == "recorded"
    assert data["draft_text"] == demo.BRIEFS["brief"]["draft_text"]
    text = json.dumps(data).lower()
    assert "authorization" not in text and "bearer" not in text
    names = {replay.schema_name(i["request"]) for i in data["interactions"]}
    assert {"BriefV1", "Plan", "CopyPack", "Review"} <= names
