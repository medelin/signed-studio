"""The recorded Brief v1 loop with Google Search ticked, offline: the owner signs the Meta
campaign and, separately, the Google Search campaign (created paused after a dry run),
the Report page shows the Google rows in both languages, and ``studio verify`` accepts
the log; a Google spec changed after signing fails it."""

import json
import sqlite3

import httpx
import pytest

from studio import demo
from studio.google.adapter import spec_sha256
from studio.receipts import Ledger, Receipt, verify
from tests.e2e.test_replay_brief import _approvers, receipts


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("replay-google")

    def no_network(request):
        raise AssertionError(f"network call in replay: {request.url}")

    real = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda self, request: no_network(request)
    try:
        summary = demo.run("brief", data_dir=tmp, log=lambda _: None, google=6)
    finally:
        httpx.HTTPTransport.handle_request = real
    return summary, tmp


def test_both_campaigns_are_created_paused_and_the_log_verifies(loop):
    summary, _ = loop
    assert summary["campaign_status"] == "PAUSED" and summary["google_status"] == "PAUSED"
    assert summary["verify_exit"] == 0 and summary["replay"]["miss"] == 0
    names = [s["step"] for s in summary["steps"]]
    assert "signed google.create" in names


def test_the_google_approval_signs_the_brief_and_the_spec(loop):
    summary, tmp = loop
    run = [r for r in receipts(tmp) if r["run_id"] == summary["run_id"]]
    (confirmed,) = [r for r in run if r["kind"] == "brief.confirmed"]
    (approval,) = [r for r in run if r["kind"] == "approval"
                   and r["details"]["action"] == "google.create"]
    assert approval["details"]["brief_sha256"] == confirmed["details"]["brief_sha256"]
    conn = sqlite3.connect(tmp / "studio.db")
    payload = json.loads(conn.execute(
        "SELECT p.payload FROM pending_payloads p JOIN approvals a ON a.id = p.approval_id "
        "WHERE a.action = 'google.create'").fetchone()[0])
    assert approval["details"]["spec_sha256"] == spec_sha256(payload["google"])
    spec = payload["google"]
    assert spec["campaign"]["daily_budget_micros"] == 6_000_000
    assert all(len(h) <= 30 for h in spec["ad"]["headlines"])
    assert all(len(d) <= 90 for d in spec["ad"]["descriptions"])
    steps = [r["details"]["tool"] for r in run if r["kind"] == "tool_call"
             and r["provider"].startswith("google")]
    assert steps == ["google_validate", "google_create"]
    (write,) = [r for r in run if r["kind"] == "external_write"
                and r["details"].get("action") == "google.create"]
    assert write["details"]["spec_sha256"] == approval["details"]["spec_sha256"]


@pytest.mark.parametrize("tamper", [False, True])
def test_a_google_spec_changed_after_signing_fails_verify(loop, tmp_path, tamper):
    """The log cut right after the Google approval, then the server (real ledger key) logs
    the creation: with the spec that was signed it verifies; with another spec's hash it
    does not."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    summary, tmp = loop
    run = summary["run_id"]
    led = Ledger(load_pem_private_key((tmp / "ledger.key").read_bytes(), password=None))
    step = write = None
    for r in receipts(tmp):
        if r["run_id"] == run and r["details"].get("tool") == "google_create":
            step = r
        if r["run_id"] == run and r["kind"] == "external_write" \
                and r["details"].get("action") == "google.create":
            write = r
    approval = None
    for r in receipts(tmp):
        led._store.append_atomic(r["run_id"], lambda prev, ts, r=r: Receipt(**r))
        if r["run_id"] == run and r["kind"] == "approval" \
                and r["details"]["action"] == "google.create":
            approval = r
            break
    assert approval and step and write
    other = "ab" * 32 if tamper else step["details"]["spec_sha256"]
    led.append(run, agent="google_buyer", kind="tool_call", provider=step["provider"],
               details={**step["details"], "spec_sha256": other})
    led.append(run, agent="google_buyer", kind="external_write",
               output_sha256=write["output_sha256"], provider=write["provider"],
               details={**write["details"], "spec_sha256": other})
    path = tmp_path / "t.jsonl"
    path.write_text(led.export_jsonl())
    report = verify(path, led.public_key, approver_keys=_approvers(tmp))
    assert report.ok is not tamper, report.errors
    if tamper:
        assert any("spec_sha256" in e for e in report.errors)
