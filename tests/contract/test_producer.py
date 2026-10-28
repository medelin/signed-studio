"""Producer loop (D2-7) replayed from the recorded end-to-end run on the demo brief."""

import json

import pytest
import respx

from studio import db
from studio.agents import producer
from studio.approvals import Pending, payload_sha256
from studio.muse.client import BASE_URL, MuseClient
from studio.muse.openweights import OpenWeightsClient
from studio.receipts import verify
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT, load, mount

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("producer")
    conn = db.connect(tmp / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    events = []
    ctx = producer.Context(client=client, open_client=OpenWeightsClient("", ledger=ledger),
                           conn=conn, photo_png=PHOTO, concept="mug", data_dir=tmp / "var",
                           on_event=lambda kind, data: events.append(kind))
    with respx.mock() as router:
        sent = mount(router, "producer_loop", BASE_URL)
        result = producer.run(ctx, load("producer_loop")["brief"], run_id="run-prod")
    return result, sent, ledger, conn, events, tmp


def test_one_brief_to_pending_approval(replay):
    result, _, _, conn, events, _ = replay
    assert result.status == "awaiting_approval" and result.pending_id
    assert len(result.plan.angles) == 3 and len(result.copy.variants) == 3
    assert len(result.creatives.images) == 3
    assert result.review["verdict"] in ("OK", "WARN")
    assert result.review["second_opinion"] == "unavailable"  # no Baseten key in the replay
    reviewed = [(f["placement"], f["sha256"]) for f in result.review["formats"]]
    assert reviewed == [(i.placement, i.sha256) for i in result.creatives.images]
    assert events == ["plan", "copy", "creatives", "review", "approval", "finished"]
    status, state = conn.execute("SELECT status, state FROM run_state WHERE run_id = ?",
                                 ("run-prod",)).fetchone()
    assert status == "awaiting_approval" and json.loads(state)["pending_id"] == result.pending_id


def test_loop_requests_tools_in_parallel_and_bounded(replay):
    _, sent, _, _, _, _ = replay
    loop = [b for b in sent if b.get("tools") and b["model"] == "muse-spark-1.3"]
    assert loop and all(b["parallel_tool_calls"] is True for b in loop)
    assert all(b["reasoning"] == {"effort": "medium", "summary": "auto"} for b in loop)
    names = {t["name"] for t in loop[0]["tools"]}
    assert names == {"run_copywriter", "run_art_director", "run_reviewers", "request_approval"}
    assert len(loop) <= producer.MAX_STEPS


def test_everything_is_a_receipt_and_the_chain_verifies(replay, tmp_path):
    result, _, ledger, _, _, _ = replay
    receipts = ledger.receipts("run-prod")
    kinds = [r.kind for r in receipts]
    assert "external_write" not in kinds and "approval" not in kinds  # nothing signed yet
    tools = [r.details["tool"] for r in receipts if r.agent == "dispatcher"]
    assert tools[:2] == ["run_copywriter", "run_art_director"]  # one parallel turn
    assert "request_approval" in tools and "review" in kinds
    assert any(r.model == "sam-3.1" for r in receipts)
    assert sum(r.model == "muse-image-1.0" for r in receipts) == 3
    assert result.cost_usd == pytest.approx(sum(r.cost_usd for r in receipts))
    path = tmp_path / "receipts.jsonl"
    path.write_text(ledger.export_jsonl())
    report = verify(path, ledger.public_key)
    assert report.ok, report.errors


def test_pending_binds_the_code_built_payload(replay):
    result, _, ledger, conn, _, _ = replay
    pending = Pending.load(conn, result.pending_id)
    assert pending.action == "campaign.create" and pending.run_id == "run-prod"
    (payload,) = conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                              (pending.id,)).fetchone()
    data = json.loads(payload)
    assert payload_sha256(data) == pending.content_sha256
    assert data["campaign"]["status"] == "PAUSED"
    assert data["campaign"]["daily_budget_minor"] == result.plan.budget_pln_daily * 100
    assert [i["sha256"] for i in data["images"]] == [i.sha256 for i in result.creatives.images]
    status = conn.execute("SELECT status FROM approvals WHERE id = ?", (pending.id,)).fetchone()
    assert status == ("pending",)
