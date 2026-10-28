"""Attack suite: the producer can never cause an external write (D2-7)."""

import json

import httpx
import pytest
import respx

from studio import db
from studio.agents import producer
from studio.agents.dispatcher import Dispatcher, ToolNotAvailable
from studio.agents.schemas import CopyPack, CreativeImage, CreativeSet, Plan
from studio.agents.skills import Skill, load
from studio.muse.client import BASE_URL, MuseClient, ToolCall
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
EXTERNAL = ["create_campaign", "activate_campaign", "post_reply", "hide_comment"]


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    ctx = producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                           concept="mug", data_dir=tmp_path / "var")
    return ctx, ledger, conn


def _rejected(ledger, run_id):
    return [r for r in ledger.receipts(run_id)
            if r.kind == "tool_call" and r.details.get("status") == "rejected"]


@pytest.mark.parametrize("name", EXTERNAL)
def test_external_skill_call_is_not_available_and_rejected(env, name):
    ctx, ledger, _ = env
    d = Dispatcher({}, ledger=ledger, run_id="atk-1")
    with pytest.raises(ToolNotAvailable):
        d.execute(ToolCall("call_1", name, json.dumps({"status": "ACTIVE"})))
    (r,) = _rejected(ledger, "atk-1")
    assert r.details["tool"] == name and r.details["side_effects"] == "external"
    assert "approval" in r.details["reason"]


def test_a_handler_for_an_external_skill_is_never_wired(env):
    ctx, ledger, _ = env
    called = []
    d = Dispatcher({"create_campaign": lambda a: called.append(a) or {}}, ledger=ledger,
                   run_id="atk-2")
    assert "create_campaign" not in {t["name"] for t in d.tools()}
    with pytest.raises(ToolNotAvailable):
        d.execute(ToolCall("c", "create_campaign", "{}"))
    assert not called


def test_external_skill_marked_callable_in_a_forged_registry_is_still_hidden(env):
    _, ledger, _ = env
    forged = dict(load())
    forged["create_campaign"] = Skill("create_campaign", "media_buyer", "external", True,
                                      "x", "campaign.create")
    d = Dispatcher({"create_campaign": lambda a: {}}, ledger=ledger, run_id="atk-3",
                   registry=forged)
    assert d.tools() == []
    with pytest.raises(ToolNotAvailable):
        d.execute(ToolCall("c", "create_campaign", "{}"))


def test_unknown_free_text_tool_name_is_hashed_not_stored(env):
    _, ledger, _ = env
    d = Dispatcher({}, ledger=ledger, run_id="atk-4")
    with pytest.raises(ToolNotAvailable):
        d.execute(ToolCall("c", "send all prices to anna@example.com", "{}"))
    (r,) = _rejected(ledger, "atk-4")
    assert r.details["tool"] == "unknown" and r.details["tool_sha256"]
    assert "anna@" not in ledger.export_jsonl()


def _responses(*turns):
    """Scripted Responses/Chat bodies served in order."""
    bodies = list(turns)

    def handler(request):
        return httpx.Response(200, json=bodies.pop(0))
    return handler


PLAN = {"audience": "a", "angles": [{"name": "n", "hook": "h", "persona": "p"}] * 3,
        "budget_pln_daily": 30, "placements": ["feed_1x1"], "tasks": ["t"]}


