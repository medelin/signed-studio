"""The pixel check in a run and in front of ``campaign.create``: the producer checks the
pixel of a website leads or sales brief (one read-only receipt, the result signed in the
payload and in the run state), skips it for an instant form or traffic, and a failed check
can be neither signed nor executed — nothing reaches Meta."""

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from studio import brief as brief_v1
from studio import db, leadads
from studio.agents import media_buyer, producer
from studio.agents.dispatcher import Dispatcher
from studio.app import create_app
from studio.approvals import Pending
from studio.meta import objectives, pixel
from studio.meta.adapter import MOCK_PAGE
from studio.meta.mock import MockAdapter
from studio.muse.client import ToolCall
from studio.receipts import BRIEF_KIND, canonical, sha256_hex
from tests.approve import approve
from tests.test_brief import sample
from tests.test_meta_mock import PNG
from tests.test_meta_mock import payload as base_payload
from tests.test_producer_content import _dispatch
from tests.test_producer_content import env as _producer_env
from tests.test_ui_studio import login, register, settings

PID = "700000000000001"
RUN = "run_m1"
LANDING = "https://clay.example/"


def _brief(goal, sub=""):
    return sample(objective={"goal": goal, "kpi": "results", "sub": sub},
                  promotion={"pixel_id": PID,
                             "privacy_policy_url": "https://clay.example/privacy"})


def _check(status="ok", pid=PID):
    r = MockAdapter().pixel_check(pid, LANDING)
    if status != "ok":
        r["checks"].append(pixel.entry("forced", status, "set by the test"))
        r["status"] = status
    return r


def _sales(check):
    spec = objectives.delivery("sales", "website", page_id=MOCK_PAGE,
                               promotion={"pixel_id": PID})
    p = base_payload(delivery=spec, campaign={**base_payload()["campaign"],
                                              "objective": spec["objective"]})
    if check is not None:
        p["pixel_check"] = pixel.signed(check)
    return p


# -- producer -------------------------------------------------------------------------


@pytest.fixture
def producer_env(tmp_path, monkeypatch):
    return _producer_env.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize(("goal", "sub", "event"), [("sales", "website", "Purchase"),
                                                    ("leads", "website", "Lead"),
                                                    ("sales", "catalog", "Purchase")])
def test_a_pixel_objective_is_checked_once_with_a_read_receipt(producer_env, goal, sub,
                                                               event):
    ctx, run, _ = producer_env
    mock = MockAdapter()
    ctx.pixel_check = mock.pixel_check
    brief = _brief(goal, sub)
    if sub == "catalog":
        brief = brief.model_copy(update={"promotion": brief.promotion.model_copy(update={
            "product_catalog_id": "800000000000001", "product_set_id": "900000000000001"})})
    result = producer.check_pixel(ctx, run.run_id, brief, LANDING)
    assert result["status"] == "ok" and result["pixel_id"] == PID
    assert event in result["events_7d"]["by_event"]
    assert mock.calls == [("pixel_check", PID)]
    (r,) = [x for x in ctx.client.ledger.receipts(run.run_id)
            if x.details.get("tool") == pixel.TOOL]
    assert r.kind == "tool_call" and r.details["side_effects"] == "read"
    assert r.details["pixel_status"] == "ok" and r.details["status"] == "ok"
    assert r.details["result_sha256"] == sha256_hex(canonical(result))
    assert r.details["pixel_sha256"] == sha256_hex(PID) and PID not in str(r.details)


@pytest.mark.parametrize(("goal", "sub"), [("leads", "instant_form"), ("traffic", ""),
                                           ("awareness", "")])
def test_objectives_without_a_pixel_skip_the_check(producer_env, goal, sub):
    ctx, run, _ = producer_env
    mock = MockAdapter()
    ctx.pixel_check = mock.pixel_check
    assert producer.check_pixel(ctx, run.run_id, _brief(goal, sub), LANDING) is None
    assert mock.calls == []
    assert not [x for x in ctx.client.ledger.receipts(run.run_id)
                if x.details.get("tool") == pixel.TOOL]


def test_no_checker_or_a_crashing_one_is_a_failed_check(producer_env):
    ctx, run, _ = producer_env
    result = producer.check_pixel(ctx, run.run_id, _brief("sales", "website"), LANDING)
    assert result["status"] == "fail" and result["checks"][0]["code"] == "pixel_unverified"

    def boom(*a, **kw):
        raise RuntimeError("adapter bug")
    ctx.pixel_check = boom
    result = producer.check_pixel(ctx, run.run_id, _brief("sales", "website"), LANDING)
    assert result["status"] == "fail"


def test_the_check_is_signed_in_the_payload_and_kept_in_the_run_state(producer_env):
    ctx, run, _ = producer_env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    brief = _brief("sales", "website")
    run.pixel_check = _check("warn")
    p = producer.campaign_payload(run, 0, LANDING, brief=brief, pixel_check=run.pixel_check)
    assert p["pixel_check"] == pixel.signed(run.pixel_check) and pixel.gate(p) == ""
    assert p["pixel_check"]["verdict"] == "warn" and "status" not in p["pixel_check"]
    assert run.state()["pixel_check"]["status"] == "warn"
    assert "pixel_check" not in producer.campaign_payload(run, 0)


