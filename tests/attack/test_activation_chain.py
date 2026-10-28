"""Attack suite (day 3 fix round): what the media buyer leaves behind when Meta answers
badly, and how a signed activation reaches every object (ad → ad set → campaign)."""

import pytest

from studio import db
from studio.agents import media_buyer
from studio.approvals import Keys
from studio.executor import ExecutionRefused
from studio.meta.adapter import AdapterError
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve
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


class NoPreview(MockAdapter):
    def preview(self, ad_id):
        raise AdapterError("Graph API returned no preview", code="bad_response")


def _execution(conn, approval_id):
    return conn.execute("SELECT status FROM executions WHERE approval_id = ?",
                        (approval_id,)).fetchone()[0]


def _verify(conn, ledger, tmp_path):
    export = tmp_path / "receipts.jsonl"
    export.write_text(ledger.export_jsonl())
    return verify(export, ledger.public_key, approver_keys=Keys.registry(conn))


def test_failed_preview_after_five_objects_is_not_fatal(env):
    conn, ledger, data = env
    mock = NoPreview()
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_np1", p, RUN)
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    assert out["status"] == "PAUSED" and out["preview"] is None
    assert out["preview_error"] == "bad_response"
    (w,) = [r for r in ledger.receipts(RUN) if r.kind == "external_write"]
    assert w.details["approval_id"] == v.pending_id and w.details["status"] == "ok"
    assert media_buyer.campaign(conn, "cmp_np1")["status"] == "PAUSED"
    assert _execution(conn, v.pending_id) == "done"
    assert _verify(conn, ledger, data).ok


def test_write_receipt_and_campaign_row_exist_before_the_preview(env):
    conn, ledger, data = env
    seen = {}

    class Watch(MockAdapter):
        def preview(self, ad_id):
            seen["writes"] = [r for r in ledger.receipts(RUN) if r.kind == "external_write"]
            seen["row"] = media_buyer.campaign(conn, "cmp_np2")
            return super().preview(ad_id)

    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_np2", p, RUN)
    media_buyer.create(conn, ledger, Watch(), v, p, data)
    assert len(seen["writes"]) == 1 and seen["row"]["status"] == "PAUSED"


def _created(conn, ledger, data, mock, item):
    p = payload()
    v = approve(conn, ledger, "campaign.create", item, p, RUN)
    return media_buyer.create(conn, ledger, mock, v, p, data)


def test_activation_names_all_three_objects_and_goes_ad_adset_campaign(env):
    conn, ledger, data = env
    mock = MockAdapter()
    out = _created(conn, ledger, data, mock, "cmp_ch1")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_ch1", ceiling_pln=50)
    assert (act["ad_external_id"], act["adset_external_id"], act["external_id"]) == (
        out["ad_id"], out["adset_id"], out["campaign_id"])
    va = approve(conn, ledger, "campaign.activate", "cmp_ch1", act, RUN)
    media_buyer.activate(conn, ledger, mock, va, act, 50)
    assert [c for c in mock.calls if c[0].startswith("activate")] == [
        ("activate_ad", out["ad_id"]), ("activate_adset", out["adset_id"]),
        ("activate_campaign", out["campaign_id"])]
    for key in ("ad_id", "adset_id", "campaign_id"):
        assert mock.objects[out[key]]["status"] == "ACTIVE"
    steps = [r for r in ledger.receipts(RUN) if r.kind == "tool_call"
             and r.details["tool"].startswith("activate_")]
    assert [s.details["tool"] for s in steps] == [
        "activate_ad", "activate_adset", "activate_campaign"]
    assert {s.details["approval_id"] for s in steps} == {va.pending_id}
    assert all(s.details["status"] == "ok" for s in steps)
    report = _verify(conn, ledger, data)
    assert report.ok, report.errors


