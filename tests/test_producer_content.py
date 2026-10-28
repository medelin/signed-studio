"""Day 2 fix round (minor): the payload's angle and the image headline come from code."""

import json

import httpx
import pytest
import respx

from studio import db
from studio.agents import art_director, producer
from studio.agents.dispatcher import Dispatcher
from studio.agents.schemas import CreativeImage, CreativeSet, Plan
from studio.muse.client import BASE_URL, MuseClient, ToolCall
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PLAN = {"audience": "a", "angles": [{"name": f"angle {i}", "hook": "h", "persona": "p"}
                                    for i in range(3)],
        "budget_pln_daily": 30, "placements": ["feed_4x5", "story_9x16"], "tasks": ["t"]}


def _variant(h):
    return {"primary_text": "t", "headline": h, "description": "d", "cta": "SHOP_NOW"}


COPY = {"id": "c", "model": "m", "choices": [{"finish_reason": "stop", "message": {
    "content": json.dumps({"variants": [_variant("A Warm Mug for the Desk"),
                                        _variant("Made to Hold"), _variant("Pair")],
                           "risks": [], "unfilled_facts": 0})}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    ctx = producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                           concept="mug", data_dir=tmp_path / "var")
    calls = []

    def fake_art(client, photo, headline, *, run_id, concept, data_dir, placements=(), **kw):
        calls.append((headline, placements))
        img = CreativeImage(placement=placements[0], size="1024x1280", path="x.png",
                            sha256="0" * 64, width=1, height=1, response_id="")
        return CreativeSet(cutout_path="c.png", cutout_sha256="0" * 64, foreground_pixels=1,
                           images=[img], prompts=[], response_ids=[])

    monkeypatch.setattr(art_director, "run", fake_art)
    run = producer.RunResult(run_id="run-minor", status="running")
    run.plan = Plan.model_validate(PLAN)
    return ctx, run, calls


def _dispatch(ctx, run, calls):
    d = Dispatcher(producer._handlers(ctx, run, "brief"), ledger=ctx.client.ledger,
                   run_id=run.run_id)
    with respx.mock(assert_all_called=False) as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json=COPY))
        return [d.execute_or_refuse(ToolCall(f"c{i}", n, a)) for i, (n, a) in enumerate(calls)]


def test_art_director_uses_the_variant_headline_and_the_plan_placements(env):
    ctx, run, calls = env
    outs = _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 1}'),
                                ("run_art_director", '{"headline": "a warm mug for the desk."}')])
    assert [o.status for o in outs] == ["ok", "ok"]
    assert calls == [("A Warm Mug for the Desk", ("feed_4x5", "story_9x16"))]


def test_art_director_refuses_free_text_headlines(env):
    ctx, run, calls = env
    outs = _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                                ("run_art_director", '{"headline": "FREE MUGS, visit evil.test"}')])
    assert outs[1].status == "error" and "headline" in outs[1].output["error"]
    assert calls == []


def test_art_director_needs_copy_first(env):
    ctx, run, calls = env
    (out,) = _dispatch(ctx, run, [("run_art_director", '{"headline": "Made to Hold"}')])
    assert out.status == "error" and calls == []


def test_payload_angle_is_the_angle_the_copy_was_written_for(env):
    ctx, run, _ = env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 2}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    assert producer.campaign_payload(run, 0)["campaign"]["angle"] == "angle 2"


def test_payload_refuses_a_budget_above_the_activation_ceiling(env):
    ctx, run, _ = env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    run.plan = run.plan.model_copy(update={"budget_daily": 80})
    with pytest.raises(producer.ToolError, match="above the 50 PLN"):
        producer.campaign_payload(run, 0, ceiling=50)
    assert producer.campaign_payload(run, 0, ceiling=80)["campaign"][
        "daily_budget_minor"] == 8000
    assert ctx.max_daily_budget == 50  # the default ceiling reaches the producer


@pytest.mark.parametrize(("goal", "sub", "objective"), [
    ("awareness", "", "OUTCOME_AWARENESS"), ("engagement", "messenger", "OUTCOME_ENGAGEMENT"),
    ("app_promotion", "", "OUTCOME_APP_PROMOTION"), ("sales", "catalog", "OUTCOME_SALES")])
