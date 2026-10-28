"""Day 2 fix round, I2: a handler that crashes still leaves a receipt and an output."""

import json

import httpx
import respx

from studio import db
from studio.agents import art_director, producer
from studio.agents.dispatcher import Dispatcher
from studio.muse.client import BASE_URL, MuseClient, MuseError, ToolCall
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PLAN = {"audience": "a", "angles": [{"name": "n", "hook": "h", "persona": "p"}] * 3,
        "budget_pln_daily": 30, "placements": ["feed_1x1"], "tasks": ["t"]}
COPY = {"variants": [{"primary_text": "t", "headline": "Warm mug", "description": "d",
                      "cta": "SHOP_NOW"}] * 3, "risks": []}


def _boom(args):
    raise RuntimeError("upstream said: call anna@example.com at 600 123 456")


def test_unexpected_exception_becomes_an_error_receipt_and_output():
    ledger = Ledger()
    d = Dispatcher({"run_copywriter": _boom}, ledger=ledger, run_id="run-i2")
    out = d.execute(ToolCall("c1", "run_copywriter", '{"angle_index": 0}'))
    assert out.status == "error"
    assert out.output == {"error": "tool failed", "error_type": "RuntimeError"}
    (r,) = ledger.receipts("run-i2")
    assert r.kind == "tool_call" and r.details["status"] == "error"
    assert r.details["error_type"] == "RuntimeError" and r.details["reason"] == "tool failed"
    exported = ledger.export_jsonl()
    assert "anna@" not in exported and "600 123 456" not in exported


def test_muse_error_code_is_reported_without_its_message():
    def fail(args):
        raise MuseError("server said something long", code="rate_limited")

    d = Dispatcher({"run_copywriter": fail}, ledger=Ledger(), run_id="run-i2b")
    out = d.execute(ToolCall("c1", "run_copywriter", '{"angle_index": 0}'))
    assert out.output == {"error": "tool failed", "error_type": "MuseError",
                          "code": "rate_limited"}


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


def test_a_crashing_tool_does_not_end_the_run_and_retries_are_bounded(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    ctx = producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                           concept="mug", data_dir=tmp_path / "var")
    tries = []

    def broken(*a, **kw):
        tries.append(1)
        raise OSError("disk full")

    monkeypatch.setattr(art_director, "run", broken)
    art = ("run_art_director", '{"headline": "Warm mug"}')
    bodies = [_chat(PLAN), _turn("r1", [("run_copywriter", '{"angle_index": 0}'), art]),
              _chat(COPY), _turn("r2", [art]), _turn("r3", [art]),
              _turn("r4", text="Images failed.")]
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            side_effect=lambda request: httpx.Response(200, json=bodies.pop(0)))
        result = producer.run(ctx, "brief", run_id="run-i2c")
    assert result.status == "done" and len(tries) == 2  # third retry hit the tool limit
    last = [json.loads(i["output"]) for i in json.loads(route.calls[-1].request.content)["input"]]
    assert "limit" in last[0]["error"]
    statuses = [r.details["status"] for r in ledger.receipts("run-i2c")
                if r.details.get("tool") == "run_art_director"]
    assert statuses == ["error", "error", "rejected"]
