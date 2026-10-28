"""Replay mode (D4-2): record through a transport, serve by request key, offline."""

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from studio import replay
from studio.muse.client import MuseClient
from studio.receipts import Ledger


def _png(size=(900, 600)):
    import random

    rnd = random.Random(7)  # noqa: S311 - test image noise
    n = size[0] * size[1] * 3
    im = Image.frombytes("RGB", size, bytes(rnd.randrange(256) for _ in range(n)))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def _fake_api(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if request.url.path.endswith("/images/generations"):
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(
            _png()).decode()}], "usage": {"input_tokens": 1, "output_tokens": 1}})
    text = body["messages"][-1]["content"]
    return httpx.Response(200, json={
        "id": "x", "model": body["model"], "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": f"echo {text[:40]}"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


def _chat(client, text, run="run_aaaabbbbccccdddd"):
    return client.chat("muse-spark-1.3", [{"role": "user", "content": text}],
                       run_id=run, agent="producer")


def _record(tmp_path, texts):
    rec = replay.RecordingTransport(httpx.MockTransport(_fake_api), blobs=tmp_path / "blobs")
    client = MuseClient("k", ledger=Ledger(), transport=rec)
    for t in texts:
        _chat(client, t)
    path = tmp_path / "demo" / "replay.json"
    rec.save(path, {"brief": "b"})
    return path, rec


def test_volatile_values_do_not_change_the_key():
    def req(text):
        return httpx.Request("POST", "https://api.meta.ai/v1/chat/completions",
                             json={"model": "m", "messages": [{"role": "user", "content": text}]})

    a = replay.request_key(req("run run_0123456789abcdef at 1761600000.25 sha 9f" + "a" * 62))
    b = replay.request_key(req("run run_fedcba9876543210 at 1761699999.5 sha 01" + "b" * 62))
    c = replay.request_key(req("another brief"))
    assert a[0] == b[0] != c[0]
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    other = {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}}
    k1 = replay.request_key(httpx.Request("POST", "https://x/v1/r", json={"input": [img]}))
    k2 = replay.request_key(httpx.Request("POST", "https://x/v1/r", json={"input": [other]}))
    assert k1[0] == k2[0]


def test_recorded_set_replays_offline_by_key(tmp_path):
    path, _ = _record(tmp_path, ["first brief", "second brief"])
    data = json.loads(path.read_text())
    assert data["_provenance"] == "recorded" and len(data["interactions"]) == 2
    assert "authorization" not in path.read_text().lower()
    tr = replay.ReplayTransport(data, blobs=tmp_path / "blobs")
    led = Ledger()
    client = MuseClient("replay", ledger=led, transport=tr)
    assert _chat(client, "second brief").text == "echo second brief"  # by key, not order
    assert _chat(client, "first brief").text == "echo first brief"
    assert tr.stats == {"exact": 2, "route": 0, "miss": 0}
    assert _chat(client, "first brief").text == "echo first brief"  # a second pass cycles


def test_unknown_text_falls_back_to_the_route_and_unknown_route_misses(tmp_path):
    path, _ = _record(tmp_path, ["ledger with 12 receipts"])
    tr = replay.ReplayTransport(json.loads(path.read_text()), blobs=tmp_path / "blobs")
    client = MuseClient("replay", ledger=Ledger(), transport=tr)
    assert _chat(client, "ledger with 40 receipts").text == "echo ledger with 12 receipts"
    assert tr.stats["route"] == 1
    with pytest.raises(Exception):  # noqa: B017 - MuseError on the 599
        client.chat("muse-spark-1.3-contributor", [{"role": "user", "content": "x"}],
                    run_id="run_x", agent="copywriter", tier="contributor")
    assert tr.stats["miss"] == 1


def test_route_fallback_prefers_the_same_output_schema(tmp_path):
    rec = replay.RecordingTransport(httpx.MockTransport(_fake_api), blobs=tmp_path / "blobs")
    client = MuseClient("k", ledger=Ledger(), transport=rec)

    def ask(c, text, schema):
        return c.chat("muse-spark-1.3", [{"role": "user", "content": text}],
                      run_id="run_aaaabbbbccccdddd", agent="producer",
                      response_format={"type": "json_schema", "json_schema": {
                          "name": schema, "strict": True, "schema": {"type": "object"}}})

    ask(client, "plan for the recorded brief", "Plan")
    ask(client, "draft of the recorded words", "BriefV1")
    path = tmp_path / "s" / "replay.json"
    rec.save(path, {"brief": "b"})
    tr = replay.ReplayTransport(json.loads(path.read_text()), blobs=tmp_path / "blobs")
    again = MuseClient("replay", ledger=Ledger(), transport=tr)
    # unknown words: the route fallback skips the (first, unused) Plan recording
    assert ask(again, "draft of other words", "BriefV1").text.startswith("echo draft of")
    assert ask(again, "draft of yet other words", "BriefV1").text.startswith("echo draft of")
    assert ask(again, "plan for another brief", "Plan").text.startswith("echo plan for")
    assert tr.stats == {"exact": 0, "route": 3, "miss": 0}


def test_images_are_stored_once_downscaled(tmp_path):
    rec = replay.RecordingTransport(httpx.MockTransport(_fake_api), blobs=tmp_path / "blobs")
    with httpx.Client(transport=rec) as c:
        for _ in range(2):
            c.post("https://api.meta.ai/v1/images/generations", json={"prompt": "mug"})
    blobs = list((tmp_path / "blobs").iterdir())
    assert len(blobs) == 1 and max(Image.open(blobs[0]).size) <= 512
    rec.save(tmp_path / "s" / "replay.json", {})
    tr = replay.ReplayTransport(json.loads((tmp_path / "s" / "replay.json").read_text()),
                                blobs=tmp_path / "blobs")
    with httpx.Client(transport=tr) as c:
        r = c.post("https://api.meta.ai/v1/images/generations", json={"prompt": "mug"})
    png = base64.b64decode(r.json()["data"][0]["b64_json"])
    assert Image.open(io.BytesIO(png)).size == (900, 600)  # scaled back to the original


def test_event_streams_and_form_bodies(tmp_path):
    sse = "event: x\ndata: {}\n\n" * 5000  # long: stored as a text blob

    def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"id": "120000000000001"})
        return httpx.Response(200, content=sse.encode(),
                              headers={"content-type": "text/event-stream"})

    rec = replay.RecordingTransport(httpx.MockTransport(handler), blobs=tmp_path / "blobs")
    with httpx.Client(transport=rec) as c:
        c.post("https://api.meta.ai/v1/responses", json={"model": "sam-3.1", "stream": True})
        c.post("https://graph.facebook.com/v25.0/act_1234567/campaigns",
               data={"name": "x", "status": "PAUSED", "access_token": "SECRET-TOKEN"})
    rec.save(tmp_path / "g" / "replay.json", {})
    text = (tmp_path / "g" / "replay.json").read_text()
    assert "SECRET-TOKEN" not in text and "@blob:" in text
    tr = replay.ReplayTransport(json.loads(text), blobs=tmp_path / "blobs")
    with httpx.Client(transport=tr) as c:
        s = c.post("https://api.meta.ai/v1/responses", json={"model": "sam-3.1", "stream": True})
        g = c.post("https://graph.facebook.com/v25.0/act_7654321/campaigns",
                   data={"name": "x", "status": "PAUSED", "access_token": "OTHER"})
    assert s.text == sse and s.headers["content-type"] == "text/event-stream"
    assert g.json() == {"id": "120000000000001"}


def test_set_names_are_restricted():
    with pytest.raises(ValueError):
        replay.set_path("../etc")
