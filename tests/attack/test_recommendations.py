"""Measurement: objective-aware snapshots as receipts, code-made recommendations, and
every executed recommendation behind a new signed ``campaign.update`` approval through the
executor (never automatic); verify rejects an update the server logged without one."""

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import db
from studio.agents import media_buyer
from studio.app import create_app
from studio.approvals import Keys
from studio.executor import ExecutionRefused
from studio.meta import measurement, objectives, pixel
from studio.meta.adapter import MOCK_PAGE, NotApproved, PausedOnly
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve
from tests.attack.test_paused_only import PNG, RUN, payload
from tests.contract.test_graph import adapter as graph
from tests.test_ui_studio import login, settings


def test_result_is_counted_for_the_objective():
    row = {"results": [{"indicator": "x", "values": [{"value": "7"}]}],
           "actions": [{"action_type": "link_click", "value": "3"}], "reach": "90",
           "spend": "14"}
    assert measurement.result_of(row, "link_click") == 7  # Meta's own results first
    row.pop("results")
    assert measurement.result_of(row, "link_click") == 3
    assert measurement.result_of(row, "reach") == 90
    assert measurement.normalize(row, "link_click")["cost_per_result"] == 4.67
    assert measurement.normalize({}, "lead")["cost_per_result"] is None


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    (tmp_path / "assets" / RUN).mkdir(parents=True)
    (tmp_path / "assets" / RUN / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    mock = MockAdapter()
    p = payload(delivery=objectives.delivery("sales", page_id=MOCK_PAGE,
                                             promotion={"pixel_id": "700000000000001"}),
                pixel_check=pixel.signed(MockAdapter().pixel_check("700000000000001",
                                                          "https://example.com/")))
    v = approve(conn, ledger, "campaign.create", "cmp_ms1", p, RUN)
    media_buyer.create(conn, ledger, mock, v, p, tmp_path)
    return conn, ledger, mock, tmp_path


def test_pull_snapshots_every_campaign_by_its_objective(env, tmp_path):
    conn, ledger, mock, _ = env
    out = measurement.pull_all(conn, ledger, mock, 50)
    assert out["campaigns"] == 1 and out["recommendations"] >= 1
    (snap,) = measurement.latest(conn)
    assert snap["objective"] == "OUTCOME_SALES"
    assert snap["result"] == "offsite_conversion.fb_pixel_purchase"
    assert snap["totals"]["results"] == 120 and snap["attribution_setting"] == "7d_click_1d_view"
    assert {k: len(v) for k, v in snap["breakdowns"].items()} == {
        "age_gender": 4, "placement": 4, "region": 3}
    codes = {r["code"] for r in snap["recommendations"]}
    assert codes == {"placement_costly", "scale"}
    (r,) = [x for x in ledger.receipts() if x.kind == "insights.snapshot"]
    assert r.details["objective"] == "OUTCOME_SALES" and r.details["provenance"] == "synthetic"
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    assert verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok


def test_rules():
    snap = {"totals": {"results": 0, "frequency": 1.2, "spend": 70.0,
                       "cost_per_result": None}, "breakdowns": {}}
    (pause,) = measurement.recommend(snap, daily_budget_minor=3000, ceiling_minor=5000)
    assert pause["code"] == "no_results" and pause["change"] == {"kind": "status",
                                                                 "status": "PAUSED"}
    snap["totals"].update(results=10, frequency=4.5, cost_per_result=7.0)
    (cut,) = measurement.recommend(snap, daily_budget_minor=3000, ceiling_minor=5000,
                                   currency="GBP")
    assert cut["change"]["to_minor"] == 2400 and cut["change"]["currency"] == "GBP"
    snap["totals"]["frequency"] = 1.5
    assert measurement.recommend(snap, daily_budget_minor=5000, ceiling_minor=5000) == []


def _rec(conn, code):
    (snap,) = measurement.latest(conn)
    return next(r for r in snap["recommendations"] if r["code"] == code)


def test_a_recommendation_runs_only_on_a_new_signed_approval(env, tmp_path):
    conn, ledger, mock, _ = env
    measurement.pull_all(conn, ledger, mock, 50)
    info = _rec(conn, "placement_costly")
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.request_update(conn, ledger, info["id"], 50)
    assert exc.value.code == "not_executable"  # information only
    scale = _rec(conn, "scale")
    pending, p = media_buyer.request_update(conn, ledger, scale["id"], 50)
    assert p["change"]["to_minor"] == 3600 and p["ceiling_minor"] == 5000
    before = [c for c in mock.calls if c[0] == "update_budget"]
    with pytest.raises(ExecutionRefused):  # proposed is not approved
        media_buyer.update(conn, ledger, mock, None, p, 50)
    assert [c for c in mock.calls if c[0] == "update_budget"] == before == []
    v = approve(conn, ledger, "campaign.update", "cmp_ms1", p, RUN)
    out = media_buyer.update(conn, ledger, mock, v, p, 50)
    assert out["change"]["kind"] == "budget"
    adset = next(o for o in mock.objects.values() if o.get("type") == "adset")
    assert adset["daily_budget"] == 3600
    assert media_buyer.campaign(conn, "cmp_ms1")["daily_budget_minor"] == 3600
    assert measurement.recommendation(conn, scale["id"])["status"] == "executed"
    with pytest.raises(ExecutionRefused):  # one signature, one change
        media_buyer.update(conn, ledger, mock, v, p, 50)
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    assert verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok


def test_a_budget_over_the_ceiling_and_an_activation_are_refused(env):
    conn, ledger, mock, _ = env
    measurement.pull_all(conn, ledger, mock, 50)
    pending, p = media_buyer.request_update(conn, ledger, _rec(conn, "scale")["id"], 50)
    v = approve(conn, ledger, "campaign.update", "cmp_ms1", p, RUN)
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.update(conn, ledger, mock, v, p, 30)  # the ceiling was lowered since
    assert exc.value.code == "over_ceiling"
    active = {**p, "change": {"kind": "status", "status": "ACTIVE"}}
    va = approve(conn, ledger, "campaign.update", "cmp_ms1", active, RUN)
    with pytest.raises(PausedOnly):
        mock.pause(active, va)


def test_verify_rejects_an_update_logged_without_approval(env, tmp_path):
    conn, ledger, mock, _ = env
    ledger.append(RUN, agent="media_buyer", kind="tool_call", provider="meta-mock",
                  details={"tool": "update_budget", "status": "ok", "side_effects": "external",
                           "approval_id": "a" * 32})
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    report = verify(export, ledger.public_key, approver_keys=Keys.registry(conn))
    assert not report.ok and any("unauthorised external step" in e for e in report.errors)


def test_graph_insights_and_update_requests(tmp_path):
    sent = []

    def handler(request):
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"success": True})

    g = graph(handler)
    g.insights_report("120200000000000101", ["results", "spend"], breakdowns="region")
    q = dict(sent[0].url.params)
    assert sent[0].url.path == "/v25.0/120200000000000101/insights"
    assert q["breakdowns"] == "region" and q["use_unified_attribution_setting"] == "true"
    assert q["fields"] == "results,spend" and q["date_preset"] == "last_7d"
    with pytest.raises(NotApproved):
        g.update_budget({"action": "campaign.update"}, None)
    assert len(sent) == 1


def test_report_page_shows_the_measurement_in_both_languages(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        assert c.post("/api/insights/pull", json={}).json() == {"campaigns": 0,
                                                                "recommendations": 0}
        assert "Measurement by objective" in c.get("/report?lang=en").text
        assert "Pomiar według celu" in c.get("/report?lang=pl").text
        assert c.post("/api/recommendations/rc_000000000000/approve",
                      json={}).status_code == 404