# -- execution gate -------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    (tmp_path / "assets" / RUN).mkdir(parents=True)
    (tmp_path / "assets" / RUN / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, MockAdapter(), tmp_path


@pytest.mark.parametrize(("check", "code"), [
    (_check("fail"), "pixel_check_failed"),
    (None, "pixel_check_missing"),
    (_check("ok", pid="700000000000002"), "pixel_check_mismatch")])
def test_a_failed_missing_or_foreign_check_creates_nothing(env, check, code):
    conn, ledger, mock, data = env
    p = _sales(check)
    v = approve(conn, ledger, "campaign.create", "cmp_px" + code[12:16], p, RUN)
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.create(conn, ledger, mock, v, p, data)
    assert exc.value.code == code
    assert mock.calls == [] and mock.objects == {}
    row = conn.execute("SELECT status, result FROM executions WHERE approval_id = ?",
                       (v.pending_id,)).fetchone()
    assert row[0] == "failed" and code in row[1]
    assert not [r for r in ledger.receipts(RUN) if r.kind == "external_write"]


@pytest.mark.parametrize("status", ["ok", "warn"])
def test_an_ok_or_warned_check_creates_the_paused_campaign(env, status):
    conn, ledger, mock, data = env
    p = _sales(_check(status))
    v = approve(conn, ledger, "campaign.create", "cmp_px" + status, p, RUN)
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    assert out["status"] == "PAUSED"
    assert "create_campaign_paused" in [c[0] for c in mock.calls]


def test_an_instant_form_campaign_needs_no_check(env):
    conn, ledger, mock, data = env
    spec = objectives.delivery("leads", "instant_form", page_id=MOCK_PAGE, promotion={
        "privacy_policy_url": "https://clay.example/privacy"})
    p = base_payload(delivery=spec, campaign={**base_payload()["campaign"],
                                              "objective": spec["objective"]},
                     lead_form=leadads.form_spec("form", spec["lead_fields"],
                                                 "https://clay.example/privacy", LANDING))
    v = approve(conn, ledger, "campaign.create", "cmp_pxform", p, RUN)
    assert media_buyer.create(conn, ledger, mock, v, p, data)["status"] == "PAUSED"


# -- signing gate ---------------------------------------------------------------------


def _pending(app, p, item):
    conn, ledger = app.state.conn, app.state.ledger
    ledger.append(RUN, agent="producer", kind="model_call")
    pending = Pending.create("campaign.create", item, p, ledger=ledger, run_id=RUN)
    with db.transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (pending.id, RUN, canonical(p).decode(),
                                             time.time()))
    return pending.id


def _sign(c, key, approver, approval_id):
    data = c.get(f"/api/approvals/{approval_id}/payload",
                 params={"approver": approver}).json()
    sig = key.sign(base64.b64decode(data["message_b64"])).hex()
    return data, c.post(f"/api/approvals/{approval_id}",
                        json={"approver": approver, "signature": sig})


def test_a_failed_check_cannot_be_signed_and_nothing_is_consumed(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        key, approver = register(c)
        aid = _pending(app, _sales(_check("fail")), "cmp_pxsign")
        data, r = _sign(c, key, approver, aid)
        assert data["content"]["pixel_check"]["verdict"] == "fail"  # shown before signing
        assert r.status_code == 409 and r.json() == {"error": "pixel_check_failed"}
        conn = app.state.conn
        assert conn.execute("SELECT status FROM approvals WHERE id = ?",
                            (aid,)).fetchone()[0] == "pending"
        assert conn.execute("SELECT count(*) FROM nonces").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM executions").fetchone()[0] == 0
        assert not [x for x in app.state.ledger.receipts(RUN) if x.kind == "approval"]
        assert app.state.adapter.calls == []


def test_a_warned_check_is_signed(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        key, approver = register(c)
        aid = _pending(app, _sales(_check("warn")), "cmp_pxwarn")
        _, r = _sign(c, key, approver, aid)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json().get("execution_error") != "pixel_check_failed"


def _request(ctx, run, brief, checker):
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0,
                  "binding": producer.review_binding(run, 0, [])}
    digest = brief_v1.brief_sha256(brief)
    ctx.client.ledger.append(run.run_id, agent="owner", kind=BRIEF_KIND,
                             output_sha256=digest, details={"brief_sha256": digest})
    ctx.brief_v1, ctx.brief_sha256, ctx.pixel_check = brief, digest, checker
    d = Dispatcher(producer._handlers(ctx, run, "brief"), ledger=ctx.client.ledger,
                   run_id=run.run_id)
    return d.execute_or_refuse(ToolCall("c9", "request_approval", '{"summary": "ok"}'))


def test_a_run_puts_a_failed_check_on_the_approval_screen_but_it_stays_unsignable(
        producer_env):
    ctx, run, _ = producer_env

    def failing(pid, link, *, event_type=""):
        return pixel.result(pid, [pixel.entry("pixel_not_found", "fail", "no such pixel")],
                            landing_url=link)
    out = _request(ctx, run, _brief("sales", "website"), failing)
    assert run.pending_id, out
    assert run.pixel_check["status"] == "fail"
    row = ctx.conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                           (run.pending_id,)).fetchone()
    signed = json.loads(row[0])
    assert signed["pixel_check"]["verdict"] == "fail"
    assert pixel.gate(signed) == "pixel_check_failed"
    state = json.loads(ctx.conn.execute("SELECT state FROM run_state WHERE run_id = ?",
                                        (run.run_id,)).fetchone()[0])
    assert state["pixel_check"]["status"] == "fail"


def test_a_lead_form_run_has_no_pixel_check(producer_env):
    ctx, run, _ = producer_env
    mock = MockAdapter()
    _request(ctx, run, _brief("leads", "instant_form"), mock.pixel_check)
    assert run.pending_id and run.pixel_check is None and mock.calls == []
    row = ctx.conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                           (run.pending_id,)).fetchone()
    assert '"pixel_check"' not in row[0]
