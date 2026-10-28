"""Contract tests for the Muse client, offline, against recorded Meta Model API cassettes."""

import json
import re
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from studio.muse.client import (
    BASE_URL,
    MuseClient,
    MuseError,
    Usage,
    cost_usd,
)
from studio.muse.structured import StructuredOutputError, strict_schema, structured
from studio.muse.vision import chat_image_part, data_url, sniff_mime, vision_parts
from studio.receipts import Ledger, sha256_hex
from studio.settings import Budget, BudgetExceeded, ReadOnlyMode

ROOT = Path(__file__).resolve().parents[2]
CASSETTES = ROOT / "fixtures" / "cassettes"
RED_PNG = (ROOT / "fixtures" / "images" / "red-16.png").read_bytes()  # wire tests only
RED_256 = (ROOT / "fixtures" / "images" / "red-256.png").read_bytes()  # recorded vision call
FAKE_KEY = "test-key-not-a-secret"


class AdLine(BaseModel):
    headline: str
    cta: str


def cassette(name):
    return json.loads((CASSETTES / f"{name}.json").read_text())["interactions"]


def mount(router, name):
    """Serve a cassette's responses in order; return the list of sent request bodies."""
    sent = []
    interactions = cassette(name)

    def handler(request):
        i = len(sent)
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert request.url.path.endswith(interactions[i]["request"]["path"])
        r = interactions[i]["response"]
        return httpx.Response(r["status"], json=r["json"])

    router.post(url__startswith=BASE_URL).mock(side_effect=handler)
    return sent, interactions


@pytest.fixture
def ledger():
    return Ledger()


@pytest.fixture
def client(ledger):
    return MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))


def test_cassettes_contain_no_secrets():
    files = list(CASSETTES.glob("*.json"))
    assert len(files) >= 3
    for path in files:
        text = path.read_text()
        assert "authorization" not in text.lower()
        assert "bearer" not in text.lower()
        assert not re.search(r"\b(sk|mk|key)[-_][A-Za-z0-9]{16,}", text)
        data = json.loads(text)
        if path.name.startswith("graph_"):  # hand-written Graph API cassettes
            assert data["_synthetic"] is True and "access_token" not in text
            continue
        for item in data["interactions"]:
            # multipart (voice) requests keep only the request JSON and audio hashes
            assert set(item["request"]) in ({"method", "path", "json"},
                                            {"method", "path", "multipart"})
            assert set(item["response"]) in ({"status", "json"}, {"status", "sse"})
        # big images live in fixture files (``@file:``), never inline in a cassette
        assert not re.search(r"[A-Za-z0-9+/=]{8000,}", text), path.name


def test_strict_structured_output(client, ledger):
    with respx.mock() as router:
        sent, interactions = mount(router, "spark_structured")
        obj, result = structured(
            client, "muse-spark-1.3",
            [{"role": "user", "content": "Write one ad line for a sourdough bakery."}],
            AdLine, tier="standard", tier_reason="test", run_id="run-1", agent="copywriter",
            max_tokens=400, reasoning_effort="minimal",
        )
    assert isinstance(obj, AdLine) and obj.headline and obj.cta
    fmt = sent[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False
    usage = interactions[0]["response"]["json"]["usage"]
    receipt = ledger.receipts("run-1")[-1]
    assert receipt.kind == "model_call" and receipt.provider == "meta-model-api"
    assert receipt.model == interactions[0]["response"]["json"]["model"]
    assert receipt.tokens_in == usage["prompt_tokens"]
    assert receipt.tokens_out == usage["completion_tokens"]
    assert receipt.cost_usd == pytest.approx(result.cost_usd) and result.cost_usd > 0
    assert receipt.output_sha256 == sha256_hex(result.text)
    assert receipt.tier == "standard" and receipt.tier_reason == "test"
    assert client.budget.spent() == pytest.approx(result.cost_usd)


def test_tool_call_round_trip(client, ledger):
    tools = [{
        "type": "function", "name": "lookup_price",
        "description": "Price of a product in PLN.",
        "parameters": {"type": "object", "properties": {"product": {"type": "string"}},
                       "required": ["product"], "additionalProperties": False},
    }]
    with respx.mock() as router:
        sent, _ = mount(router, "spark_tool_call")
        first = client.respond(
            "muse-spark-1.3", "How much is the sourdough subscription? Use the tool.",
            tools=tools, reasoning={"effort": "low"}, run_id="run-1", agent="producer",
            max_output_tokens=300,
        )
        assert first.tool_calls and first.tool_calls[0].name == "lookup_price"
        call = first.tool_calls[0]
        assert json.loads(call.arguments)["product"]
        second = client.respond(
            "muse-spark-1.3",
            [{"type": "function_call_output", "call_id": call.call_id, "output": "49 PLN"}],
            tools=tools, previous_response_id=first.response_id, reasoning={"effort": "low"},
            run_id="run-1", agent="producer", max_output_tokens=300,
        )
    assert "49" in second.text
    assert sent[1]["previous_response_id"] == first.response_id
    r1, r2 = ledger.receipts("run-1")
    assert r2.prev == r1.hash
    assert r1.details["tool_calls"] == ["lookup_price"]


def test_red_256_fixture_is_a_solid_256px_png():
    import struct
    import zlib

    assert RED_256.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", RED_256[16:24])
    assert width >= 256 and height >= 256
    idat = RED_256[RED_256.index(b"IDAT") + 4:RED_256.index(b"IEND") - 8]
    pixels = zlib.decompress(idat)
    assert set(pixels[1:width * 3 + 1:3]) == {255} and set(pixels[2:width * 3 + 1:3]) == {0}


def test_vision_recognises_the_colour(client, ledger):
    with respx.mock() as router:
        sent, interactions = mount(router, "spark_vision")
        result = client.chat(
            "muse-spark-1.3",
            [{"role": "user", "content": [
                {"type": "text", "text": "What colour is this square? One word."},
                chat_image_part(RED_256),
            ]}],
            run_id="run-1", agent="reviewer", max_tokens=1500, reasoning_effort="low",
        )
    assert "red" in result.text.lower() and result.finish == "stop"
    url = sent[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert url == interactions[0]["request"]["json"]["messages"][0]["content"][1][
        "image_url"]["url"]  # the cassette was recorded on exactly this image
    receipt = ledger.receipts("run-1")[-1]
    assert receipt.input_sha256 == [sha256_hex(RED_256)]


def test_read_only_makes_no_call(ledger):
    c = MuseClient(FAKE_KEY, ledger=ledger, read_only=True)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL)
        with pytest.raises(ReadOnlyMode):
            c.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                   agent="a")
    assert not route.called


