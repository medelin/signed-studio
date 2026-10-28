"""Google Search: campaign, ad group and ad are created PAUSED from the signed spec only,
once; a spec that is not the one signed never reaches Google; activation is its own
approval; ``studio verify`` rejects a log whose campaign was created with another spec."""

import copy

import pytest

from studio import executor
from studio.agents import google_buyer
from studio.agents.media_buyer import BuyError
from studio.approvals import BriefChanged, Keys
from studio.executor import ExecutionRefused
from studio.google.adapter import NotApproved, PausedOnly, SpecError, spec_sha256
from studio.receipts import verify
from tests.approve import approve
from tests.google import RUN, make_env, payload, spec


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def _verify(ledger, conn, tmp_path):
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    return verify(export, ledger.public_key, approver_keys=Keys.registry(conn))


def test_an_enabled_creation_payload_is_never_executed(env):
    conn, ledger, mock, _ = env
    s = spec()
    s["campaign"]["status"] = "ENABLED"
    p = payload(google=s)
    v = approve(conn, ledger, "google.create", "gcmp_en1", p, RUN)
    with pytest.raises(PausedOnly):
        google_buyer.create(conn, ledger, mock, v, p)
    assert mock.calls == []


def test_a_spec_hand_edited_after_signing_calls_nothing(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_ed1", p, RUN)
    tampered = copy.deepcopy(p)
    tampered["google"]["campaign"]["daily_budget_micros"] = 600_000_000
    with pytest.raises(ExecutionRefused) as exc:
        google_buyer.create(conn, ledger, mock, v, tampered)
    assert exc.value.code == "content_mismatch" and mock.calls == []


def test_a_signed_but_invalid_spec_calls_nothing(env):
    conn, ledger, mock, _ = env
    s = spec()
    s["ad"]["headlines"].append("H" * 31)
    p = payload(google=s)
    v = approve(conn, ledger, "google.create", "gcmp_bad1", p, RUN)  # even if signed
    with pytest.raises(SpecError):
        google_buyer.create(conn, ledger, mock, v, p)
    assert mock.calls == []


def test_a_payload_without_a_spec_calls_nothing(env):
    conn, ledger, mock, _ = env
    p = payload()
    del p["google"]
    v = approve(conn, ledger, "google.create", "gcmp_no1", p, RUN)
    with pytest.raises(BuyError) as exc:
        google_buyer.create(conn, ledger, mock, v, p)
    assert exc.value.code == "spec_changed" and mock.calls == []


def test_another_account_is_refused(env):
    conn, ledger, mock, _ = env
    p = payload(google=spec(customer_id="9999999999"))
    v = approve(conn, ledger, "google.create", "gcmp_ac1", p, RUN)
    with pytest.raises(NotApproved):
        google_buyer.create(conn, ledger, mock, v, p)
    assert mock.calls == []


def test_a_meta_approval_cannot_create_a_google_campaign(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "campaign.activate", "cmp_wrong1", {**p, "action":
                                                                  "campaign.activate"}, RUN)
    with pytest.raises(ExecutionRefused):
        google_buyer.create(conn, ledger, mock, v, p)
    assert mock.calls == []


def test_a_google_approval_runs_once(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_once1", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    with pytest.raises(ExecutionRefused) as exc:
        google_buyer.create(conn, ledger, mock, v, p)
    assert exc.value.code == "replay"


def test_the_adapter_refuses_a_write_outside_an_executor(env):
    conn, ledger, mock, _ = env
    from studio.google.mock import MockAdapter

    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_out1", p, RUN)
    bound = MockAdapter(conn=conn)
    with pytest.raises(NotApproved):  # approved, but no execution was claimed
        bound.create_search_campaign(p, v)
    assert bound.calls == []


def test_activation_needs_its_own_signed_approval_naming_the_campaign(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_act1", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    _, ap = google_buyer.request_activation(conn, ledger, v.item_id, 50)
    other = {**ap, "campaign": ap["campaign"].replace("/campaigns/", "/campaigns/9")}
    v2 = approve(conn, ledger, "google.activate", v.item_id, other, RUN)
    with pytest.raises(BuyError) as exc:
        google_buyer.activate(conn, ledger, mock, v2, other, 50)
    assert exc.value.code == "campaign_changed"
    assert not [c for c in mock.calls if c[0] == "activate"]


def test_activation_above_the_ceiling_at_execution_time_is_refused(env):
    conn, ledger, mock, _ = env
    p = payload(google=spec(daily_budget=40))
    v = approve(conn, ledger, "google.create", "gcmp_cl1", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    _, ap = google_buyer.request_activation(conn, ledger, v.item_id, 50)
    v2 = approve(conn, ledger, "google.activate", v.item_id, ap, RUN)
    with pytest.raises(BuyError) as exc:
        google_buyer.activate(conn, ledger, mock, v2, ap, 10)  # the ceiling was lowered
    assert exc.value.code == "over_ceiling"
    assert not [c for c in mock.calls if c[0] == "activate"]


def test_the_signed_message_carries_the_google_spec_hash(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_sig1", p, RUN)
    (receipt,) = [r for r in ledger.receipts(RUN) if r.kind == "approval"]
    assert receipt.details["spec_sha256"] == v.spec_sha256 == spec_sha256(p["google"])


def test_verify_rejects_a_step_that_created_another_spec(env, tmp_path):
    """The server itself (real ledger key) logs a campaign created with a bigger budget
    than the signed one: the step's spec hash is not the signed spec_sha256."""
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_vf1", p, RUN)
    other = copy.deepcopy(p["google"])
    other["campaign"]["daily_budget_micros"] *= 10
    executor.record_step(ledger, v, agent="google_buyer", adapter="mock", step="google_create",
                         status="ok", channel="google",
                         details={"spec_sha256": spec_sha256(other)})
    report = _verify(ledger, conn, tmp_path)
    assert not report.ok and any("spec_sha256" in e for e in report.errors)


def test_verify_rejects_a_google_step_that_names_no_spec(env, tmp_path):
    conn, ledger, mock, _ = env
    v = approve(conn, ledger, "google.create", "gcmp_vf2", payload(), RUN)
    executor.record_step(ledger, v, agent="google_buyer", adapter="mock", step="google_create",
                         status="ok", channel="google", details={})
    assert not _verify(ledger, conn, tmp_path).ok


def test_verify_rejects_a_google_step_outside_the_actions_steps(env, tmp_path):
    conn, ledger, mock, _ = env
    v = approve(conn, ledger, "google.create", "gcmp_vf3", payload(), RUN)
    executor.record_step(ledger, v, agent="google_buyer", adapter="mock", step="campaign",
                         status="ok", channel="google",
                         details={"spec_sha256": v.spec_sha256})
    report = _verify(ledger, conn, tmp_path)
    assert not report.ok and any("not a step" in e for e in report.errors)


def test_a_google_campaign_must_sign_the_confirmed_brief(env):
    conn, ledger, mock, _ = env
    from studio.receipts import BRIEF_KIND

    ledger.append(RUN, agent="owner", kind=BRIEF_KIND, output_sha256="a" * 64, details={
        "schema": "brief/v1", "brief_sha256": "a" * 64, "facts": 1, "open_questions": 0,
        "answered": 0, "struck": 0, "voice": ""})
    with pytest.raises(BriefChanged):  # the approval must sign the run's confirmed brief
        approve(conn, ledger, "google.create", "gcmp_vf4", payload(), RUN)