def test_activation_ids_are_bound_to_the_signature(env):
    conn, ledger, data = env
    mock = MockAdapter()
    _created(conn, ledger, data, mock, "cmp_ch2")
    other = _created(conn, ledger, data, mock, "cmp_ch3")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_ch2", ceiling_pln=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_ch2", act, RUN)
    swapped = {**act, "ad_external_id": other["ad_id"]}  # someone else's ad
    with pytest.raises(ExecutionRefused):  # content mismatch at the gate
        media_buyer.activate(conn, ledger, mock, va, swapped, 50)
    # a signed payload whose ids differ from the campaign row is refused too
    _, act2 = media_buyer.request_activation(conn, ledger, "cmp_ch2", ceiling_pln=50)
    forged = {**act2, "adset_external_id": other["adset_id"]}
    vf = approve(conn, ledger, "campaign.activate", "cmp_ch2", forged, RUN)
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.activate(conn, ledger, mock, vf, forged, 50)
    assert exc.value.code == "campaign_changed"
    assert not [c for c in mock.calls if c[0].startswith("activate")]


def test_failure_mid_chain_leaves_the_campaign_paused_and_says_so(env):
    conn, ledger, data = env

    class AdsetDown(MockAdapter):
        def activate(self, level, payload, approval):
            if level == "adset":
                raise AdapterError("Graph API error 2", code="graph_error")
            super().activate(level, payload, approval)

    mock = AdsetDown()
    out = _created(conn, ledger, data, mock, "cmp_ch4")
    _, act = media_buyer.request_activation(conn, ledger, "cmp_ch4", ceiling_pln=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_ch4", act, RUN)
    with pytest.raises(AdapterError):
        media_buyer.activate(conn, ledger, mock, va, act, 50)
    assert mock.objects[out["campaign_id"]]["status"] == "PAUSED"
    assert media_buyer.campaign(conn, "cmp_ch4")["status"] == "PAUSED"
    assert _execution(conn, va.pending_id) == "failed"
    writes = [r for r in ledger.receipts(RUN) if r.kind == "external_write"
              and r.details["action"] == "campaign.activate"]
    assert [(w.details["status"], w.details["activated"]) for w in writes] == [
        ("partial", ["ad"])]
    assert _verify(conn, ledger, data).ok


def test_a_campaign_row_without_adset_or_ad_cannot_request_activation(env):
    conn, ledger, data = env
    conn.execute("INSERT INTO campaigns (id, run_id, name, objective, status, "
                 "daily_budget_minor, adapter, external_id, created_ts) VALUES "
                 "('cmp_old', ?, 'x', 'OUTCOME_TRAFFIC', 'PAUSED', 3000, 'mock', 'mock-cmp-1', 0)",
                 (RUN,))
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.request_activation(conn, ledger, "cmp_old", ceiling_pln=50)
    assert exc.value.code == "incomplete"


def test_a_bound_adapter_writes_only_inside_a_claimed_running_execution(env):
    from studio.meta.adapter import NotApproved
    conn, ledger, data = env
    mock = MockAdapter(conn=conn)
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_run1", p, RUN)
    with pytest.raises(NotApproved):  # signed, but no executor claimed it
        mock.create_campaign_paused(p, v)
    assert mock.calls == []
    out = media_buyer.create(conn, ledger, mock, v, p, data)  # authorize → running → done
    assert out["status"] == "PAUSED"
    with pytest.raises(NotApproved):  # finished: the approval cannot drive more writes
        mock.create_campaign_paused(p, v)
    _, act = media_buyer.request_activation(conn, ledger, "cmp_run1", ceiling_pln=50)
    va = approve(conn, ledger, "campaign.activate", "cmp_run1", act, RUN)
    assert media_buyer.activate(conn, ledger, mock, va, act, 50)["status"] == "ACTIVE"


def test_the_app_binds_its_adapter_to_the_database(tmp_path):
    from studio.app import create_app
    from tests.test_ui_studio import settings
    a = create_app(settings(tmp_path))
    assert a.state.adapter._conn is a.state.conn
