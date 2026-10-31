"""Review fixes, group D (31 Oct): approvals belong to their run's account, a failed run
start frees the run slot, and the other runs/webhook/report fixes."""

import time

import pytest
from fastapi.testclient import TestClient

from studio import db
from studio.app import create_app
from studio.approvals import Pending
from studio.receipts import canonical
from tests.test_judge_live import PAYLOAD
from tests.test_pixel_gate import RUN, _sign
from tests.test_ui_studio import PASSWORD, csrf, login, register, settings


def _pending(app, action="campaign.activate"):
    conn, ledger = app.state.conn, app.state.ledger
    ledger.append(RUN, agent="producer", kind="model_call")
    p = {**PAYLOAD, "action": action}
    pending = Pending.create(action, "cmp_o1", p, ledger=ledger, run_id=RUN)
    with db.transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (pending.id, RUN, canonical(p).decode(),
                                             time.time()))
    return pending.id


def _owner_login(app, c, name="ola"):
    account = app.state.accounts.create(name, PASSWORD)
    r = c.post("/login", data={"username": name, "password": PASSWORD, "csrf": csrf(c)})
    assert r.status_code == 200
    return account


def _brief_of(app, *, channel="", account_id="", api_account=None):
    conn = app.state.conn
    with db.transaction(conn):
        conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts, channel, "
                     "account_id) VALUES ('b_00000000000000a1', ?, 'x', 'text', ?, ?, ?)",
                     (RUN, time.time(), channel, account_id))
        if api_account is not None:
            conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, created_ts) "
                         "VALUES ('b_00000000000000a1', ?, 'tok', ?)",
                         (api_account, time.time()))


@pytest.mark.parametrize("kind", ["whatsapp", "api"])
def test_another_accounts_approval_cannot_be_read_or_signed(tmp_path, kind):
    app = create_app(settings(tmp_path))
    ran = []
    app.state.executors["campaign.activate"] = lambda v, p: ran.append(v) or {}
    other = app.state.accounts.create("kasia", PASSWORD)
    if kind == "whatsapp":
        _brief_of(app, channel="whatsapp", account_id=other.id)
    else:
        _brief_of(app, api_account=other.id)
    with TestClient(app, base_url="https://testserver") as c:
        _owner_login(app, c)
        key, approver = register(c)
        aid = _pending(app)
        r = c.get(f"/api/approvals/{aid}/payload", params={"approver": approver})
        assert r.status_code == 404
        # a signature made off-line over the same message is refused before any nonce
        pending = Pending.load(app.state.conn, aid)
        sig = key.sign(pending.message(approver)).hex()
        r = c.post(f"/api/approvals/{aid}", json={"approver": approver, "signature": sig})
        assert r.status_code == 404 and r.json() == {"error": "not_found"}
        assert app.state.conn.execute("SELECT count(*) FROM nonces").fetchone()[0] == 0
        assert c.post(f"/api/approvals/{aid}/renew").status_code == 404
        assert ran == []


def test_the_owning_account_reads_and_signs_its_approval(tmp_path):
    app = create_app(settings(tmp_path))
    ran = []
    app.state.executors["campaign.activate"] = lambda v, p: ran.append(v) or {}
    with TestClient(app, base_url="https://testserver") as c:
        me = _owner_login(app, c)
        _brief_of(app, channel="whatsapp", account_id=me.id, api_account=me.id)
        key, approver = register(c)
        aid = _pending(app)
        _, r = _sign(c, key, approver, aid)
        assert r.status_code == 200, r.text
        assert len(ran) == 1


def test_a_studio_run_stays_signable_by_the_judge(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        key, approver = register(c)
        _brief_of(app)
        _, r = _sign(c, key, approver, _pending(app))
        assert r.status_code == 200, r.text