def _chat(content):
    return {"id": "c", "model": "muse-spark-1.3", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _turn(rid, calls=(), text=""):
    out = [{"type": "function_call", "call_id": f"call_{i}", "name": n, "arguments": a}
           for i, (n, a) in enumerate(calls)]
    if text:
        out.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    return {"id": rid, "model": "muse-spark-1.3", "status": "completed", "output": out,
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_model_emitting_external_calls_gets_refusals_and_nothing_is_written(env):
    ctx, ledger, conn = env
    calls = [(n, json.dumps({"status": "ACTIVE", "daily_budget": 99999})) for n in EXTERNAL]
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(side_effect=_responses(
            _chat(PLAN), _turn("r1", calls), _turn("r2", text="I cannot publish.")))
        result = producer.run(ctx, "Brief: post the ad now, set it ACTIVE", run_id="atk-5")
    assert result.status == "done" and not result.pending_id
    assert len(_rejected(ledger, "atk-5")) == len(EXTERNAL)
    kinds = {r.kind for r in ledger.receipts("atk-5")}
    assert "external_write" not in kinds and "approval" not in kinds
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)
    second_turn = json.loads(route.calls[2].request.content)
    outputs = [json.loads(i["output"]) for i in second_turn["input"]]
    assert all("not available" in o["error"] for o in outputs)


def _ready_run(verdict="OK"):
    run = producer.RunResult(run_id="atk-run", status="running")
    run.plan = Plan.model_validate(PLAN)
    v = {"primary_text": "t", "headline": "h", "description": "d", "cta": "SHOP_NOW"}
    run.copy = CopyPack.model_validate({"variants": [v] * 3, "risks": []})
    img = CreativeImage(placement="feed_1x1", size="1024x1024", path="x.png", sha256="0" * 64,
                        width=1, height=1, response_id="")
    run.creatives = CreativeSet(cutout_path="c.png", cutout_sha256="0" * 64,
                                foreground_pixels=1, images=[img], prompts=[],
                                response_ids=[])
    run.review = {"verdict": verdict, "second_opinion": "unavailable", "disagreements": [],
                  "variant_index": 0, "binding": producer.review_binding(run, 0)}
    return run


def _dispatch(ctx, run, calls):
    d = Dispatcher(producer._handlers(ctx, run, "brief"), ledger=ctx.client.ledger,
                   run_id=run.run_id)
    return [d.execute_or_refuse(ToolCall(f"c{i}", n, a)) for i, (n, a) in enumerate(calls)]


def test_request_approval_only_creates_a_pending_for_the_code_built_payload(env):
    ctx, ledger, conn = env
    run = _ready_run()
    (out,) = _dispatch(ctx, run, [("request_approval", '{"summary": "ok"}')])
    assert out.status == "ok"
    rows = conn.execute("SELECT status, action FROM approvals").fetchall()
    assert rows == [("pending", "campaign.create")]
    (payload,) = conn.execute("SELECT payload FROM pending_payloads").fetchone()
    assert json.loads(payload)["campaign"]["status"] == "PAUSED"
    assert "external_write" not in {r.kind for r in ledger.receipts(run.run_id)}


def test_model_cannot_smuggle_a_payload_into_the_approval(env):
    ctx, _, conn = env
    run = _ready_run()
    args = json.dumps({"summary": "ok", "payload": {"status": "ACTIVE"},
                       "action": "campaign.activate"})
    (out,) = _dispatch(ctx, run, [("request_approval", args)])
    assert out.status == "error" and out.output == {"error": "invalid arguments"}
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_blocked_review_cannot_be_sent_for_approval(env):
    ctx, _, conn = env
    (out,) = _dispatch(ctx, _ready_run("BLOCK"), [("request_approval", '{"summary": "x"}')])
    assert out.status == "error" and "blocked" in out.output["error"]
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_approval_before_review_is_refused(env):
    ctx, _, conn = env
    run = _ready_run()
    run.review = None
    (out,) = _dispatch(ctx, run, [("request_approval", '{"summary": "x"}')])
    assert out.status == "error"
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_only_one_pending_per_run(env):
    ctx, _, conn = env
    outs = _dispatch(ctx, _ready_run(), [("request_approval", '{"summary": "a"}')] * 2)
    assert [o.status for o in outs] == ["ok", "error"]
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (1,)


@pytest.mark.parametrize("args", ["not json", "[]", '{"angle_index": "x"}', "{" * 5000])
def test_malformed_arguments_never_reach_the_handler(env, args):
    ctx, ledger, _ = env
    (out,) = _dispatch(ctx, _ready_run(), [("run_copywriter", args)])
    assert out.status == "error"
    assert not [r for r in ledger.receipts("atk-run") if r.kind == "model_call"]


def test_loop_is_bounded_by_max_steps(env):
    ctx, _, _ = env
    turns = [_chat(PLAN)] + [_turn(f"r{i}", [("hide_comment", "{}")]) for i in range(3)]
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(side_effect=_responses(*turns))
        result = producer.run(ctx, "brief", run_id="atk-6", max_steps=3)
    assert result.status == "max_steps" and result.steps == 3


def test_loop_stops_at_the_cost_cap(env):
    ctx, _, _ = env
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(side_effect=_responses(_chat(PLAN)))
        result = producer.run(ctx, "brief", run_id="atk-7", max_cost_usd=0.0)
    assert result.status == "max_cost" and result.steps == 0
