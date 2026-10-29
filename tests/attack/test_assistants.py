"""``/api/v1`` and ``/mcp`` as the attacker's way in: no token, a read token that tries to
draft, another account's token, a revoked or expired one, a session cookie instead of a
token, invented signing endpoints and tools, a flood of calls.

Every function that signs, consumes an approval, executes, writes to Meta or Google Ads,
changes a budget or touches an account is a tripwire for the module: it records the hit
and raises. The tests fail on any hit and on any approval that leaves ``pending``."""

import logging
import re

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from studio import approvals, assistants, executor, routes_studio
from studio.accounts import Accounts
from studio.agents import community, google_buyer, media_buyer
from studio.meta.mock import MockAdapter
from studio.sandbox import Window
from tests.test_assistants import (
    BASE,
    OTHER_PASSWORD,
    account,
    bearer,
    draft_confirm_run,
    login,
    make_app,
    token,
)

META_WRITES = ("activate", "create_ad_paused", "create_adset_paused", "create_campaign_paused",
               "create_creative", "hide", "pause", "reply", "update_budget", "upload_image")
TRIPWIRES = (
    [(approvals, "verify_and_consume"), (routes_studio, "verify_and_consume"),
     (approvals, "renew"), (routes_studio, "renew_approval"), (approvals.Keys, "register"),
     (executor, "authorize"), (executor, "record_write"), (executor, "record_step"),
     (media_buyer, "create"), (media_buyer, "activate"), (media_buyer, "update"),
     (media_buyer, "request_activation"), (media_buyer, "request_update"),
     (community, "send"), (google_buyer, "create"), (google_buyer, "activate"),
     (google_buyer, "request_activation")]
    + [(MockAdapter, n) for n in META_WRITES]
    + [(Accounts, n) for n in ("set_status", "reset_password", "update_profile",
                               "change_password", "totp_begin", "totp_disable", "delete")])

GET_PATHS = ("/me", "/briefs", "/briefs/b_0000000000000000", "/runs/run_x",
             "/runs/run_x/assets/feed_1x1.png", "/approvals", "/campaigns", "/insights",
             "/leads", "/comments")
POSTS = (("/briefs", {"text": "Mugs", "lang": "en"}),
         ("/briefs/b_0000000000000000/confirm", {"resolutions": []}),
         ("/runs", {"brief_id": "b_0000000000000000"}),
         ("/comments/c-1/reply", {"message": "hi"}))
DRAFT_OPS = {n for n, o in assistants.OPS.items() if o.scope == "draft"}


@pytest.fixture(scope="module")
def attacked(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("assist-attack"))
    hits: list[str] = []
    mp = pytest.MonkeyPatch()
    for owner, name in TRIPWIRES:
        label = f"{getattr(owner, '__name__', owner)}.{name}"

        def tripwire(*_a, _label=label, **_k):
            hits.append(_label)
            raise AssertionError(f"assistant reached {_label}")

        mp.setattr(owner, name, tripwire)
    mp.setattr(httpx.HTTPTransport, "handle_request",
               lambda self, request: hits.append(f"network {request.url}"))
    tok, value = token(app)
    c = TestClient(app, base_url=BASE)
    d, started = draft_confirm_run(c, bearer(value))
    try:
        yield app, c, hits, tok, value, d, started
    finally:
        mp.undo()


def _statuses(app):
    return dict(app.state.conn.execute("SELECT id, status FROM approvals").fetchall())


def test_no_token_is_401_everywhere(attacked):
    app, c, hits, *_ = attacked
    for path in GET_PATHS:
        r = c.get("/api/v1" + path)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"].startswith("Bearer")
    for path, body in POSTS:
        assert c.post("/api/v1" + path, json=body).status_code == 401, path
    for bad in ("Bearer ", "Bearer msl_tok_000000000000_" + "A" * 43, "Basic abc", "msl_x"):
        assert c.get("/api/v1/me", headers={"Authorization": bad}).status_code == 401
    assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                  ).status_code == 401
    assert c.get("/mcp").status_code == 401
    assert hits == []


