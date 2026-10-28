"""Attack suite (D3-7): the media buyer creates PAUSED objects only, and only for the
exact item a human signed, once. Every refusal happens before any adapter call."""

import hashlib

import pytest

from studio import db
from studio.agents import community, media_buyer
from studio.approvals import Keys, Verified
from studio.executor import ExecutionRefused
from studio.meta import objectives
from studio.meta.adapter import MOCK_PAGE, PausedOnly
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve

PNG = b"\x89PNG\r\n\x1a\n" + b"hero-image" * 20
RUN = "run_buy1"

TRAFFIC = objectives.delivery("traffic", page_id=MOCK_PAGE)


def payload(**over):
    p = {"action": "campaign.create", "run_id": RUN,
         "delivery": TRAFFIC,
         "campaign": {"objective": "OUTCOME_TRAFFIC", "status": "PAUSED",
                      "daily_budget_minor": 3000, "currency": "PLN", "angle": "Slow mornings"},
         "audience": "home office", "link": "https://example.com/",
         "targeting": {"countries": ["PL"], "age_min": 25, "age_max": 55},
         "ad": {"primary_text": "A mug for slow mornings.", "headline": "Slow mornings",
                "description": "89 PLN", "cta": "SHOP_NOW"},
         "images": [{"placement": "feed_1x1", "sha256": hashlib.sha256(PNG).hexdigest()}],
         "review": {"verdict": "OK", "second_opinion": "unavailable", "disagreements": []}}
    p.update(over)
    return p


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    assets = tmp_path / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")  # the run's provenance
    return conn, ledger, MockAdapter(), tmp_path


def _writes(ledger):
    return [r for r in ledger.receipts() if r.kind == "external_write"]


def test_signed_create_makes_paused_objects_and_verifies(env, tmp_path):
    conn, ledger, mock, data = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_ok1", p, RUN)
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    assert out["status"] == "PAUSED" and out["preview"]["kind"] == "inline"
    assert all(o["status"] == "PAUSED" for o in mock.objects.values() if "status" in o)
    (w,) = _writes(ledger)
    assert w.details["approval_id"] == v.pending_id and w.details["item_id"] == "cmp_ok1"
    assert w.details["action"] == "campaign.create" and w.output_sha256 == v.content_sha256
    assert w.details["adapter"] == "mock" and w.provider == "meta-mock"
    steps = [r.details["tool"] for r in ledger.receipts(RUN) if r.agent == "media_buyer"
             and r.kind == "tool_call"]
    assert steps == ["image", "campaign", "adset", "creative", "ad"]
    assert media_buyer.campaign(conn, "cmp_ok1")["status"] == "PAUSED"
    export = tmp_path / "receipts.jsonl"
    export.write_text(ledger.export_jsonl())
    report = verify(export, ledger.public_key, approver_keys=Keys.registry(conn))
    assert report.ok, report.errors
    assert report.signatures == 1


def test_active_at_creation_raises_and_calls_nothing(env):
    conn, ledger, mock, data = env
    bad = payload(campaign={**payload()["campaign"], "status": "ACTIVE"})
    v = approve(conn, ledger, "campaign.create", "cmp_bad1", bad, RUN)  # even if signed
    with pytest.raises(PausedOnly):
        media_buyer.create(conn, ledger, mock, v, bad, data)
    nested = payload(ad={**payload()["ad"], "status": "ACTIVE"})
    v2 = approve(conn, ledger, "campaign.create", "cmp_bad2", nested, RUN)
    with pytest.raises(PausedOnly):
        media_buyer.create(conn, ledger, mock, v2, nested, data)
    assert mock.calls == [] and _writes(ledger) == []


