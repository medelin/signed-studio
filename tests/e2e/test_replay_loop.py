"""D4-2: ``make demo-replay`` — the whole loop offline from the recorded demo set, < 60 s,
ending with ``studio verify`` OK."""

import json
import time

import httpx
import pytest

from studio import demo, replay


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("replay-loop")

    def no_network(request):  # replay must never reach the network
        raise AssertionError(f"network call in replay: {request.url}")

    real = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda self, request: no_network(request)
    try:
        started = time.monotonic()
        summary = demo.run("demo", data_dir=tmp, log=lambda _: None)
        elapsed = time.monotonic() - started
    finally:
        httpx.HTTPTransport.handle_request = real
    return summary, elapsed, tmp


def test_whole_loop_replays_offline_in_under_a_minute(loop):
    summary, elapsed, _ = loop
    assert elapsed < 60, elapsed
    assert summary["status"] in ("awaiting_approval", "approved")
    assert summary["campaign_status"] == "PAUSED"
    assert summary["reply_executed"] is True
    assert summary["lead"] and summary["lead"]["score"] is not None
    assert summary["report"] is True
    assert summary["replay"]["miss"] == 0


def test_studio_verify_passes_on_the_replayed_export(loop):
    summary, _, tmp = loop
    assert summary["verify_exit"] == 0
    lines = [json.loads(x) for x in (tmp / "receipts.jsonl").read_text().splitlines()]
    receipts = [r for r in lines if "manifest" not in r]
    kinds = {r["kind"] for r in receipts}
    assert {"model_call", "tool_call", "review", "approval", "external_write"} <= kinds
    writes = [r["details"]["action"] for r in receipts if r["kind"] == "external_write"]
    assert sorted(writes) == ["campaign.create", "campaign.update", "reply.post"]
    providers = {r["provider"] for r in receipts if r["kind"] == "model_call"}
    assert providers == {"meta-model-api-replay", "baseten-replay"}  # labelled as a replay
    reviews = [r for r in receipts if r["kind"] == "review"]
    assert reviews and all(r["details"]["second_opinion"] == "available" for r in reviews)


def test_the_demo_set_is_a_real_recording():
    data = replay.load_set("demo")
    assert data["_provenance"] == "recorded" and data["brief"] == demo.BRIEFS["demo"]["brief"]
    text = json.dumps(data).lower()
    assert "authorization" not in text and "bearer" not in text
    models = {i["route"].split()[3] for i in data["interactions"] if len(i["route"].split()) > 3}
    assert {"muse-spark-1.3", "muse-spark-1.3-contributor", "sam-3.1",
            "muse-image-1.0", "meta-models/muse-spark-open"} <= models
