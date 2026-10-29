"""Every objective is created PAUSED from the signed delivery block only; a delivery that
is not a row of the objective table, or not the one signed, never reaches Meta; ``studio
verify`` rejects a log whose campaign was created with another objective mapping."""

import copy

import pytest

from studio import db
from studio.agents import media_buyer
from studio.approvals import Keys
from studio.meta import objectives, pixel
from studio.meta.adapter import MOCK_PAGE, NotApproved, spec_sha256
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve
from tests.attack.test_paused_only import PNG, RUN, payload
from tests.test_objectives import IDS

# rows a mock end-to-end run covers here (instant forms: tests/attack/test_lead_ads.py)
ROWS = [k for k in objectives.TABLE if k != ("leads", "instant_form")]


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    assets = tmp_path / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, MockAdapter(), tmp_path


def _spec(goal, sub):
    return objectives.delivery(goal, sub, page_id=MOCK_PAGE, promotion=IDS)


def _with_pixel_check(p, spec):
    """Website leads and sales: a passing pixel check is signed with the payload."""
    if pixel.pixel_of(spec):
        p["pixel_check"] = pixel.signed(MockAdapter().pixel_check(pixel.pixel_of(spec),
                                                                  p["link"]))


@pytest.mark.parametrize("key", ROWS, ids=lambda k: ":".join(k))
def test_every_objective_is_created_paused_from_the_signed_delivery(env, key):
    conn, ledger, mock, data = env
    spec = _spec(*key)
    p = payload(delivery=spec, campaign={**payload()["campaign"],
                                         "objective": spec["objective"]})
    _with_pixel_check(p, spec)
    v = approve(conn, ledger, "campaign.create", "cmp_" + key[1][:6] + "1", p, RUN)
    assert v.spec_sha256 == spec_sha256(spec)
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    assert out["status"] == "PAUSED"
    by_type = {o["type"]: o for o in mock.objects.values() if "type" in o}
    assert by_type["campaign"]["objective"] == spec["objective"]
    adset = by_type["adset"]
    assert adset["optimization_goal"] == spec["optimization_goal"]
    assert adset.get("destination_type", "") == spec["destination_type"]
    assert adset.get("promoted_object", {}) == spec["promoted_object"]
    assert ("attribution_spec" in adset) == ("attribution_spec" in spec)
    body = by_type["creative"]["body"]
    if spec["creative"] == "catalog":
        assert body["product_set_id"] == IDS["product_set_id"]
        assert by_type["campaign"]["promoted_object"] == {
            "product_catalog_id": IDS["product_catalog_id"]}
    link = body["object_story_spec"].get("link_data") or {}
    if spec["creative"] == "message":
        assert link["call_to_action"]["value"]["app_destination"] in (
            "MESSENGER", "INSTAGRAM_DIRECT", "WHATSAPP")
    if spec["creative"] == "app":
        assert link["link"] == IDS["object_store_url"]
        assert link["call_to_action"] == {"type": "INSTALL_MOBILE_APP", "value": {
            "application": IDS["application_id"], "link": IDS["object_store_url"]}}
    if spec["creative"] == "event":
        assert link["link"].endswith(f"/events/{IDS['event_id']}/")
    steps = {r.details["tool"]: r.details for r in ledger.receipts(RUN)
             if r.kind == "tool_call" and r.agent == "media_buyer"}
    assert steps["campaign"]["spec_sha256"] == v.spec_sha256
    assert steps["adset"]["objective"] == spec["objective"]
    export = data / "receipts.jsonl"
    export.write_text(ledger.export_jsonl())
    assert verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok


@pytest.mark.parametrize("edit", [
    lambda s: s.update(optimization_goal="LINK_CLICKS"),
    lambda s: s.update(objective="OUTCOME_TRAFFIC"),
    lambda s: s["promoted_object"].pop("pixel_id"),
    lambda s: s.update(destination_type=""),
], ids=["goal", "objective", "promoted", "destination"])
def test_a_signed_but_invalid_mapping_calls_nothing(env, edit):
    conn, ledger, mock, data = env
    spec = _spec("sales", "website")
    edit(spec)
    p = payload(delivery=spec)
    _with_pixel_check(p, spec)  # the mapping alone is what is refused
    v = approve(conn, ledger, "campaign.create", "cmp_bad_spec", p, RUN)  # even if signed
    with pytest.raises(NotApproved):
        media_buyer.create(conn, ledger, mock, v, p, data)
    assert [c for c in mock.calls if c[0] != "upload_image"] == []


def test_a_payload_without_delivery_calls_nothing(env):
    conn, ledger, mock, data = env
    p = payload()
    del p["delivery"]
    v = approve(conn, ledger, "campaign.create", "cmp_no_spec", p, RUN)
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.create(conn, ledger, mock, v, p, data)
    assert exc.value.code == "spec_changed" and mock.calls == []


def test_another_page_is_refused(env):
    conn, ledger, _, data = env
    spec = objectives.delivery("awareness", page_id="600000000000999")
    p = payload(delivery=spec)
    v = approve(conn, ledger, "campaign.create", "cmp_page", p, RUN)
    mock = MockAdapter()
    with pytest.raises(NotApproved):
        media_buyer.create(conn, ledger, mock, v, p, data)


def test_verify_rejects_a_step_that_sent_another_mapping(env, tmp_path):
    """The server itself (real ledger key) logs a campaign created with another objective
    than the signed one: the step's spec hash is not the signed spec_sha256."""
    conn, ledger, mock, data = env
    spec = _spec("sales", "website")
    p = payload(delivery=spec)
    v = approve(conn, ledger, "campaign.create", "cmp_v1", p, RUN)
    other = copy.deepcopy(spec)
    other["objective"] = "OUTCOME_TRAFFIC"
    ledger.append(RUN, agent="media_buyer", kind="tool_call", provider="meta-mock",
                  details={"tool": "campaign", "status": "ok", "side_effects": "external",
                           "approval_id": v.pending_id, "spec_sha256": spec_sha256(other),
                           "objective": "OUTCOME_TRAFFIC"})
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    report = verify(export, ledger.public_key, approver_keys=Keys.registry(conn))
    assert not report.ok and any("spec_sha256" in e for e in report.errors)


def test_verify_rejects_an_adset_step_that_names_no_mapping(env, tmp_path):
    conn, ledger, mock, data = env
    p = payload(delivery=_spec("traffic", ""))
    v = approve(conn, ledger, "campaign.create", "cmp_v2", p, RUN)
    ledger.append(RUN, agent="media_buyer", kind="tool_call", provider="meta-mock",
                  details={"tool": "adset", "status": "ok", "side_effects": "external",
                           "approval_id": v.pending_id})
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    assert not verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok


def test_the_signed_message_carries_the_spec_hash(env):
    conn, ledger, mock, data = env
    p = payload(delivery=_spec("awareness", "ad_recall_lift"))
    v = approve(conn, ledger, "campaign.create", "cmp_v3", p, RUN)
    (receipt,) = [r for r in ledger.receipts(RUN) if r.kind == "approval"]
    assert receipt.details["spec_sha256"] == v.spec_sha256 == spec_sha256(p["delivery"])