def test_adapter_creation_has_no_status_parameter(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_bad3", p, RUN)
    with pytest.raises(TypeError):
        mock.create_campaign_paused(p, v, status="ACTIVE")
    with pytest.raises(TypeError):
        mock.create_ad_paused("a", "c", p, v, status="ACTIVE")


def test_write_without_approval_is_refused(env):
    conn, ledger, mock, data = env
    p = payload()
    with pytest.raises(ExecutionRefused) as exc:
        media_buyer.create(conn, ledger, mock, None, p, data)
    assert exc.value.code == "not_approved"
    from studio.approvals import payload_sha256
    forged = Verified(pending_id="0" * 32, run_id=RUN, action="campaign.create",
                      item_id="cmp_x", content_sha256=payload_sha256(p),
                      approver="ed25519:" + "1" * 32, signature="ab" * 64)
    with pytest.raises(ExecutionRefused) as exc:  # a Verified the database never approved
        media_buyer.create(conn, ledger, mock, forged, p, data)
    assert exc.value.code == "not_approved"
    assert mock.calls == [] and _writes(ledger) == []


def test_approval_for_a_different_item_is_refused(env):
    conn, ledger, mock, data = env
    p_a = payload()
    v_a = approve(conn, ledger, "campaign.create", "cmp_a", p_a, RUN)
    p_b = payload(campaign={**p_a["campaign"], "daily_budget_minor": 50000})
    with pytest.raises(ExecutionRefused) as exc:
        media_buyer.create(conn, ledger, mock, v_a, p_b, data)
    assert exc.value.code == "content_mismatch"
    relabelled = Verified(**{**v_a.__dict__, "item_id": "cmp_b"})  # same hash, other item
    with pytest.raises(ExecutionRefused):
        media_buyer.create(conn, ledger, mock, relabelled, p_a, data)
    # an inbox approval for comment c-1 cannot post under comment c-3
    db.seed(conn)
    _, r1 = community.request(conn, ledger, "c-1", "reply.post", "89 PLN.")
    v1 = approve(conn, ledger, "reply.post", "c-1", r1, community.run_id_for("c-1"))
    r3 = community.reply_payload({"id": "c-3", "external_id": ""}, "89 PLN.")
    with pytest.raises(ExecutionRefused):
        community.send(conn, ledger, mock, v1, r3)
    with pytest.raises(ExecutionRefused):  # nor create a campaign
        media_buyer.create(conn, ledger, mock, v1, r1, data)
    assert mock.calls == [] and _writes(ledger) == []


def test_replay_is_refused(env):
    conn, ledger, mock, data = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_r1", p, RUN)
    media_buyer.create(conn, ledger, mock, v, p, data)
    calls = len(mock.calls)
    with pytest.raises(ExecutionRefused) as exc:
        media_buyer.create(conn, ledger, mock, v, p, data)
    assert exc.value.code == "replay" and len(mock.calls) == calls
    assert len(_writes(ledger)) == 1


def test_expired_approval_is_refused(env):
    conn, ledger, mock, data = env
    import time
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_e1", p, RUN, now=time.time() - 100)
    from studio import executor
    with pytest.raises(ExecutionRefused) as exc:
        executor.authorize(conn, v, "campaign.create", p, now=time.time() + 1000)
    assert exc.value.code == "expired" and mock.calls == []


def test_image_swapped_on_disk_is_refused(env):
    conn, ledger, mock, data = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_i1", p, RUN)
    (data / "assets" / RUN / "feed_1x1.png").write_bytes(b"\x89PNGswapped")
    with pytest.raises(media_buyer.BuyError):
        media_buyer.create(conn, ledger, mock, v, p, data)
    assert mock.calls == [] and _writes(ledger) == []


def test_activation_needs_its_own_approval_and_respects_the_ceiling(env):
    conn, ledger, mock, data = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_act", p, RUN)
    media_buyer.create(conn, ledger, mock, v, p, data)
    pending, act = media_buyer.request_activation(conn, ledger, "cmp_act", ceiling=50)
    with pytest.raises(ExecutionRefused):  # the creation approval cannot activate
        media_buyer.activate(conn, ledger, mock, v, act, 50)
    va = approve(conn, ledger, "campaign.activate", "cmp_act", act, RUN)
    with pytest.raises(media_buyer.BuyError) as exc:  # ceiling lowered after signing
        media_buyer.activate(conn, ledger, mock, va, act, ceiling=20)
    assert exc.value.code == "over_ceiling"
    assert media_buyer.campaign(conn, "cmp_act")["status"] == "PAUSED"
    _, act2 = media_buyer.request_activation(conn, ledger, "cmp_act", ceiling=50)
    va2 = approve(conn, ledger, "campaign.activate", "cmp_act", act2, RUN)
    out = media_buyer.activate(conn, ledger, mock, va2, act2, 50)
    assert out["status"] == "ACTIVE" and media_buyer.campaign(conn, "cmp_act")[
        "status"] == "ACTIVE"
    with pytest.raises(media_buyer.BuyError):  # not PAUSED any more
        media_buyer.request_activation(conn, ledger, "cmp_act", 50)


def test_over_ceiling_campaign_cannot_even_request_activation(env):
    conn, ledger, mock, data = env
    p = payload(campaign={**payload()["campaign"], "daily_budget_minor": 9000})
    v = approve(conn, ledger, "campaign.create", "cmp_big", p, RUN)
    media_buyer.create(conn, ledger, mock, v, p, data)
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.request_activation(conn, ledger, "cmp_big", ceiling=50)
    assert exc.value.code == "over_ceiling"