def test_a_forged_secret_for_a_real_token_id_is_refused(attacked):
    app, c, hits, tok, value, *_ = attacked
    forged = value[:-1] + ("A" if value[-1] != "A" else "B")
    assert c.get("/api/v1/me", headers=bearer(forged)).status_code == 401


def test_session_cookies_never_authorize_the_api_or_mcp(attacked):
    app, c, hits, *_ = attacked
    with TestClient(app, base_url=BASE) as browser:
        login(browser, app)
        assert browser.get("/account").status_code == 200  # the session is live
        assert browser.get("/api/v1/me").status_code == 401
        assert browser.get("/api/v1/approvals").status_code == 401
        assert browser.post("/api/v1/briefs", json={"text": "x"}).status_code == 401
        assert browser.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/list"}).status_code == 401


def test_a_read_token_cannot_draft_over_http_or_mcp(attacked):
    app, c, hits, tok, value, d, started = attacked
    _, read_value = token(app, scope="read", label="reader")
    h = bearer(read_value)
    for path, body in POSTS:
        r = c.post("/api/v1" + path, json=body, headers=h)
        assert r.status_code == 403 and r.json()["error"] == "insufficient_scope", path
    r = c.post("/api/v1/briefs/voice", content=b"RIFF", headers={**h,
                                                                  "Content-Type": "audio/wav"})
    assert r.status_code == 403
    read_tok = app.state.assistants.tokens.verify(read_value)
    principal = assistants.Principal(read_tok, "mcp", BASE)
    for name in DRAFT_OPS:
        status, out = app.state.assistants.call(principal, name, {})
        assert status == 403 and out["error"] == "insufficient_scope", name
    assert c.get("/api/v1/approvals", headers=h).status_code == 200  # reading is fine
    receipts = app.state.ledger.receipts("api_" + read_tok.id)
    assert {r.details["status"] for r in receipts if r.details["tool"] == "api.draft_brief"
            } == {"refused"}


def test_no_endpoint_or_tool_signs_consumes_executes_or_changes_a_budget(attacked):
    app, c, hits, tok, value, d, started = attacked
    before = _statuses(app)
    h = bearer(value)
    pending = c.get("/api/v1/approvals", headers=h).json()["pending"]
    aid = pending[0]["approval_id"]
    invented = ([f"/approvals/{aid}", f"/approvals/{aid}/sign", f"/approvals/{aid}/approve",
                 f"/runs/{started['run_id']}/approve", f"/runs/{started['run_id']}/launch",
                 "/campaigns/cmp-demo-1/activate", "/campaigns/cmp-demo-1/budget",
                 "/campaigns/cmp-demo-1", "/recommendations/rc_000000000000/approve",
                 "/comments/c-1/hide", "/keys", "/tokens"])
    for path in invented:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            r = c.request(method, "/api/v1" + path, json={"approval_id": aid,
                                                          "signature": "ab" * 64}, headers=h)
            assert r.status_code in (404, 405), (method, path, r.status_code)
    # the web UI's signing and write routes do not take a bearer token
    for path in (f"/api/approvals/{aid}", f"/api/approvals/{aid}/renew",
                 "/api/campaigns/cmp-demo-1/activate", "/api/keys",
                 "/api/recommendations/rc_000000000000/approve"):
        r = c.post(path, json={"signature": "ab" * 64}, headers=h)
        assert r.status_code in (401, 403), (path, r.status_code)
    principal = assistants.Principal(tok, "mcp", BASE)
    svc = app.state.assistants
    for name in ("approve", "sign_approval", "activate_campaign", "update_budget",
                 "post_reply", "hide_comment", "consume_approval", "execute", "set_status"):
        status, out = svc.call(principal, name, {"approval_id": aid})
        assert status == 404 and out["error"] == "unknown_tool", name
    args = {"brief_id": d["brief_id"], "run_id": started["run_id"], "comment_id": "c-1",
            "approval_id": aid, "status": "ACTIVE", "daily_budget": 999999,
            "signature": "ab" * 64, "resolutions": [], "text": "x", "message": "y"}
    for name in assistants.OPS:
        for attempt in ({}, args, {k: args[k] for k in ("run_id", "brief_id") if k in args}):
            svc.call(principal, name, attempt)
    assert hits == []
    after = _statuses(app)
    assert all(after[k] == v for k, v in before.items())
    assert "approved" not in after.values()
    kinds = {r.kind for r in app.state.ledger.receipts(started["run_id"])}
    assert "approval" not in kinds and "external_write" not in kinds
    assert app.state.conn.execute("SELECT count(*) FROM campaigns WHERE run_id = ?",
                                  (started["run_id"],)).fetchone()[0] == 0


