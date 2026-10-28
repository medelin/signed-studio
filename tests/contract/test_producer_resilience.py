"""Day 4 (real recordings): the API can fail the producer's last turn — a 503, or "referenced
response not found or expired" for the previous response id. Once the approval was
requested the run is complete; the summary turn is not worth failing it."""

import httpx
import pytest

from studio import db
from studio.agents import producer
from studio.muse.client import MuseClient
from studio.muse.openweights import OpenWeightsClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT, interactions, load

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
NOT_FOUND = {"error": {"code": None, "message": "referenced response not found or expired",
                       "param": None, "type": "invalid_request_error"}}


def _run(tmp_path, failures):
    """Replay ``producer_loop``; the last producer turn first fails ``failures`` times."""
    items = interactions("producer_loop")
    last = len(items) - 1
    state = {"i": 0, "failed": 0}

    def handler(request):
        i = state["i"]
        if i == last and state["failed"] < failures:
            state["failed"] += 1
            return httpx.Response(400, json=NOT_FOUND)
        state["i"] += 1
        r = items[i]["response"]
        if "sse" in r:
            from tests.cassettes import sse_body
            return httpx.Response(r["status"], content=sse_body(r["sse"]),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(r["status"], json=r["json"])

    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0),
                        transport=httpx.MockTransport(handler))
    ctx = producer.Context(client=client, open_client=OpenWeightsClient("", ledger=ledger),
                           conn=conn, photo_png=PHOTO, concept="mug", data_dir=tmp_path / "v")
    producer.TURN_RETRY_DELAY_S = 0.0
    result = producer.run(ctx, load("producer_loop")["brief"], run_id="run-res")
    status = conn.execute("SELECT status FROM run_state WHERE run_id = 'run-res'").fetchone()[0]
    return result, status, ledger


@pytest.fixture(autouse=True)
def _restore():
    old = producer.TURN_RETRY_DELAY_S
    yield
    producer.TURN_RETRY_DELAY_S = old


def test_an_expired_previous_response_is_retried_once(tmp_path):
    result, status, ledger = _run(tmp_path, failures=1)
    assert status == "awaiting_approval" and result.summary
    errors = [r for r in ledger.receipts("run-res") if r.details.get("error_status") == 400]
    assert len(errors) == 1


def test_a_failed_summary_after_the_approval_request_keeps_the_run(tmp_path):
    result, status, _ = _run(tmp_path, failures=5)
    assert status == "awaiting_approval" and result.pending_id
    assert "summary unavailable" in result.summary
