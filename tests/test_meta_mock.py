"""Mock Meta adapter (D3-6): same contract as graph — approval + exact payload, PAUSED."""

import hashlib

import pytest

from studio import db
from studio.agents import community
from studio.approvals import Verified
from studio.meta.adapter import PAUSED, NotApproved, PausedOnly, check_paused
from studio.meta.mock import MockAdapter
from tests.approve import approve

PNG = b"\x89PNG\r\n\x1a\n" + b"demo" * 10


def payload(**over):
    p = {"action": "campaign.create", "run_id": "run_m1",
         "campaign": {"objective": "OUTCOME_TRAFFIC", "status": "PAUSED",
                      "daily_budget_minor": 3000, "currency": "PLN", "angle": "Slow mornings"},
         "audience": "home office", "link": "https://example.com/",
         "targeting": {"countries": ["PL"], "age_min": 25, "age_max": 55},
         "ad": {"primary_text": "A mug for slow mornings.", "headline": "Slow mornings",
                "description": "89 PLN", "cta": "SHOP_NOW"},
         "images": [{"placement": "feed_1x1", "sha256": hashlib.sha256(PNG).hexdigest()}],
         "review": {"verdict": "OK"}}
    p.update(over)
    return p


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    return conn, ledger


def test_full_paused_sequence(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_a1", p, "run_m1")
    m = MockAdapter()
    image = m.upload_image(PNG, p, v)
    cmp = m.create_campaign_paused(p, v)
    adset = m.create_adset_paused(cmp, p, v)
    creative = m.create_creative(image, p, v)
    ad = m.create_ad_paused(adset, creative, p, v)
    assert all(m.objects[i]["status"] == PAUSED for i in (cmp, adset, ad))
    assert m.objects[adset]["daily_budget"] == 3000
    assert m.objects[creative]["message"] == "A mug for slow mornings."
    prev = m.preview(ad)
    assert prev.kind == "inline" and prev.fields["name"] == "Slow mornings"
    assert [c[0] for c in m.calls] == ["upload_image", "create_campaign_paused",
                                       "create_adset_paused", "create_creative",
                                       "create_ad_paused", "preview"]


def test_writes_without_a_matching_approval_are_refused(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_a2", p, "run_m1")
    m = MockAdapter()
    with pytest.raises(NotApproved):
        m.create_campaign_paused(p, None)
    with pytest.raises(NotApproved):  # edited after signing
        m.create_campaign_paused(payload(audience="everyone"), v)
    forged = Verified(**{**v.__dict__, "action": "campaign.activate"})
    with pytest.raises(NotApproved):
        m.create_campaign_paused(p, forged)
    with pytest.raises(NotApproved):
        m.upload_image(b"\x89PNGother", p, v)
    with pytest.raises(NotApproved):  # a campaign approval cannot activate
        m.activate("campaign", {**p, "external_id": "x"}, v)
    assert m.calls == []


def test_active_in_a_creation_payload_is_refused(env):
    conn, ledger = env
    bad = payload(campaign={**payload()["campaign"], "status": "ACTIVE"})
    v = approve(conn, ledger, "campaign.create", "cmp_a3", bad, "run_m1")
    with pytest.raises(PausedOnly):
        MockAdapter().create_campaign_paused(bad, v)
    with pytest.raises(PausedOnly):
        check_paused({"ad": {"nested": [{"status": "ACTIVE"}]}})


def test_reply_and_hide_need_their_own_actions(env):
    conn, ledger = env
    db.seed(conn)
    pending, reply = community.request(conn, ledger, "c-3", "reply.post", "Dziękujemy!")
    m = MockAdapter()
    v = approve(conn, ledger, "reply.post", "c-3", reply, community.run_id_for("c-3"))
    hide = community.hide_payload({"id": "c-3", "external_id": ""})
    with pytest.raises(NotApproved):
        m.hide(hide, v)
    assert m.reply(reply, v).startswith("mock-reply-")
    assert m.replies == {"c-3": ["Dziękujemy!"]}
