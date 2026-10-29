"""Attack suite (day 2 fix round): the model's plan cannot multiply image spend.

``placements`` comes from the model. A plan with 50 (duplicated) placements is refused by
the schema; a plan that bypasses validation is still deduplicated and clipped before the
art director runs, and the art director checks the run's remaining budget before every
image.
"""

import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import ValidationError

from studio import db
from studio.agents import art_director, producer
from studio.agents.schemas import PLACEMENT_SIZES, Plan
from studio.muse import image
from studio.muse.client import BASE_URL, MuseClient
from studio.muse.sam import Cutout
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PNG = (ROOT / "fixtures" / "images" / "red-16.png").read_bytes()
ANGLES = [{"name": "n", "hook": "h", "persona": "p"}] * 3
FIFTY = {"audience": "a", "angles": ANGLES, "budget_pln_daily": 30,
         "placements": ["feed_1x1", "feed_4x5", "story_9x16"] * 16 + ["feed_1x1"] * 2,
         "tasks": ["t"]}
COPY = {"variants": [{"primary_text": "t", "headline": "Warm mug", "description": "d",
                      "cta": "SHOP_NOW"}] * 3, "risks": [], "unfilled_facts": 0}


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


def test_schema_refuses_duplicated_or_too_many_placements():
    with pytest.raises(ValidationError):
        Plan.model_validate(FIFTY)
    with pytest.raises(ValidationError):
        Plan.model_validate({**FIFTY, "placements": ["feed_1x1", "feed_1x1"]})
    ok = Plan.model_validate({**FIFTY, "placements": list(PLACEMENT_SIZES)})
    assert len(ok.placements) == len(PLACEMENT_SIZES)


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(5.0))
    client.unrecorded = frozenset({"sam.check"})  # cassette recorded before sam.check
    ctx = producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                           concept="mug", data_dir=tmp_path / "var")
    images = []

    def fake_image(client, *a, run_id, agent, **kw):
        images.append(kw.get("size"))
        client.ledger.append(run_id, agent=agent, kind="model_call", model="muse-image-1.0",
                             cost_usd=art_director.IMAGE_COST_USD)
        return image.ImageOut(PNG, 16, 16, SimpleNamespace(response_id=""))

    monkeypatch.setattr(art_director.sam, "cutout", lambda *a, **kw: Cutout(
        alpha_png=PNG, crop_png=PNG, box=(0, 0, 1, 1), mask_px=(1, 1), foreground_pixels=1,
        matches=1, response_id="", receipt_id=""))
    monkeypatch.setattr(art_director, "_flatten", lambda png: png)
    monkeypatch.setattr(art_director, "write_scene", lambda *a, **kw: "a desk")
    monkeypatch.setattr(art_director.image, "respond_image", fake_image)
    monkeypatch.setattr(art_director.image, "edit", fake_image)
    return ctx, images


def _run(ctx, plan, max_cost_usd):
    bodies = [_turn("r1", [("run_copywriter", '{"angle_index": 0}'),
                           ("run_art_director", '{"headline": "Warm mug"}')]),
              _chat(COPY), _turn("r2", text="done")]
    with respx.mock(assert_all_called=False) as router:
        router.post(url__startswith=BASE_URL).mock(
            side_effect=lambda request: httpx.Response(200, json=bodies.pop(0)))
        orig = producer.make_plan
        producer.make_plan = lambda *a: plan
        try:
            return producer.run(ctx, "brief", run_id="atk-pl", max_cost_usd=max_cost_usd)
        finally:
            producer.make_plan = orig


def test_fifty_placements_render_at_most_three_images(env):
    ctx, images = env
    plan = Plan.model_construct(**{**FIFTY, "angles": Plan.model_validate(
        {**FIFTY, "placements": ["feed_1x1"]}).angles})
    result = _run(ctx, plan, max_cost_usd=1.0)
    assert len(images) == 3
    assert result.cost_usd < 1.0
    assert [i.placement for i in result.creatives.images] == list(PLACEMENT_SIZES)


def test_art_director_stops_before_an_image_that_would_exceed_the_cap(env):
    ctx, images = env
    plan = Plan.model_validate({**FIFTY, "placements": list(PLACEMENT_SIZES)})
    result = _run(ctx, plan, max_cost_usd=0.025)
    assert len(images) == 2  # $0.01 each; a third would make $0.03 > $0.025
    assert result.cost_usd <= 0.025 and result.creatives is None
    refused = [r for r in ctx.client.ledger.receipts("atk-pl")
               if r.details.get("tool") == "run_art_director"]
    assert refused[0].details["status"] == "error"
