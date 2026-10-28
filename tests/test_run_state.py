"""Day 2 fix round, I4: a signed run never drops back from ``approved``.

The human can sign while the producer's last turn is still running; the producer's final
state write (or a failure write) must not move ``approved`` back to ``awaiting_approval``.
"""

import json
from pathlib import Path

from studio import db
from studio.agents import producer

UI = Path(__file__).resolve().parents[1] / "studio" / "ui"


def _conn(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    return conn


def _status(conn, run_id):
    return conn.execute("SELECT status, state FROM run_state WHERE run_id = ?",
                        (run_id,)).fetchone()


def _approve(conn, run_id):
    with db.transaction(conn):
        conn.execute("UPDATE run_state SET status = 'approved' WHERE run_id = ?", (run_id,))


def test_final_state_write_keeps_approved(tmp_path):
    conn = _conn(tmp_path)
    run = producer.RunResult(run_id="run-i4", status="running")
    producer._save_state(conn, run, "running")
    _approve(conn, "run-i4")  # signed during the last turn
    run.summary = "done"
    producer._save_state(conn, run, "awaiting_approval")
    status, state = _status(conn, "run-i4")
    assert status == "approved" and json.loads(state)["summary"] == "done"


def test_failure_write_keeps_approved(tmp_path):
    conn = _conn(tmp_path)
    run = producer.RunResult(run_id="run-i4b", status="running")
    producer._save_state(conn, run, "running")
    _approve(conn, "run-i4b")
    producer.mark_failed(conn, "run-i4b", "RuntimeError")
    assert _status(conn, "run-i4b")[0] == "approved"
    producer.mark_failed(conn, "run-i4c", "RuntimeError")  # no row yet
    assert _status(conn, "run-i4c")[0] == "failed"


def test_other_statuses_still_move(tmp_path):
    conn = _conn(tmp_path)
    run = producer.RunResult(run_id="run-i4d", status="running")
    producer._save_state(conn, run, "running")
    producer._save_state(conn, run, "awaiting_approval")
    assert _status(conn, "run-i4d")[0] == "awaiting_approval"


def test_approval_panel_opens_only_when_the_run_awaits_approval():
    js = (UI / "static" / "app.js").read_text()
    assert 'st === "awaiting_approval"' in js and 'st !== "approved"' not in js
