"""Attack suite (day 2 fix round, I3): copy with an unfilled ``[FACT]`` cannot be signed.

The copywriter writes ``[FACT]`` where the brand sheet lacks a fact. A model (or an
injected brief) that pushes such a variant to approval anyway is refused in code, and the
copy's ``risks`` travel inside the payload the human signs.
"""

import json

import pytest

from studio import db
from studio.agents import producer
from studio.agents.dispatcher import Dispatcher
from studio.agents.schemas import CopyPack, CreativeImage, CreativeSet, Plan
from studio.muse.client import MuseClient, ToolCall
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PLAN = {"audience": "a", "angles": [{"name": "n", "hook": "h", "persona": "p"}] * 3,
        "budget_pln_daily": 30, "placements": ["feed_1x1"], "tasks": ["t"]}
CLEAN = {"primary_text": "Hand-thrown, 350 ml.", "headline": "Warm mug", "description": "89 PLN",
         "cta": "SHOP_NOW"}


@pytest.fixture
def ctx(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    return producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                            concept="mug", data_dir=tmp_path / "var")


def _run(variant, risks):
    run = producer.RunResult(run_id="atk-i3", status="running")
    run.plan = Plan.model_validate(PLAN)
    run.copy = CopyPack.model_validate({"variants": [variant, CLEAN, CLEAN], "risks": risks})
    img = CreativeImage(placement="feed_1x1", size="1024x1024", path="x.png", sha256="0" * 64,
                        width=1, height=1, response_id="")
    run.creatives = CreativeSet(cutout_path="c.png", cutout_sha256="0" * 64,
                                foreground_pixels=1, images=[img], prompts=[],
                                response_ids=[])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [{"placement": "feed_1x1", "sha256": "0" * 64, "verdict": "OK"}],
                  "variant_index": 0, "binding": producer.review_binding(run, 0)}
    return run


def _approve(ctx, run):
    d = Dispatcher(producer._handlers(ctx, run, "brief"), ledger=ctx.client.ledger,
                   run_id=run.run_id)
    return d.execute_or_refuse(ToolCall("c1", "request_approval", '{"summary": "all OK"}'))


@pytest.mark.parametrize("field", ["primary_text", "headline", "description"])
@pytest.mark.parametrize("token", ["[FACT]", "[fact]", "[ FACT ]"])
def test_variant_with_unfilled_fact_is_refused(ctx, field, token):
    variant = {**CLEAN, field: f"Glazed in {token} colours"}
    out = _approve(ctx, _run(variant, ["variant 1: unfilled [FACT] placeholder"]))
    assert out.status == "error" and "[FACT]" in out.output["error"]
    assert ctx.conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_risks_are_part_of_the_signed_payload(ctx):
    risks = ["variant 2: unfilled [FACT] placeholder, needs a real fact before use"]
    out = _approve(ctx, _run(CLEAN, risks))
    assert out.status == "ok"
    (payload,) = ctx.conn.execute("SELECT payload FROM pending_payloads").fetchone()
    assert json.loads(payload)["risks"] == risks
