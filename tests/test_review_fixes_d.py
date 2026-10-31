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


# -- WhatsApp webhook (routes_whatsapp, whatsapp.Inbox) ------------------------------------


def test_a_chunked_whatsapp_body_over_the_cap_is_refused(tmp_path):
    from studio import whatsapp
    from tests.test_whatsapp import make

    app = make(tmp_path)

    def chunks():
        for _ in range(3):
            yield b"x" * whatsapp.MAX_WEBHOOK_BODY  # no Content-Length: streamed

    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/webhooks/whatsapp", content=chunks(),
                   headers={"content-type": "application/json"})
        assert r.status_code == 413


def test_the_sender_limiter_forgets_idle_senders():
    from studio import whatsapp

    now = [1000.0]
    lim = whatsapp.SenderLimiter(clock=lambda: now[0])
    for i in range(whatsapp.SenderLimiter.MAX_SENDERS + 50):
        assert lim.allow(f"s{i}")
    assert len(lim._seen) <= whatsapp.SenderLimiter.MAX_SENDERS
    now[0] += whatsapp.RATE_WINDOW_S + 1
    lim.allow("fresh")
    assert len(lim._seen) == 1  # every idle sender's window has closed


def test_a_linked_owners_text_with_a_code_like_string_is_a_brief(tmp_path):
    from tests.test_whatsapp import deliver, event, make, text, wa_receipts

    app = make(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        r = deliver(c, event(text("Promo for the weekend, code MUSE-123456 at checkout")))
        assert r.json()["queued"] == 1
    app.state.whatsapp_inbox.flush()
    statuses = [r.details.get("status") for r in wa_receipts(app)]
    assert "verification_failed" not in statuses and "accepted" in statuses


def test_the_lead_webhook_under_read_only_asks_meta_to_retry_and_stores_nothing(tmp_path):
    from studio import leadads
    from tests.attack.test_lead_ads import RUN as LEAD_RUN
    from tests.attack.test_lead_ads import SECRET, _event
    from tests.attack.test_lead_ads import _sign as hub_sign

    app = create_app(settings(tmp_path, META_APP_SECRET=SECRET, STUDIO_READ_ONLY="1"))
    form_id = "mock-form-0a1b2c3d4e"
    app.state.adapter.objects[form_id] = {"type": "lead_form"}
    leadads.record_form(app.state.conn, form_id, "cmp_hook1", LEAD_RUN, app.state.adapter.name)
    body = _event(form_id, f"lead-demo-2-{form_id[-6:]}")
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/api/meta/webhook", content=body,
                   headers={"X-Hub-Signature-256": hub_sign(body)})
        assert r.status_code == 503 and r.headers.get("retry-after")
        # an unsigned request is still refused first
        assert c.post("/api/meta/webhook", content=body).status_code == 403
    assert app.state.conn.execute("SELECT count(*) FROM leads WHERE external_id != ''"
                                  ).fetchone()[0] == 0


def test_the_report_reads_its_inputs_off_the_event_loop(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from studio.agents import analyst
    from studio.muse.client import MuseClient
    from studio.settings import Budget
    from tests.cassettes import FAKE_KEY
    from tests.test_analyst import REPORT, _fake

    app = create_app(settings(tmp_path))
    app.state.muse = MuseClient(FAKE_KEY, ledger=app.state.ledger, budget=Budget(1.0),
                                transport=httpx.MockTransport(
                                    lambda req: httpx.Response(200, json=_fake(REPORT))))
    on_loop = []

    def watch(name, fn):
        def wrapped(*a, **kw):
            try:
                asyncio.get_running_loop()
                on_loop.append(name)
            except RuntimeError:
                pass  # a worker thread: no running loop
            return fn(*a, **kw)
        return wrapped

    ledger = app.state.ledger
    monkeypatch.setattr(ledger, "export_jsonl", watch("export", ledger.export_jsonl))
    monkeypatch.setattr(analyst, "inbox_summary", watch("inbox", analyst.inbox_summary))
    monkeypatch.setattr(analyst, "save", watch("save", analyst.save))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        r = c.post("/api/reports", json={})
        assert r.status_code == 200, r.text
    assert on_loop == []


@pytest.mark.parametrize("path", ["Caddyfile", "deploy/Caddyfile"])
def test_caddy_allows_the_microphone_and_the_leads_import_body(path):
    import re
    from pathlib import Path

    from studio import routes_export

    text = (Path(__file__).resolve().parents[1] / path).read_text()
    policy = re.search(r'Permissions-Policy "([^"]*)"', text).group(1)
    assert "microphone=(self)" in policy and "camera=()" in policy  # voice brief, Dictate
    small = re.search(r"@small not path ([^\n]*)", text).group(1).split()
    assert "/leads/import" in small  # not capped at 128 KB
    m = re.search(r"request_body @import \{\s*max_size (\d+)MB", text)
    assert m and int(m.group(1)) * 1_000_000 >= routes_export.IMPORT_BODY_LIMIT
    assert "@import path /leads/import" in text
