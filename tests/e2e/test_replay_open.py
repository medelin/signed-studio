"""The recorded ``open`` set: the demo loop in open-weights mode, offline. Copy, comment
triage and lead rationale ran on Muse Spark Open; the receipts say so step by step."""

import json

import httpx
import pytest

from studio import demo, replay

CUSTOMER_AGENTS = ("community", "lead_scorer")


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("replay-open")

    def no_network(request):
        raise AssertionError(f"network call in replay: {request.url}")

    real = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda self, request: no_network(request)
    try:
        summary = demo.run("open", data_dir=tmp, log=lambda _: None)
    finally:
        httpx.HTTPTransport.handle_request = real
    receipts = [json.loads(line) for line in (tmp / "receipts.jsonl").read_text().splitlines()
                if '"manifest"' not in line]
    return summary, [r for r in receipts if r["kind"] == "model_call"]


def test_open_set_is_a_recording_in_open_weights_mode():
    data = replay.load_set("open")
    assert data["_provenance"] == "recorded" and data["open_weights"] is True
    assert data["brief"] == demo.BRIEFS["open"]["brief"]
    routes = {i["route"] for i in data["interactions"]}
    assert any("inference.baseten.co" in r and "meta-models/muse-spark-open" in r
               for r in routes)
    # customer text (comments, leads) never went to the hosted API: only the open model
    # saw the seeded comments
    for i in data["interactions"]:
        if "api.meta.ai" in i["route"]:
            assert "deliver on Saturdays" not in json.dumps(i["request"])


def test_open_loop_replays_end_to_end(loop):
    summary, _ = loop
    assert summary["verify_exit"] == 0
    assert summary["campaign_status"] == "PAUSED"
    assert summary["reply_executed"] is True
    assert summary["replay"]["miss"] == 0


def test_copy_and_customer_text_ran_only_on_open_weights(loop):
    _, calls = loop
    copy = [r for r in calls if r["agent"] == "copywriter"]
    assert copy and all(r["tier"] == "open" and r["provider"] == "baseten-replay"
                        and r["details"]["model_family"] == "muse-spark-open"
                        for r in copy)
    customer = [r for r in calls if r["agent"] in CUSTOMER_AGENTS]
    assert customer, "the comment triage must be in the set"
    for r in customer:
        assert r["tier"] == "open" and r["details"]["weights"] == "open", r["agent"]
        assert "kept on open weights" in r["tier_reason"]
    assert not [r for r in customer if r["provider"].startswith("meta-model-api")]


def test_every_model_call_names_its_family(loop):
    _, calls = loop
    assert all(r["details"].get("model_family") not in (None, "unknown") for r in calls)
    families = {r["details"]["model_family"] for r in calls}
    # independent second family on the review, hosted Spark for planning and vision
    assert {"muse-spark", "muse-spark-open"} <= families
    reviews = {r["details"]["model_family"] for r in calls if r["agent"].startswith("reviewer")}
    assert reviews == {"muse-spark", "muse-spark-open"}