def test_mcp_tools_over_http_reach_no_signature(attacked):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app, c, hits, tok, value, d, started = attacked
    before = _statuses(app)

    async def go():
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                                 headers=bearer(value), timeout=60)
        async with http, streamable_http_client(f"{BASE}/mcp", http_client=http) as (
                read, write, _), ClientSession(read, write) as session:
            await session.initialize()
            out = []
            for t in (await session.list_tools()).tools:
                out.append(await session.call_tool(t.name, {}))
                out.append(await session.call_tool(t.name, {"run_id": started["run_id"],
                                                            "approval_id": "x"}))
            for name in ("approve", "sign", "launch", "update_budget"):
                out.append(await session.call_tool(name, {}))
            return out

    results = anyio.run(go)
    assert results and hits == []
    after = _statuses(app)
    assert all(after[k] == v for k, v in before.items())
    assert "approved" not in after.values()


def test_tokens_of_one_account_cannot_see_another_accounts_work(attacked):
    app, c, hits, tok, value, d, started = attacked
    other, other_value = token(app, username="mallory", label="Mallory bot")
    h = bearer(other_value)
    run_id = started["run_id"]
    assert d["brief_id"] not in {b["brief_id"] for b in c.get(
        "/api/v1/briefs", headers=h).json()["briefs"]}
    for path in (f"/briefs/{d['brief_id']}", f"/runs/{run_id}",
                 f"/runs/{run_id}/assets/feed_1x1.png"):
        assert c.get("/api/v1" + path, headers=h).status_code == 404, path
    assert c.post(f"/api/v1/briefs/{d['brief_id']}/confirm", json={"resolutions": []},
                  headers=h).status_code == 404
    assert c.post("/api/v1/runs", json={"brief_id": d["brief_id"]},
                  headers=h).status_code == 404
    pending = c.get("/api/v1/approvals", headers=h).json()["pending"]
    assert run_id not in {p["run_id"] for p in pending}
    assert run_id not in {r["run_id"] for r in c.get("/api/v1/campaigns",
                                                       headers=h).json()["runs"]}
    store = app.state.assistants.tokens
    assert not store.revoke(other.account_id, tok.id)  # not its token
    assert store.get(other.account_id, tok.id) is None
    assert [t.id for t in store.listing(other.account_id)] == [other.id]
    with TestClient(app, base_url=BASE) as browser:  # nor through the account page
        login(browser, app, "mallory", OTHER_PASSWORD)
        page = browser.get("/account").text
        assert tok.id not in page and "Claude Desktop" not in page
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        r = browser.post("/account/tokens/revoke", data={"csrf": csrf, "token": tok.id})
        assert r.status_code == 404
    assert c.get("/api/v1/me", headers=bearer(value)).status_code == 200
    # the owner still sees it all
    assert c.get(f"/api/v1/runs/{run_id}", headers=bearer(value)).status_code == 200


