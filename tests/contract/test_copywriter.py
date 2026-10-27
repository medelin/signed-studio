"""Copywriter agent (D2-3) against a recorded Contributor call."""

import json

import httpx
import pytest
import respx

from studio.agents import copywriter, prompts
from studio.agents.schemas import Angle, CopyPack
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, mount

ANGLE = Angle(name="Morning ritual", hook="Your first coffee deserves a real mug",
              persona="home-office worker, 30-45")


@pytest.fixture
def ledger():
    return Ledger()


@pytest.fixture
def client(ledger):
    return MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))


def test_copy_pack_from_contributor(client, ledger):
    with respx.mock() as router:
        sent = mount(router, "copy_contributor", BASE_URL)
        pack, result = copywriter.write(client, ANGLE, "home-office worker", run_id="run-1")
    assert isinstance(pack, CopyPack) and len(pack.variants) == 3
    body = sent[0]
    assert body["model"] == "muse-spark-1.3-contributor"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][0]["content"].startswith(prompts.stable_prefix())
    receipt = ledger.receipts("run-1")[-1]
    assert receipt.tier == "contributor" and "no pii" in receipt.tier_reason
    assert receipt.agent == "copywriter" and result.cost_usd > 0
    for v in pack.variants:
        if "[FACT]" in v.primary_text + v.headline + v.description:
            assert any("[FACT]" in r for r in pack.risks)


def test_pii_in_the_angle_routes_to_standard(client, ledger):
    angle = ANGLE.model_copy(update={"hook": "Call 600 100 200 for a mug"})
    fake = {"id": "c1", "model": "muse-spark-1.3", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"variants": [_variant()] * 3, "risks": []})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=fake))
        copywriter.write(client, angle, "x", run_id="run-2")
    assert json.loads(route.calls[0].request.content)["model"] == "muse-spark-1.3"
    assert "phone" in ledger.receipts("run-2")[-1].tier_reason


def _variant(text="Hand-thrown mug"):
    return {"primary_text": text, "headline": "h", "description": "d", "cta": "SHOP_NOW"}


def test_unfilled_fact_is_flagged_as_risk(client):
    content = {"variants": [_variant("Only [FACT] left"), _variant(), _variant()], "risks": []}
    fake = {"id": "c1", "model": "muse-spark-1.3-contributor", "choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json=fake))
        pack, _ = copywriter.write(client, ANGLE, "x", run_id="run-3")
    assert any("[FACT]" in r and "variant 1" in r for r in pack.risks)
