"""Attack suite (day 2 fix round, I1): parallel tool calls cannot outrun the limits.

A model that answers one turn with many expensive calls gets at most ``MAX_CALLS_PER_TURN``
dispatched, at most ``TOOL_LIMITS`` per tool per run, and the cost cap is checked before
every single call, not only between turns. Every refusal is an output for the model and a
``rejected`` receipt.
"""

import json

import httpx
import pytest
import respx

from studio import db
from studio.agents import art_director, producer
from studio.agents.schemas import CreativeImage, CreativeSet
from studio.muse.client import BASE_URL, MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PLAN = {"audience": "a", "angles": [{"name": "n", "hook": "h", "persona": "p"}] * 3,
        "budget_pln_daily": 30, "placements": ["feed_1x1"], "tasks": ["t"]}
COPY = {"variants": [{"primary_text": "t", "headline": "Warm mug", "description": "d",
                      "cta": "SHOP_NOW"}] * 3, "risks": []}
ART = ("run_art_director", '{"headline": "Warm mug"}')
COPYWRITER = ("run_copywriter", '{"angle_index": 0}')


def _chat(content):
    return {"id": "c", "model": "muse-spark-1.3", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def _turn(rid, calls=(), text=""):
    out = [{"type": "function_call", "call_id": f"call_{rid}_{i}", "name": n, "arguments": a}
           for i, (n, a) in enumerate(calls)]
    if text:
        out.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    return {"id": rid, "model": "muse-spark-1.3", "status": "completed", "output": out,
            "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(5.0))
    ctx = producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                           concept="mug", data_dir=tmp_path / "var")
    renders = []

    def fake_art(client, photo, headline, *, run_id, concept, data_dir, placements=()):
        renders.append(headline)  # each render costs $0.40 (an expensive image series)
        client.ledger.append(run_id, agent="art_director", kind="model_call",
                             model="muse-image-1.0", cost_usd=0.40)
        img = CreativeImage(placement="feed_1x1", size="1024x1024", path="x.png",
                            sha256="0" * 64, width=1, height=1, response_id="")
        return CreativeSet(cutout_path="c.png", cutout_sha256="0" * 64, foreground_pixels=1,
                           images=[img], prompts=[], response_ids=[])

    monkeypatch.setattr(art_director, "run", fake_art)
    return ctx, ledger, renders


def _run(ctx, run_id, *bodies, **kw):
    queue = list(bodies)
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            side_effect=lambda request: httpx.Response(200, json=queue.pop(0)))
        result = producer.run(ctx, "brief", run_id=run_id, **kw)
    return result, [json.loads(c.request.content) for c in route.calls]


def _outputs(body):
    return [json.loads(i["output"]) for i in body["input"]]


def _rejected(ledger, run_id):
    return [r for r in ledger.receipts(run_id)
            if r.kind == "tool_call" and r.details.get("status") == "rejected"]


def test_cost_cap_is_checked_before_every_call_in_a_parallel_turn(env):
    ctx, ledger, renders = env
    turn = _turn("r1", [COPYWRITER, ART, ART])
    result, _ = _run(ctx, "atk-i1a", _chat(PLAN), turn, _chat(COPY), max_cost_usd=0.30)
    assert len(renders) == 1  # the first render crossed the cap; the second never ran
    assert result.status == "max_cost" and not result.pending_id
    (r,) = _rejected(ledger, "atk-i1a")
    assert r.details["tool"] == "run_art_director" and "cost" in r.details["reason"]


def test_at_most_four_calls_are_dispatched_per_turn(env):
    ctx, ledger, _ = env
    six = [COPYWRITER] * 6
    result, sent = _run(ctx, "atk-i1b", _chat(PLAN), _turn("r1", six),
                        *[_chat(COPY)] * producer.MAX_CALLS_PER_TURN,
                        _turn("r2", text="ok"))
    outs = _outputs(sent[-1])
    assert len(outs) == 6  # every call gets an output, in order
    assert all("error" not in o for o in outs[:4])
    assert all("per turn" in o["error"] for o in outs[4:])
    assert len(_rejected(ledger, "atk-i1b")) == 2


def test_art_director_is_limited_per_run_across_turns(env):
    ctx, ledger, renders = env
    turns = [_chat(PLAN), _turn("r1", [COPYWRITER, ART, ART]), _chat(COPY),
             _turn("r2", [ART, ART]), _turn("r3", text="ok")]
    result, sent = _run(ctx, "atk-i1c", *turns)
    assert len(renders) == producer.TOOL_LIMITS["run_art_director"] == 2
    assert all("limit" in o["error"] for o in _outputs(sent[-1]))
    assert [r.details["tool"] for r in _rejected(ledger, "atk-i1c")] == ["run_art_director"] * 2
    assert producer.TOOL_LIMITS["run_reviewers"] == 2