def test_revoked_expired_and_disabled_account_tokens_fail(attacked):
    app, c, hits, *_ = attacked
    store = app.state.assistants.tokens
    rev, rev_value = token(app, label="revoked")
    assert c.get("/api/v1/me", headers=bearer(rev_value)).status_code == 200
    store.revoke(rev.account_id, rev.id)
    assert c.get("/api/v1/me", headers=bearer(rev_value)).status_code == 401
    old, old_value = token(app, label="old")
    with app.state.conn.lock:
        app.state.conn.execute("UPDATE api_tokens SET expires_ts = 1 WHERE id = ?", (old.id,))
        app.state.conn.commit()
    assert c.get("/api/v1/me", headers=bearer(old_value)).status_code == 401
    acct = account(app, "trudy")
    _, t_value = store.create(acct.id, "trudy", "read", 7)
    assert c.get("/api/v1/me", headers=bearer(t_value)).status_code == 200
    with app.state.conn.lock:
        app.state.conn.execute("UPDATE accounts SET status = 'disabled' WHERE id = ?",
                               (acct.id,))
        app.state.conn.commit()
    assert c.get("/api/v1/me", headers=bearer(t_value)).status_code == 401


def test_the_token_value_is_never_in_logs_receipts_or_the_database(attacked, caplog):
    app, c, hits, *_ = attacked
    tok, value = token(app, label="logged")
    caplog.set_level(logging.DEBUG)
    h = bearer(value)
    c.get("/api/v1/me", headers=h)
    c.get("/api/v1/runs/run_nope", headers=h)
    c.post("/api/v1/briefs", json={"text": "x" * 5000}, headers=h)
    c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
           headers={**h, "Accept": "application/json, text/event-stream"})
    secret = value[21:]
    assert secret not in caplog.text and value not in caplog.text
    export = app.state.ledger.export_jsonl()
    assert secret not in export and tok.id in export
    dump = "\n".join(app.state.conn.iterdump())
    assert secret not in dump


def test_the_rate_limit_is_per_token(tmp_path):
    app = make_app(tmp_path)
    svc = app.state.assistants
    svc.requests = Window(3, 60.0)
    svc.drafts = Window(1, 60.0)
    _, one = token(app, label="one")
    _, two = token(app, label="two")
    c = TestClient(app, base_url=BASE)
    for _ in range(3):
        assert c.get("/api/v1/me", headers=bearer(one)).status_code == 200
    r = c.get("/api/v1/me", headers=bearer(one))
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert c.post("/mcp", json={}, headers=bearer(one)).status_code == 429
    assert c.get("/api/v1/me", headers=bearer(two)).status_code == 200  # its own budget
    assert c.post("/api/v1/runs", json={"brief_id": "b_" + "0" * 16},
                  headers=bearer(two)).status_code == 404
    r = c.post("/api/v1/runs", json={"brief_id": "b_" + "0" * 16}, headers=bearer(two))
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"


def test_read_only_refuses_every_call_and_writes_nothing(tmp_path):
    app = make_app(tmp_path, STUDIO_READ_ONLY="1")
    tok, value = token(app)  # the read-only switch is in the call path, not the store
    seq = app.state.ledger.seq()
    c = TestClient(app, base_url=BASE)
    assert c.get("/api/v1/approvals", headers=bearer(value)).status_code == 503
    assert c.post("/api/v1/briefs", json={"text": "x"}, headers=bearer(value)
                  ).status_code == 503
    assert app.state.ledger.seq() == seq


def test_in_the_sandbox_bearer_calls_work_and_cookieless_writes_elsewhere_do_not(
        tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SANDBOX", "1")
    app = make_app(tmp_path)
    app.state.sandbox_resetter.stop()
    _, value = token(app)
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/api/v1/approvals", headers=bearer(value)).status_code == 200
        assert c.post("/api/v1/runs", json={"brief_id": "b_" + "0" * 16},
                      headers=bearer(value)).status_code == 404  # reached the API
        assert c.post("/api/v1/runs", json={"brief_id": "b_" + "0" * 16}
                      ).status_code == 401
        assert c.post("/api/runs", json={"brief": "x"}, headers=bearer(value)
                      ).status_code == 401
