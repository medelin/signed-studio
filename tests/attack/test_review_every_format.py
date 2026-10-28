"""Attack suite (day 2 fix round, C2): every signed image has been reviewed.

A clean hero (1:1) must not carry a story image the reviewers never saw: each placement
is reviewed on its own and the worst verdict wins.
"""

import json

import httpx
import pytest
import respx

from studio import db
from studio.agents import producer
from studio.agents.dispatcher import Dispatcher
from studio.agents.schemas import CopyPack, CreativeImage, CreativeSet, Plan
from studio.muse.client import BASE_URL, MuseClient, ToolCall
from studio.receipts import sha256_hex
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT
from tests.images import placement_png

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PNG = placement_png("feed_1x1")
PLACEMENTS = ("feed_1x1", "feed_4x5", "story_9x16")
PLAN = {"audience": "a", "angles": [{"name": "n", "hook": "h", "persona": "p"}] * 3,
        "budget_pln_daily": 30, "placements": list(PLACEMENTS), "tasks": ["t"]}


def _review(verdict, findings=()):
    return {"id": "c", "model": "muse-spark-1.3", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"verdict": verdict, "findings": list(findings),
                                               "summary": "s"})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    data_dir = tmp_path / "var"
    ctx = producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                           concept="mug", data_dir=data_dir)
    run = producer.RunResult(run_id="atk-c2", status="running")
    run.plan = Plan.model_validate(PLAN)
    v = {"primary_text": "t", "headline": "h", "description": "d", "cta": "SHOP_NOW"}
    run.copy = CopyPack.model_validate({"variants": [v] * 3, "risks": []})
    images = []
    out = data_dir / "assets" / run.run_id
    out.mkdir(parents=True)
    for n, placement in enumerate(PLACEMENTS):
        png = placement_png(placement) + bytes([n])
        (out / f"{placement}.png").write_bytes(png)
        images.append(CreativeImage(placement=placement, size="1024x1024",
                                    path=f"assets/{run.run_id}/{placement}.png",
                                    sha256=sha256_hex(png), width=16, height=16,
                                    response_id=""))
    run.creatives = CreativeSet(cutout_path="c.png", cutout_sha256="0" * 64,
                                foreground_pixels=1, images=images, prompts=[],
                                response_ids=[])
    return ctx, conn, run, data_dir


def _dispatch(ctx, run, calls):
    d = Dispatcher(producer._handlers(ctx, run, "brief"), ledger=ctx.client.ledger,
                   run_id=run.run_id)
    return [d.execute_or_refuse(ToolCall(f"c{i}", n, a)) for i, (n, a) in enumerate(calls)]


def _run(ctx, run, *reviews):
    queue = list(reviews)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL).mock(
            side_effect=lambda request: httpx.Response(200, json=queue.pop(0)))
        outs = _dispatch(ctx, run, [("run_reviewers", '{"variant_index": 0}'),
                                    ("request_approval", '{"summary": "x"}')])
    return outs, [json.loads(c.request.content) for c in route.calls]


def _image_url(body):
    return next(p["image_url"]["url"] for p in body["messages"][1]["content"]
                if p["type"] == "image_url")


def test_a_blocked_story_image_blocks_the_campaign(env):
    ctx, conn, run, _ = env
    block = [{"rule": "P3", "evidence": "a face in the story image", "severity": "block"}]
    outs, sent = _run(ctx, run, _review("OK"), _review("OK"), _review("BLOCK", block))
    assert len(sent) == 3 and len({_image_url(b) for b in sent}) == 3  # each image seen
    assert run.review["verdict"] == "BLOCK"
    assert [f["placement"] for f in run.review["formats"]] == list(PLACEMENTS)
    assert run.review["formats"][2]["verdict"] == "BLOCK"
    assert outs[0].output["verdict"] == "BLOCK"
    assert outs[1].status == "error" and "blocked" in outs[1].output["error"]
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_the_payload_lists_the_verdict_of_every_signed_image(env):
    ctx, conn, run, _ = env
    warn = [{"rule": "P4", "evidence": "price small", "severity": "warn"}]
    outs, _ = _run(ctx, run, _review("OK"), _review("WARN", warn), _review("OK"))
    assert [o.status for o in outs] == ["ok", "ok"]
    (payload,) = conn.execute("SELECT payload FROM pending_payloads").fetchone()
    data = json.loads(payload)
    assert data["review"]["verdict"] == "WARN"
    reviewed = {(f["placement"], f["sha256"]) for f in data["review"]["formats"]}
    assert reviewed == {(i["placement"], i["sha256"]) for i in data["images"]}


def test_an_image_changed_on_disk_after_rendering_is_not_reviewed(env):
    ctx, conn, run, data_dir = env
    (data_dir / run.creatives.images[1].path).write_bytes(PNG + b"swapped")
    outs, sent = _run(ctx, run, _review("OK"), _review("OK"), _review("OK"))
    assert sent == []  # every hash is checked before any review call
    assert outs[0].status == "error" and "changed on disk" in outs[0].output["error"]
    assert run.review is None
    assert conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)