def test_budget_exceeded_blocks_before_call(ledger):
    b = Budget(0.01)
    b.charge(0.01)
    c = MuseClient(FAKE_KEY, ledger=ledger, budget=b)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL)
        with pytest.raises(BudgetExceeded):
            c.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                   agent="a")
    assert not route.called


def test_http_error_leaves_receipt_without_secrets(client, ledger):
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(
            400, json={"error": {"type": "invalid_request_error", "message": "bad schema"}}))
        with pytest.raises(MuseError) as exc:
            client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                        agent="a")
    assert exc.value.status == 400 and exc.value.code == "invalid_request_error"
    receipt = ledger.receipts("r")[-1]
    assert receipt.details["error_status"] == 400
    assert FAKE_KEY not in json.dumps(receipt.__dict__) and FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in repr(client)


def test_network_error_leaves_receipt(client, ledger):
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(MuseError):
            client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                        agent="a")
    assert ledger.receipts("r")[-1].details["error"] == "network"


def test_invalid_structured_output_raises(client):
    body = {"id": "x", "model": "muse-spark-1.3", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "not json", "refusal": None}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}}
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(StructuredOutputError):
            structured(client, "muse-spark-1.3", [{"role": "user", "content": "x"}], AdLine,
                       tier="standard", tier_reason="t", run_id="r", agent="a")


def test_tier_must_match_model(client):
    with pytest.raises(ValueError, match="tier"):
        client.chat("muse-spark-1.3", [], run_id="r", agent="a", tier="contributor")
    with pytest.raises(ValueError, match="tier"):
        client.chat("muse-spark-1.3-contributor", [], run_id="r", agent="a", tier="standard")


def test_unknown_model_rejected(client):
    with pytest.raises(ValueError, match="model"):
        client.chat("gpt-4o", [], run_id="r", agent="a")


def test_cost_table():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cached_tokens=0)
    assert cost_usd("muse-spark-1.3", u) == pytest.approx(1.25 + 4.25)
    assert cost_usd("muse-spark-1.3-contributor", u) == pytest.approx(0.10 + 0.20)
    assert cost_usd("muse-wonder-1.0", u) == pytest.approx(24.0)
    cached = Usage(input_tokens=1_000_000, output_tokens=0, cached_tokens=1_000_000)
    assert cost_usd("muse-spark-1.3", cached) == pytest.approx(0.15)


class Inner(BaseModel):
    rule: str
    severity: int = 1


class Outer(BaseModel):
    verdict: str
    findings: list[Inner]
    note: str | None = None