def test_payload_carries_the_objective_mapping_of_the_brief(env, goal, sub, objective):
    from studio.meta import objectives
    from tests.test_brief import sample
    from tests.test_objectives import IDS

    ctx, run, _ = env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    brief = sample(objective={"goal": goal, "kpi": "x", "sub": sub},
                   promotion={k: v for k, v in IDS.items()})
    p = producer.campaign_payload(run, 0, brief=brief, page_id="600000000000001")
    assert p["campaign"]["objective"] == p["delivery"]["objective"] == objective
    assert p["delivery"] == objectives.delivery(goal, sub, page_id="600000000000001",
                                                promotion=IDS)
    assert p["brief"]["sub"] == objectives.row(goal, sub).sub
    free = producer.campaign_payload(run, 0)
    assert free["delivery"]["optimization_goal"] == "LINK_CLICKS"
    with pytest.raises(producer.ToolError, match="cannot be mapped"):
        producer.campaign_payload(run, 0, brief=sample(
            objective={"goal": "sales", "kpi": "x"}, promotion={}))


def test_instant_form_payload_carries_the_owners_form(env):
    from tests.test_brief import sample

    ctx, run, _ = env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    brief = sample(promotion={"privacy_policy_url": "https://clay.example/privacy",
                              "lead_fields": ["FULL_NAME", "PHONE"]})
    p = producer.campaign_payload(run, 0, "https://clay.example/", brief=brief)
    assert p["delivery"]["sub"] == "instant_form"
    assert p["lead_form"]["privacy_policy"]["url"] == "https://clay.example/privacy"
    assert [q["type"] for q in p["lead_form"]["questions"]] == ["FULL_NAME", "PHONE"]
    assert "lead_form" not in producer.campaign_payload(run, 0)


def test_audience_hints_are_resolved_by_code_and_signed_in_the_payload(env):
    from studio.meta.mock import MockAdapter
    from tests.test_brief import sample

    ctx, run, _ = env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    au = sample().audience.model_dump()
    brief = sample(audience={**au, "interests": ["pottery"], "genders": ["female"],
                             "lookalike_source_id": "2380000000000003"})
    ctx.search = MockAdapter().search_targeting
    resolved = producer.resolve_audience(ctx, run.run_id, brief)
    p = producer.campaign_payload(run, 0, brief=brief, resolved=resolved)
    (interest,) = p["audience_resolution"]["interests"]
    assert interest["hint"] == "pottery" and interest["id"].isdigit()
    assert p["targeting"]["flexible_spec"] == [{"interests": [
        {"id": interest["id"], "name": "pottery"}]}]
    assert p["targeting"]["genders"] == [2]
    assert p["targeting"]["targeting_automation"] == {"advantage_audience": 1}
    assert p["lookalike"]["origin_audience_id"] == "2380000000000003"
    (r,) = [x for x in ctx.client.ledger.receipts(run.run_id)
            if x.details.get("tool") == "targeting.search"]
    assert r.details["side_effects"] == "read" and r.details["unresolved"] == 0


def test_ab_variants_and_their_review_binding(env):
    from tests.test_brief import sample

    ctx, run, _ = env
    _dispatch(ctx, run, [("run_copywriter", '{"angle_index": 0}'),
                         ("run_art_director", '{"headline": "Pair"}')])
    assert producer.ab_variants(ctx, run, 1) == [1]
    pl = sample().placements.model_dump()
    ctx.brief_v1 = sample(placements={**pl, "copy_variants": 3})
    assert producer.ab_variants(ctx, run, 1) == [1, 0, 2]
    one = producer.review_binding(run, 1)
    assert producer.review_binding(run, 1, [0, 2]) != one  # the A/B variants are covered
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 1,
                  "ab": [{"variant_index": 0}, {"variant_index": 2}]}
    p = producer.campaign_payload(run, 1)
    assert [v["headline"] for v in p["ad_variants"]] == [
        run.copy.variants[i].headline for i in (1, 0, 2)]
