"""The reviewer -> rewrite loop (no recorded set: the live copywriter never wrote a copy the
reviewers blocked, so the loop is tested with fakes)."""

import re
from pathlib import Path

import pytest

from studio import db
from studio.agents import copywriter, producer
from studio.agents.dispatcher import CopywriterArgs
from studio.agents.schemas import Angle, CopyPack, CopyVariant, Plan
from studio.muse.client import MuseClient
from studio.receipts import Receipt
from studio.routes_receipts import loop_marks
from studio.settings import Budget
from studio.ui import i18n
from tests.cassettes import FAKE_KEY

UI = Path(__file__).resolve().parents[1] / "studio" / "ui"
FINDING = {"rule": "P8", "severity": "block", "evidence": "cures migraine"}
REVIEW = {"verdict": "BLOCK", "variant_index": 1, "ab": [],
          "formats": [{"primary": {"findings": [FINDING]}, "second": None}]}


def variant(text):
    return CopyVariant(primary_text=text, headline=text[:20], description="d", cta="SHOP_NOW")


def pack(text):
    return CopyPack(variants=[variant(text + str(i)) for i in range(3)], risks=[])


@pytest.fixture
def setup(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    ctx = producer.Context(client=MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(5.0)),
                           open_client=None, conn=conn, photo_png=b"", concept="mug",
                           data_dir=tmp_path / "var")
    run = producer.RunResult(run_id="run_x", status="running")
    angle = Angle(name="n", hook="h", persona="p")
    run.plan = Plan(audience="a", angles=[angle] * 3, budget_daily=30,
                    placements=["feed_1x1"], tasks=["t"])
    run.copy = pack("blocked claim ")
    seen = []

    def fake_write(client, angle, persona, **kw):
        seen.append(kw.get("blocked"))
        return pack("clean text "), None

    monkeypatch.setattr(copywriter, "write", fake_write)
    return producer._handlers(ctx, run, "brief"), run, seen


def test_a_block_review_makes_the_rewrite_keep_before_and_send_the_findings(setup):
    handlers, run, seen = setup
    run.review = dict(REVIEW)
    handlers["run_copywriter"](CopywriterArgs(angle_index=0))
    assert seen == [[{"rule": "P8", "evidence": "cures migraine"}]]
    rw = run.state()["rewrites"]
    assert len(rw) == 1 and rw[0]["variant_index"] == 1
    assert rw[0]["before"]["primary_text"] == "blocked claim 1"
    assert rw[0]["after"] is None and rw[0]["after_verdict"] == ""


def test_a_second_rewrite_is_refused(setup):
    handlers, run, seen = setup
    run.review = dict(REVIEW)
    handlers["run_copywriter"](CopywriterArgs(angle_index=0))
    run.review = dict(REVIEW)  # blocked again
    with pytest.raises(Exception, match="already rewritten"):
        handlers["run_copywriter"](CopywriterArgs(angle_index=0))
    assert len(run.rewrites) == 1


def test_copy_without_a_block_is_not_a_rewrite(setup):
    handlers, run, seen = setup
    handlers["run_copywriter"](CopywriterArgs(angle_index=0))
    assert seen == [None] and run.rewrites == []


def test_blocked_findings_reach_the_copywriter_prompt():
    angle = Angle(name="a", hook="h", persona="p")
    msg = copywriter._user_message(angle, "p", "b", blocked=[{"rule": "P8", "evidence": "x"}])
    assert copywriter.REWRITE_RULE in msg and "- P8: x" in msg
    assert copywriter.REWRITE_RULE not in copywriter._user_message(angle, "p", "b")


def test_blocking_findings_keep_only_block_severity():
    review = {"formats": [{"primary": {"findings": [
        FINDING, {"rule": "P4", "severity": "warn", "evidence": "y"}]}, "second": None}]}
    assert producer.blocking_findings(review) == [{"rule": "P8", "evidence": "cures migraine"}]


def test_receipts_label_the_loop():
    def r(i, kind, agent, **details):
        return Receipt(id=f"r{i}", prev="", ts=i, run_id="run_x", agent=agent, kind=kind,
                       details=details)
    marks = loop_marks([r(1, "review", "reviewer", verdict="BLOCK", rules=["P8"]),
                        r(2, "model_call", "copywriter"),
                        r(3, "review", "reviewer", verdict="WARN")])
    assert marks["r1"]["mark"] == "blocked" and marks["r1"]["rules"] == "P8"
    assert marks["r2"]["mark"] == "rewrite"
    assert marks["r3"] == {"mark": "after", "verdict": "WARN"}


def test_loop_strings_have_parity_and_the_ui_wraps_on_a_phone():
    for key in ("js.loop_banner", "js.loop_banner_wait", "js.loop_before", "js.loop_after",
                "js.loop_finding", "studio.loop", "loop.mark_blocked", "loop.mark_rewrite",
                "loop.mark_after"):
        assert key in i18n.EN and key in i18n.PL
        assert i18n.placeholders(i18n.EN[key]) == i18n.placeholders(i18n.PL[key])
    css = (UI / "static" / "components.css").read_text()
    assert re.search(r"\.loop-pair\s*\{[^}]*flex-wrap:\s*wrap", css)
    assert 'id="loop-panel"' in (UI / "templates" / "studio.html").read_text()
    assert "renderLoop(state.rewrites)" in (UI / "static" / "app.js").read_text()