def test_strict_schema_is_strict_recursively():
    schema = strict_schema(Outer)
    assert schema["type"] == "object"

    def walk(node):
        if isinstance(node, dict):
            assert "title" not in node and "default" not in node
            assert "allOf" not in node and "oneOf" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert sorted(node["required"]) == sorted(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


def test_vision_helpers():
    assert sniff_mime(RED_PNG) == "image/png"
    assert data_url(RED_PNG).startswith("data:image/png;base64,")
    assert vision_parts(RED_PNG)["type"] == "input_image"
    with pytest.raises(ValueError):
        sniff_mime(b"GIF-but-not-really")
    with pytest.raises(ValueError):
        data_url(b"\x89PNG\r\n\x1a\n" + b"0" * 50_000_001)


class Tricky(BaseModel):
    title: str
    default: int


def test_strict_schema_keeps_property_names_that_look_like_keywords():
    schema = strict_schema(Tricky)
    assert sorted(schema["properties"]) == ["default", "title"]
    assert sorted(schema["required"]) == ["default", "title"]


def _ok_chat():
    return httpx.Response(200, json={
        "id": "x", "model": "muse-spark-1.3",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok", "refusal": None}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})


def test_malformed_data_url_never_sent_without_receipt(client, ledger):
    bad = "data:image/png;base64,@@@not-base64@@@"
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(return_value=_ok_chat())
        client.chat("muse-spark-1.3", [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": bad}}]}], run_id="r", agent="a")
    assert route.called
    receipt = ledger.receipts("r")[-1]
    assert receipt.input_sha256 == ["undecodable:" + sha256_hex(bad)]


def test_images_hashed_only_at_known_positions(client, ledger):
    text_with_prefix = "data:image/png;base64,AAAA"  # plain text, not an image part
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=_ok_chat())
        client.chat("muse-spark-1.3", [{"role": "user", "content": [
            {"type": "text", "text": text_with_prefix}, chat_image_part(RED_PNG)]}],
            run_id="r", agent="a")
    assert ledger.receipts("r")[-1].input_sha256 == [sha256_hex(RED_PNG)]


def test_responses_input_image_hashed_by_decoded_bytes(client, ledger):
    body = {"id": "resp_1", "model": "muse-spark-1.3", "status": "completed", "output": [],
            "usage": {"input_tokens": 5, "output_tokens": 1}}
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json=body))
        client.respond("muse-spark-1.3", [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "describe"}, vision_parts(RED_PNG)]}],
            run_id="r", agent="a")
    assert ledger.receipts("r")[-1].input_sha256 == [sha256_hex(RED_PNG)]


def test_unparseable_success_body_still_leaves_receipt(client, ledger):
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json={"choices": "garbage", "usage": []}))
        with pytest.raises(MuseError):
            client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                        agent="a")
    assert ledger.receipts("r")[-1].details["error"] == "bad_response"


@pytest.mark.parametrize("bad", [{"agent": "two words"}, {"run_id": "jan@example.org"},
                                 {"agent": ""}])
def test_bad_caller_ids_fail_before_any_request(ledger, bad):
    c = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    kwargs = {"run_id": "r", "agent": "a", **bad}
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL)
        with pytest.raises(ValueError):
            c.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], **kwargs)
    assert not route.called
    assert ledger.receipts() == []


def test_odd_server_model_string_still_leaves_a_receipt(client, ledger):
    body = cassette("spark_structured")[0]["response"]["json"]
    odd = "Muse Spark <build 7> for jan@example.org"
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json={**body, "model": odd}))
        result = client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}],
                             run_id="r", agent="a")
    receipt = ledger.receipts("r")[-1]
    assert receipt.id == result.receipt_id
    assert receipt.model == "unrecognized"
    assert receipt.details["reported_model_sha256"] == sha256_hex(odd)
    assert receipt.details["requested_model"] == "muse-spark-1.3"
    assert "jan@example.org" not in ledger.export_jsonl()


def test_estimate_is_reserved_before_the_call(ledger):
    b = Budget(0.05)  # muse-spark-1.3: 100k output tokens alone would cost $0.425
    c = MuseClient(FAKE_KEY, ledger=ledger, budget=b)
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL)
        with pytest.raises(BudgetExceeded):
            c.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                   agent="a", max_tokens=100_000)
    assert not route.called
    assert b.spent() == 0.0 and b.remaining() == pytest.approx(0.05)


def test_success_settles_the_reservation_to_the_real_cost(client, ledger):
    with respx.mock() as router:
        mount(router, "spark_structured")
        result = client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}],
                             run_id="r", agent="a", max_tokens=400)
    assert client.budget.spent() == pytest.approx(result.cost_usd)
    assert client.budget.remaining() == pytest.approx(1.0 - result.cost_usd)


