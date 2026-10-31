"""Review fixes, group D (31 Oct): approvals belong to their run's account, a failed run
start frees the run slot, and the other runs/webhook/report fixes."""

import time

import pytest
from fastapi.testclient import TestClient

from studio import db, mandates
from studio.app import create_app
from studio.approvals import Pending
from studio.receipts import canonical
from tests.test_judge_live import PAYLOAD
from tests.test_pixel_gate import RUN, _sign, studio_brief
from tests.test_ui_studio import PASSWORD, csrf, login, register, settings


def _pending(app, action="campaign.activate", *, item="cmp_o1", run_id=RUN, payload=None):
    conn, ledger = app.state.conn, app.state.ledger
    ledger.append(run_id, agent="producer", kind="model_call")
    p = payload or {**PAYLOAD, "action": action}
    pending = Pending.create(action, item, p, ledger=ledger, run_id=run_id)
    with db.transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (pending.id, run_id, canonical(p).decode(),
                                             time.time()))
    return pending.id


def _offline_sign(c, app, key, approver, aid):
    """A signature made without the payload route, over the same message."""
    sig = key.sign(Pending.load(app.state.conn, aid).message(approver)).hex()
    return c.post(f"/api/approvals/{aid}", json={"approver": approver, "signature": sig})


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
        r = _offline_sign(c, app, key, approver, aid)
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
        studio_brief(app.state.conn)
        _, r = _sign(c, key, approver, _pending(app))
        assert r.status_code == 200, r.text


def test_an_approval_without_a_known_owner_is_for_a_full_account_only(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.executors["campaign.activate"] = lambda v, p: {}
    with TestClient(app, base_url="https://testserver") as c:
        login(c)  # the shared judge account
        key, approver = register(c)
        aid = _pending(app)  # no brief, no campaign: nobody's
        assert c.get(f"/api/approvals/{aid}/payload",
                     params={"approver": approver}).status_code == 404
        assert _offline_sign(c, app, key, approver, aid).status_code == 404
        assert app.state.conn.execute("SELECT count(*) FROM nonces").fetchone()[0] == 0
    with TestClient(app, base_url="https://testserver") as c:
        _owner_login(app, c)
        key, approver = register(c)
        _, r = _sign(c, key, approver, aid)
        assert r.status_code == 200, r.text


def test_the_judge_still_activates_a_seeded_campaign(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.executors["campaign.activate"] = lambda v, p: {}
    with db.transaction(app.state.conn):
        app.state.conn.execute(
            "INSERT INTO campaigns (id, run_id, name, objective, daily_budget_minor, adapter, "
            "created_ts) VALUES ('cmp_seed01', '', 'Seed', 'traffic', 500, 'mock', 1)")
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        key, approver = register(c)
        _, r = _sign(c, key, approver, _pending(app, item="cmp_seed01", run_id="run_seed01"))
        assert r.status_code == 200, r.text


def test_another_accounts_mandate_cannot_be_read_renewed_or_signed(tmp_path):
    app = create_app(settings(tmp_path))
    other = app.state.accounts.create("kasia", PASSWORD)
    mid = mandates.new_id()
    grant = {"action": mandates.GRANT, mandates.MANDATE_FIELD: {"account_id": other.id}}
    with TestClient(app, base_url="https://testserver") as c:
        _owner_login(app, c)
        key, approver = register(c)
        aid = _pending(app, mandates.GRANT, item=mid, run_id=mid, payload=grant)
        assert c.get(f"/api/approvals/{aid}/payload",
                     params={"approver": approver}).status_code == 404
        assert c.post(f"/api/approvals/{aid}/renew").status_code == 404
        r = _offline_sign(c, app, key, approver, aid)
        assert r.status_code == 403 and r.json() == {"error": "mandate_not_yours"}


class _NoThread:
    def __init__(self, *a, **kw):
        pass

    def start(self):
        raise RuntimeError("can't start new thread")


def test_a_run_whose_thread_cannot_start_frees_the_run_slot(tmp_path, monkeypatch):
    from studio import routes_studio

    app = create_app(settings(tmp_path))
    app.state.muse = object()  # a model client is there; the run never starts
    monkeypatch.setattr(routes_studio.threading, "Thread", _NoThread)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        login(c)
        assert c.post("/api/runs", json={"brief": "a brief"}).status_code == 500
        assert app.state.active_run is None
        # its committed run is failed, not left running for ever
        assert app.state.conn.execute("SELECT status FROM run_state").fetchall() == \
            [("failed",)]


def test_a_confirmation_that_fails_unexpectedly_frees_the_run_slot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from studio import routes_studio

    app = create_app(settings(tmp_path))
    app.state.muse = object()

    def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(routes_studio, "check_confirmation",
                        lambda *a, **kw: SimpleNamespace(run_id="run_x1"))
    monkeypatch.setattr(routes_studio, "commit_confirmation", boom)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        login(c)
        r = c.post("/api/runs", json={"brief_v1": {}})
        assert r.status_code == 500
        assert app.state.active_run is None


def test_an_mcp_run_whose_clients_fail_is_marked_failed(tmp_path, monkeypatch):
    from studio import demo, replay
    from tests.test_mcp import CLIENT, make_studio

    app, studio, _ = make_studio(tmp_path)
    meta = replay.load_set("brief")
    d = studio.call(CLIENT, "draft_brief", {"text": meta["draft_text"], "lang": "pl"})
    res = demo.resolutions(d["brief"]["open_questions"], meta["answers"])

    def broken():
        raise RuntimeError("no replay set")

    monkeypatch.setattr(studio, "clients", broken)
    out = studio.call(CLIENT, "run_pipeline", {"brief_id": d["brief_id"], "resolutions": res})
    assert out["run_status"] == "failed", out
    assert not studio._run_lock.locked()


def test_the_mcp_account_sees_no_brief_another_accounts_token_drafted(tmp_path):
    from tests.test_mcp import CLIENT, make_studio

    app, studio, _ = make_studio(tmp_path)
    other = app.state.accounts.create("kasia", PASSWORD)
    conn = app.state.conn
    with db.transaction(conn):
        conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts, draft, "
                     "draft_run_id) VALUES ('b_00000000000000f1', 'run_f1', 'x', 'text', 1, "
                     "'{}', 'run_fd1')")
        conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, created_ts) "
                     "VALUES ('b_00000000000000f1', ?, 'tok', 1)", (other.id,))
        conn.execute("INSERT INTO run_state (run_id, status, state, updated_ts) "
                     "VALUES ('run_f1', 'failed', '{}', 1)")
    app.state.ledger.append("run_f1", agent="producer", kind="model_call")
    listed = studio.call(CLIENT, "list_briefs", {})
    assert "b_00000000000000f1" not in str(listed)
    assert studio.call(CLIENT, "read_brief",
                       {"brief_id": "b_00000000000000f1"})["error"] == "not_found"
    assert studio.call(CLIENT, "run_pipeline", {"brief_id": "b_00000000000000f1",
                                                "resolutions": []})["error"] == "unknown_draft"
    assert studio.call(CLIENT, "read_run", {"run_id": "run_f1"})["error"] == "not_found"
    assert studio.call(CLIENT, "request_launch", {"run_id": "run_f1"})["error"] == "not_found"
    assert "run_f1" not in str(studio.call(CLIENT, "list_campaigns", {})["runs"])
    got = studio.call(CLIENT, "read_receipts", {"run_id": "run_f1"})
    assert got["matched"] == 0
