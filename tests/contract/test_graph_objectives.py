"""Graph adapter bodies for each objective's ad set and creative, against the hand-written
v25.0 cassette (``graph_campaign.SYNTHETIC.json``): the fields come from the signed
delivery block, the Page must be the adapter's own."""

import json

import pytest

from studio import db
from studio.meta import objectives
from studio.meta.adapter import NotApproved
from tests.approve import approve
from tests.contract.test_graph import CASSETTE, Replay, adapter
from tests.test_meta_mock import PNG, payload
from tests.test_objectives import IDS

PAGE = "600000000000001"


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    return conn, db.open_ledger(conn, tmp_path / "ledger.key")


def _run(conn, ledger, spec, item):
    p = payload(delivery=spec, campaign={**payload()["campaign"],
                                         "objective": spec["objective"]})
    v = approve(conn, ledger, "campaign.create", item, p, "run_m1")
    replay = Replay(CASSETTE["interactions"])
    g = adapter(replay)
    image = g.upload_image(PNG, p, v)
    cmp = g.create_campaign_paused(p, v)
    g.create_adset_paused(cmp, p, v)
    g.create_creative(image, p, v)
    return {r.url.path.rsplit("/", 1)[-1]: f for r, f in replay.sent if r.method == "POST"}


@pytest.mark.parametrize(("goal", "sub"), [("sales", "website"), ("sales", "catalog"),
                                           ("app_promotion", ""), ("engagement", "messenger"),
                                           ("awareness", "reach")])
def test_adset_and_creative_bodies_follow_the_delivery(env, goal, sub):
    conn, ledger = env
    spec = objectives.delivery(goal, sub, page_id=PAGE, promotion=IDS)
    posts = _run(conn, ledger, spec, "cmp_go" + goal[:4])
    assert posts["campaigns"]["objective"] == spec["objective"]
    assert posts["campaigns"]["status"] == posts["adsets"]["status"] == "PAUSED"
    adset = posts["adsets"]
    assert adset["optimization_goal"] == spec["optimization_goal"]
    assert adset["billing_event"] == "IMPRESSIONS"
    assert adset.get("destination_type", "") == spec["destination_type"]
    assert json.loads(adset.get("promoted_object", "{}")) == spec["promoted_object"]
    if spec.get("attribution_spec"):
        assert json.loads(adset["attribution_spec"]) == spec["attribution_spec"]
    if goal == "sales" and sub == "catalog":
        assert json.loads(posts["campaigns"]["promoted_object"]) == {
            "product_catalog_id": IDS["product_catalog_id"]}
        assert posts["adcreatives"]["product_set_id"] == IDS["product_set_id"]
        assert "template_data" in json.loads(posts["adcreatives"]["object_story_spec"])
    story = json.loads(posts["adcreatives"]["object_story_spec"])
    assert story["page_id"] == PAGE


def test_a_delivery_for_another_page_sends_nothing(env):
    conn, ledger = env
    spec = objectives.delivery("engagement", "whatsapp", page_id="600000000000999")
    p = payload(delivery=spec)
    v = approve(conn, ledger, "campaign.create", "cmp_gp", p, "run_m1")
    replay = Replay([])
    with pytest.raises(NotApproved):
        adapter(replay).create_campaign_paused(p, v)
    assert replay.sent == []
