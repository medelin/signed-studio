"""Attack suite (day 3 fix round, I3): ``studio verify`` checks every external step.

A ``tool_call`` receipt with ``side_effects: external`` is an adapter call that touched
Meta. Each one must name (``approval_id``) a valid approval of the same run, for an action
the step belongs to, before that approval's write and inside its TTL. Otherwise verify
reports "unauthorised external step" — even when the ledger signature is valid (a buggy or
compromised server path holding the ledger key)."""

import json

import pytest

from studio import db
from studio.agents import media_buyer
from studio.approvals import Keys
from studio.meta.mock import MockAdapter
from studio.receipts import APPROVAL_TTL_S, verify
from tests.approve import approve
from tests.attack.test_activation_chain import _created
from tests.attack.test_paused_only import PNG, RUN, payload


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    assets = tmp_path / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, tmp_path


def _report(conn, ledger, tmp_path, text=None):
    export = tmp_path / "receipts.jsonl"
    export.write_text(text if text is not None else ledger.export_jsonl())
    return verify(export, ledger.public_key, approver_keys=Keys.registry(conn))


def _step(ledger, run=RUN, **details):
    return ledger.append(run, agent="media_buyer", kind="tool_call", provider="meta-mock",
                         details={"tool": "activate_campaign", "status": "ok",
                                  "side_effects": "external", "adapter": "mock",
                                  **details})


def _unauthorised(report):
    return [e for e in report.errors if "unauthorised external step" in e]


def test_genuine_create_and_activation_verify(env):
    conn, ledger, data = env
    mock = MockAdapter()
    _created(conn, ledger, data, mock, "cmp_x1")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_x1", ceiling=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_x1", act, RUN)
    media_buyer.activate(conn, ledger, mock, va, act, 50)
    report = _report(conn, ledger, data)
    assert report.ok, report.errors


def test_external_step_without_approval_id_is_unauthorised(env):
    conn, ledger, data = env
    _step(ledger)
    assert _unauthorised(_report(conn, ledger, data))


def test_external_step_naming_an_unknown_approval_is_unauthorised(env):
    conn, ledger, data = env
    _step(ledger, approval_id="f" * 32)
    assert _unauthorised(_report(conn, ledger, data))


def test_external_step_using_another_runs_approval_is_unauthorised(env):
    conn, ledger, data = env
    mock = MockAdapter()
    _created(conn, ledger, data, mock, "cmp_x2")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_x2", ceiling=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_x2", act, RUN)
    ledger.append("run_other", agent="producer", kind="model_call")
    _step(ledger, run="run_other", approval_id=va.pending_id)
    assert _unauthorised(_report(conn, ledger, data))


def test_activation_steps_under_the_create_grant_are_unauthorised(env):
    """All three activation steps must be covered by the campaign.activate grant."""
    conn, ledger, data = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_x3", p, RUN)
    for level in ("ad", "adset", "campaign"):
        _step(ledger, tool="activate_" + level, approval_id=v.pending_id)
    assert len(_unauthorised(_report(conn, ledger, data))) == 3


def test_extra_step_after_the_write_consumed_the_approval_is_unauthorised(env):
    conn, ledger, data = env
    mock = MockAdapter()
    _created(conn, ledger, data, mock, "cmp_x4")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_x4", ceiling=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_x4", act, RUN)
    media_buyer.activate(conn, ledger, mock, va, act, 50)
    _step(ledger, approval_id=va.pending_id)  # a second activation riding on the first
    assert _unauthorised(_report(conn, ledger, data))


def test_external_step_after_the_ttl_is_unauthorised(env, monkeypatch):
    conn, ledger, data = env
    mock = MockAdapter()
    _created(conn, ledger, data, mock, "cmp_x5")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_x5", ceiling=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_x5", act, RUN)
    import time as _time
    later = _time.time() + APPROVAL_TTL_S + 60
    monkeypatch.setattr("studio.receipts.time.time", lambda: later)
    _step(ledger, approval_id=va.pending_id)
    assert _unauthorised(_report(conn, ledger, data))


def test_tampered_export_is_caught(env):
    """Editing an exported step's approval_id breaks the hash and the authorisation."""
    conn, ledger, data = env
    mock = MockAdapter()
    _created(conn, ledger, data, mock, "cmp_x6")
    lines = ledger.export_jsonl().splitlines()
    out = []
    for line in lines:
        r = json.loads(line)
        if r.get("kind") == "tool_call" and r["details"].get("tool") == "campaign":
            r["details"]["approval_id"] = "0" * 32
        out.append(json.dumps(r, sort_keys=True))
    report = _report(conn, ledger, data, "\n".join(out) + "\n")
    assert not report.ok and _unauthorised(report)
    assert any("hash mismatch" in e for e in report.errors)


def test_dispatcher_rejection_of_an_external_skill_is_not_an_external_step(env):
    from studio.agents.dispatcher import Dispatcher, ToolCall, ToolNotAvailable
    conn, ledger, data = env
    d = Dispatcher({}, ledger=ledger, run_id=RUN)
    with pytest.raises(ToolNotAvailable):
        d.execute(ToolCall("call_1", "activate_campaign", "{}"))
    report = _report(conn, ledger, data)
    assert report.ok, report.errors
