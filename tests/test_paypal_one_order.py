"""One open PayPal order per ad: racing ``create-order`` calls share it (a partial unique
index backs this), ``payer-approved`` binds PayPal's orderID to the approval's order, and a
database from before the index is repaired at start without losing the newest order."""

from __future__ import annotations

import threading

import pytest

from studio import db, funding
from studio.app import create_app
from studio.funding import FundingError
from studio.muse.client import MuseClient
from studio.muse.openweights import OpenWeightsClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, load, transport
from tests.test_ui_studio import login, new_client, settings, wait_for

H = "cd" * 32
Q = funding.quote("GBP", 1000, 3, funding.Decimal(5))


@pytest.fixture
def run(tmp_path):  # a fresh app per test: an approval is consumed by its funding
    a = create_app(settings(tmp_path, PAYPAL_ADAPTER="mock"))
    tr, _ = transport("producer_loop")
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0), transport=tr)
    a.state.muse.unrecorded = frozenset({"sam.check"})
    a.state.open_weights = OpenWeightsClient("", ledger=a.state.ledger)
    with new_client(a) as c:
        login(c)
        r = c.post("/api/runs", json={"brief": load("producer_loop")["brief"]})
        data = wait_for(c, r.json()["run_id"])
    return a, data["state"]["pending_id"]


def orders_of(conn, item):
    return conn.execute("SELECT COUNT(*) FROM funding_moves m JOIN fundings f "
                        "ON f.id = m.funding_id WHERE f.item_id = ? AND m.move = 'order'",
                        (item,)).fetchone()[0]


def test_racing_creates_share_one_funding_and_one_paypal_order(run):
    a, pending_id = run
    with new_client(a) as c:
        login(c)
        barrier = threading.Barrier(2)
        out: list = []

        def go():
            barrier.wait()
            out.append(c.post(f"/api/funding/{pending_id}/order", json={"days": 3}))

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(out) == 2 and all(r.status_code in (200, 201) for r in out), \
            [r.text for r in out]
        order_ids = {r.json()["order_id"] for r in out}
        assert len(order_ids) == 1 and len({r.json()["funding_id"] for r in out}) == 1
        item = out[0].json()["item_id"]
        rows = funding.of_item(a.state.conn, item)
        assert len(rows) == 1 and rows[0].state == "created"
        assert orders_of(a.state.conn, item) == 1
        later = c.post(f"/api/funding/{pending_id}/order", json={})
        assert later.json()["order_id"] == order_ids.pop()


def test_payer_approved_binds_the_order_id(run):
    a, pending_id = run
    with new_client(a) as c:
        login(c)
        o = c.post(f"/api/funding/{pending_id}/order", json={}).json()
        for wrong in ("NOSUCHORDER", "", 7):
            r = c.post(f"/api/funding/{pending_id}/payer-approved", json={"order_id": wrong})
            assert r.status_code == 409 and r.json() == {"error": "order_mismatch"}
        assert funding.get(a.state.conn, o["funding_id"]).state == "created"
        a.state.funder.fake.approve(o["order_id"])  # the buyer approves in PayPal
        r = c.post(f"/api/funding/{pending_id}/payer-approved",
                   json={"order_id": o["order_id"]})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "payer_approved" and r.json()["order_id"] == o["order_id"]


def test_payer_approved_without_an_id_works_as_before(run):
    a, pending_id = run
    with new_client(a) as c:
        login(c)
        o = c.post(f"/api/funding/{pending_id}/order", json={}).json()
        a.state.funder.fake.approve(o["order_id"])
        r = c.post(f"/api/funding/{pending_id}/payer-approved")  # no body at all
        assert r.status_code == 200, r.text
        assert r.json()["order_id"] == o["order_id"]


def test_mock_approve_needs_no_order_id(run):
    a, pending_id = run
    with new_client(a) as c:
        login(c)
        o = c.post(f"/api/funding/{pending_id}/order", json={}).json()
        r = c.post(f"/api/funding/{pending_id}/mock-approve")
        assert r.status_code == 200, r.text
        assert r.json()["order_id"] == o["order_id"]


# -- the store ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    yield c
    c.close()


def test_create_returns_the_open_funding_and_the_index_refuses_a_second(conn):
    first = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256=H, q=Q)
    again = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256=H, q=Q)
    assert again.id == first.id
    other = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256="ef" * 32, q=Q)
    assert other.id != first.id  # another ad hash is another funding
    funding.advance(conn, first.id, "expired", expect="created")
    fresh = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256=H, q=Q)
    assert fresh.id != first.id  # an ended funding no longer blocks a new one
    with pytest.raises(Exception) as e:  # the index itself, bypassing create()
        conn.execute("INSERT INTO fundings (id, run_id, item_id, content_sha256, currency, "
                     "daily_budget_minor, days, budget_minor, fee_pct, fee_minor, total_minor,"
                     " state, created_ts, updated_ts) VALUES ('fund_x', 'r', 'cre_1', ?, "
                     "'GBP', 1, 1, 1, '5', 0, 1, 'payer_approved', 1, 1)", (H,))
    assert "UNIQUE" in str(e.value)


def test_a_database_with_duplicate_open_fundings_is_repaired_at_start(conn):
    conn.execute("DROP INDEX fundings_one_open")  # the database as it was before the index
    for i, (state, ts) in enumerate([("created", 1.0), ("payer_approved", 2.0),
                                     ("created", 3.0), ("captured", 0.5)]):
        paid = int(state == "captured")
        conn.execute(
            "INSERT INTO fundings (id, run_id, item_id, content_sha256, currency, "
            "daily_budget_minor, days, budget_minor, fee_pct, fee_minor, total_minor, state, "
            "authorized_minor, captured_minor, created_ts, updated_ts) "
            "VALUES (?, 'r', 'cre_1', ?, 'GBP', 1, 1, 1, '5', 0, 1, ?, ?, ?, ?, ?)",
            (f"fund_{i}", H, state, paid, paid, ts, ts))
    funding.ensure_schema(conn)  # must not raise
    states = dict(conn.execute("SELECT id, state FROM fundings").fetchall())
    assert states == {"fund_0": "expired", "fund_1": "expired", "fund_2": "created",
                      "fund_3": "captured"}
    funding.ensure_schema(conn)  # idempotent
    with pytest.raises(FundingError):
        funding.get(conn, "fund_nope")
