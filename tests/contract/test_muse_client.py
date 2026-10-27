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
RED_PNG = (ROOT / "fixtures" / "images" / "red-16.png").read_bytes()
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
        for item in data["interactions"]:
            assert set(item["request"]) == {"method", "path", "json"}
            assert set(item["response"]) == {"status", "json"}


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


def test_vision_part_hashed_into_receipt(client, ledger):
    with respx.mock() as router:
        sent, _ = mount(router, "spark_vision")
        result = client.chat(
            "muse-spark-1.3",
            [{"role": "user", "content": [
                {"type": "text", "text": "What colour is this square? One word."},
                chat_image_part(RED_PNG),
            ]}],
            run_id="run-1", agent="reviewer", max_tokens=1500, reasoning_effort="low",
        )
    # Wire contract only: the recorded answer for this 16x16 swatch is not a colour check.
    assert result.text.strip() and result.finish == "stop"
    url = sent[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    receipt = ledger.receipts("run-1")[-1]
    assert receipt.input_sha256 == [sha256_hex(RED_PNG)]


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