def test_bad_response_still_records_its_usage_and_cost(client, ledger):
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(
            200, json={"model": "muse-spark-1.3", "choices": "garbage",
                       "usage": {"prompt_tokens": 1000, "completion_tokens": 2000}}))
        with pytest.raises(MuseError):
            client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                        agent="a", max_tokens=4000)
    receipt = ledger.receipts("r")[-1]
    expected = cost_usd("muse-spark-1.3", Usage(input_tokens=1000, output_tokens=2000))
    assert receipt.details["error"] == "bad_response"
    assert receipt.tokens_in == 1000 and receipt.tokens_out == 2000
    assert receipt.cost_usd == pytest.approx(expected)
    assert client.budget.spent() == pytest.approx(expected)


def test_http_error_releases_the_reservation(client, ledger):
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(
            500, json={"error": {"type": "server_error"}}))
        with pytest.raises(MuseError):
            client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                        agent="a", max_tokens=4000)
    assert client.budget.spent() == 0.0 and client.budget.remaining() == pytest.approx(1.0)


def test_network_error_charges_the_estimate_conservatively(client, ledger):
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(MuseError):
            client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                        agent="a", max_tokens=4000)
    receipt = ledger.receipts("r")[-1]
    assert receipt.details["cost_basis"] == "estimate" and receipt.cost_usd > 0
    assert client.budget.spent() == pytest.approx(receipt.cost_usd)


def test_tool_calls_request_parallel_tool_calls(client, ledger):
    """Spec: the producer runs with parallel_tool_calls=true (Responses and Chat)."""
    tools = [{"type": "function", "name": "lookup_price",
              "description": "Price of a product in PLN.",
              "parameters": {"type": "object", "properties": {"product": {"type": "string"}},
                             "required": ["product"], "additionalProperties": False}}]
    with respx.mock() as router:
        sent, _ = mount(router, "spark_parallel_tools")
        result = client.respond("muse-spark-1.3",
                                "Look up the price of bread and of rolls; call the tool once "
                                "per product.",
                                tools=tools, run_id="r", agent="producer",
                                reasoning={"effort": "low"}, max_output_tokens=300)
    assert sent[0]["parallel_tool_calls"] is True
    # The recorded call shows the API accepts the field (HTTP 200). It spent its 300-token
    # cap on reasoning ("incomplete"), so it is not evidence of parallel calls themselves.
    assert result.finish == "incomplete" and result.response_id
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=cassette("spark_structured")[0]["response"][
                "json"]))
        client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                    agent="a", tools=[{"type": "function", "function": {"name": "f"}}])
        assert json.loads(route.calls[0].request.content)["parallel_tool_calls"] is True
        client.chat("muse-spark-1.3", [{"role": "user", "content": "hi"}], run_id="r",
                    agent="a")
        assert "parallel_tool_calls" not in json.loads(route.calls[1].request.content)


def test_unverified_model_prices_are_not_listed():
    from studio.muse.client import TOKEN_PRICES

    assert not any(m.startswith(("muse-spark-1.1", "muse-spark-1.2")) for m in TOKEN_PRICES)


def test_a_503_is_retried_once_and_both_attempts_have_receipts():
    """Day 4: the API answered 503 mid-run in a real recording; one retry after a pause."""
    from studio.muse import client as mc

    ok = {"id": "x", "model": "muse-spark-1.3", "choices": [{
        "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
    led = Ledger()
    c = mc.MuseClient("test-key-not-a-secret", ledger=led, budget=Budget(1.0))
    c.retry_delay_s = 0.0
    with respx.mock() as router:
        route = router.post(f"{mc.BASE_URL}/chat/completions").mock(side_effect=[
            httpx.Response(503, json={"error": {"type": "server_error"}}),
            httpx.Response(200, json=ok)])
        assert c.chat("muse-spark-1.3", [{"role": "user", "content": "x"}], run_id="r",
                      agent="a").text == "hi"
    assert route.call_count == 2
    statuses = [r.details.get("error_status") for r in led.receipts("r")]
    assert statuses == [503, None]


def test_other_errors_and_a_second_503_are_not_retried():
    from studio.muse import client as mc

    led = Ledger()
    c = mc.MuseClient("test-key-not-a-secret", ledger=led, budget=Budget(1.0))
    c.retry_delay_s = 0.0
    for first, calls in ((400, 1), (503, 2)):
        with respx.mock() as router:
            route = router.post(f"{mc.BASE_URL}/chat/completions").mock(
                return_value=httpx.Response(first, json={"error": {"type": "e"}}))
            with pytest.raises(mc.MuseError):
                c.chat("muse-spark-1.3", [{"role": "user", "content": "x"}], run_id="r",
                       agent="a")
        assert route.call_count == calls
