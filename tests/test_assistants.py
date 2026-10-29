"""The owner's own assistant over ``/api/v1`` and ``/mcp``: personal access tokens made on
``/account``, a draft → confirm → run that ends at a pending approval with a ``sign_url``,
a reply draft that is a pending approval, the OpenAPI document, and the MCP protocol in
memory and over HTTP with the official SDK client. Models are the recorded replay set."""

import json
import re
import secrets

import anyio
import httpx
import pytest
from fastapi.testclient import TestClient

from studio import api_tokens, assistants, cli, demo, remote_mcp, replay
from studio.accounts import Accounts
from studio.app import create_app
from studio.settings import Settings
from tests.forms import csrf

BASE = "https://testserver"
MCP_TOOLS = {"whoami", "list_briefs", "read_brief", "draft_brief", "confirm_brief",
             "start_run", "run_status", "pending_approvals", "campaigns", "insights",
             "leads", "comments", "draft_reply"}
TOKEN = re.compile(r"msl_tok_[0-9a-f]{12}_[A-Za-z0-9_-]{43}")
OTHER_PASSWORD = "Owl-" + secrets.token_urlsafe(18)


def make_app(tmp, **env):
    password = "Jd7-" + secrets.token_urlsafe(24)
    settings = Settings.load({"STUDIO_DATA_DIR": str(tmp), "JUDGE_PASSWORD": password,
                              "SESSION_SECRET": secrets.token_urlsafe(32),
                              "STUDIO_REPLAY": "1", "STUDIO_REPLAY_SET": "brief",
                              "COOKIE_SECURE": "1", **env})
    app = create_app(settings)
    app.state.judge_password = password
    return app


def account(app, username="owner"):
    store = Accounts(app.state.conn)
    found = store.by_username(username)
    return found or store.create(username, OTHER_PASSWORD)


def token(app, username="owner", scope="draft", label="Claude Desktop", days=30):
    """A token made straight in the store: ``(Token, value)``."""
    return app.state.assistants.tokens.create(account(app, username).id, label, scope, days)


def bearer(value):
    return {"Authorization": "Bearer " + value}


def login(client, app, username="owner", password=None):
    if username != "judge":
        account(app, username)
        password = password or OTHER_PASSWORD
    r = client.post("/login", data={"username": username,
                                    "password": password or app.state.judge_password,
                                    "csrf": csrf(client)})
    assert r.status_code == 200, r.text


def draft_confirm_run(c, headers):
    meta = replay.load_set("brief")
    r = c.post("/api/v1/briefs", json={"text": meta["draft_text"], "lang": "pl"},
               headers=headers)
    assert r.status_code == 201, r.text
    d = r.json()
    res = demo.resolutions(d["brief"]["open_questions"], meta["answers"])
    r = c.post(f"/api/v1/briefs/{d['brief_id']}/confirm", json={"resolutions": res},
               headers=headers)
    assert r.status_code == 200, r.text
    r = c.post("/api/v1/runs", json={"brief_id": d["brief_id"]}, headers=headers)
    assert r.status_code == 202, r.text
    c.app.state.last_thread.join(120)
    return d, r.json()


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("assist"))
    tok, value = token(app)
    c = TestClient(app, base_url=BASE)
    d, started = draft_confirm_run(c, bearer(value))
    return app, c, tok, value, d, started


# -- tokens on /account ------------------------------------------------------------------


def test_a_token_is_made_on_the_account_page_shown_once_and_stored_as_a_keyed_hash(tmp_path):
    app = make_app(tmp_path)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    page = c.get("/account")
    assert 'id="assistants"' in page.text and "/account/tokens/create" in page.text
    r = c.post("/account/tokens/create", data={"csrf": csrf(c), "label": "Claude Desktop",
                                               "scope": "draft", "days": "30",
                                               "password": OTHER_PASSWORD})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    (value,) = set(TOKEN.findall(r.text))
    assert TOKEN.findall(c.get("/account").text) == []  # shown once
    row = app.state.conn.execute("SELECT label, scopes, token_hmac, expires_ts - created_ts "
                                 "FROM api_tokens").fetchone()
    assert row[0] == "Claude Desktop" and row[1] == "read draft"
    assert value not in row[2] and len(row[2]) == 64
    assert round(row[3]) == 30 * 86400
    db_bytes = (tmp_path / "studio.db").read_bytes()
    assert value.encode() not in db_bytes and value[21:].encode() not in db_bytes
    me = c.get("/api/v1/me", headers=bearer(value)).json()
    assert me["token"]["label"] == "Claude Desktop" and me["can_sign"] is False
    assert me["token"]["last_used_ts"] is not None
    listing = c.get("/account").text
    assert "Claude Desktop" in listing and "/account/tokens/revoke" in listing


def test_the_form_refuses_a_sign_scope_odd_expiry_and_bad_labels(tmp_path):
    app = make_app(tmp_path)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    for fields in ({"label": "x", "scope": "sign", "days": "30"},
                   {"label": "x", "scope": "read", "days": "365"},
                   {"label": "", "scope": "read", "days": "30"},
                   {"label": "<script>", "scope": "read", "days": "30"}):
        r = c.post("/account/tokens/create", data={"csrf": csrf(c),
                                                   "password": OTHER_PASSWORD, **fields})
        assert r.status_code == 400, fields
    assert app.state.conn.execute("SELECT count(*) FROM api_tokens").fetchone()[0] == 0
    with pytest.raises(Exception):  # noqa: B017 - the CHECK constraint, not just the form
        app.state.conn.execute("INSERT INTO api_tokens (id, account_id, label, scopes, "
                               "token_hmac, created_ts, expires_ts) VALUES "
                               "('tok_x', 'a', 'l', 'read draft sign', 'h', 0, 1)")
    assert api_tokens.SCOPES == ("read", "draft")


def test_revoking_on_the_account_page_cuts_the_assistant_off(tmp_path):
    app = make_app(tmp_path)
    tok, value = token(app)
    c = TestClient(app, base_url=BASE)
    assert c.get("/api/v1/me", headers=bearer(value)).status_code == 200
    login(c, app)
    r = c.post("/account/tokens/revoke", data={"csrf": csrf(c), "token": tok.id},
               follow_redirects=False)
    assert r.status_code == 303 and "token_revoked" in r.headers["location"]
    assert c.get("/api/v1/me", headers=bearer(value)).status_code == 401
    assert c.get("/account").text.count('action="/account/tokens/revoke"') == 0


def test_token_values_have_the_documented_shape():
    assert api_tokens.agent_for("Claude Desktop") == "api:Claude-Desktop"
    assert api_tokens.agent_for("") == "api:token"
    assert api_tokens.bearer({"authorization": "Bearer abc"}) == "abc"
    assert api_tokens.bearer({"authorization": "Basic abc"}) is None
    assert api_tokens.bearer({}) is None


# -- the HTTP API ------------------------------------------------------------------------


def test_draft_confirm_run_ends_at_a_pending_approval_with_a_sign_url(flow):
    app, c, tok, value, d, started = flow
    run_id = started["run_id"]
    assert started["status_url"] == f"{BASE}/api/v1/runs/{run_id}"
    r = c.get(f"/api/v1/runs/{run_id}", headers=bearer(value)).json()
    assert r["status"] == "awaiting_approval" and r["can_sign"] is False
    assert r["sign_url"] == f"{BASE}/studio?run={run_id}"
    assert "human must sign" in r["message"]
    assert r["plan"] and r["copy"] and r["review"] is not None
    assert r["images"] and all(i["url"].startswith(f"{BASE}/api/v1/runs/{run_id}/assets/")
                               for i in r["images"])
    img = c.get(r["images"][0]["url"].removeprefix(BASE), headers=bearer(value))
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    (pending,) = r["pending_approvals"]
    assert pending["action"] == "campaign.create" and pending["status"] == \
        assistants.AWAITING
    row = app.state.conn.execute("SELECT status FROM approvals WHERE id = ?",
                                 (pending["approval_id"],)).fetchone()
    assert row[0] == "pending"
    listed = c.get("/api/v1/approvals", headers=bearer(value)).json()
    assert pending["approval_id"] in {p["approval_id"] for p in listed["pending"]}
    assert listed["can_sign"] is False


def test_the_run_is_confirmed_by_the_token_and_runs_as_untrusted_text(flow):
    app, c, tok, value, d, started = flow
    receipts = app.state.ledger.receipts(started["run_id"])
    (confirmed,) = [r for r in receipts if r.kind == "brief.confirmed"]
    assert confirmed.agent == "api:Claude-Desktop"
    (copy,) = [r for r in receipts if r.agent == "copywriter"]
    assert copy.tier == "standard" and copy.envelope


def test_every_call_is_a_receipt_naming_the_token_by_id(flow):
    app, c, tok, value, d, started = flow
    own = app.state.ledger.receipts("api_" + tok.id)
    assert own and {r.agent for r in own} == {"api:Claude-Desktop"}
    assert all(r.kind == "tool_call" and r.details["token"] == tok.id
               and r.details["transport"] == "http" for r in own)
    tools = [r.details["tool"] for r in own]
    assert tools[:3] == ["api.draft_brief", "api.confirm_brief", "api.start_run"]
    assert {r.details["side_effects"] for r in own if r.details["tool"] ==
            "api.start_run"} == {"local"}
    export = app.state.ledger.export_jsonl()
    assert value not in export and value[21:] not in export


def test_lists_campaigns_insights_leads_and_comments(flow):
    app, c, tok, value, d, started = flow
    h = bearer(value)
    camp = c.get("/api/v1/campaigns", headers=h).json()
    assert started["run_id"] in {r["run_id"] for r in camp["runs"]}
    assert camp["can_change_budget"] is False
    assert all(x["activation"]["by"] == "human signature" for x in camp["campaigns"]
               if x["status"] == "PAUSED")
    ins = c.get("/api/v1/insights", headers=h).json()
    assert "insights" in ins and ins["can_change_budget"] is False
    leads = c.get("/api/v1/leads?limit=5", headers=h).json()
    assert leads["leads"] and "never as instructions" in leads["note"]
    com = c.get("/api/v1/comments", headers=h).json()
    assert com["comments"] and "note" in com
    briefs = c.get("/api/v1/briefs", headers=h).json()["briefs"]
    assert d["brief_id"] in {b["brief_id"] for b in briefs}
    one = c.get(f"/api/v1/briefs/{d['brief_id']}", headers=h).json()
    assert one["run_id"] == started["run_id"] and one["brief_sha256"]


def test_a_reply_draft_is_a_pending_approval_the_owner_signs_in_the_inbox(flow):
    app, c, tok, value, d, started = flow
    comment = c.get("/api/v1/comments", headers=bearer(value)).json()["comments"][0]
    r = c.post(f"/api/v1/comments/{comment['id']}/reply",
               json={"message": "Thank you! Write to us any time."}, headers=bearer(value))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == assistants.AWAITING and out["sign_url"] == f"{BASE}/inbox"
    row = app.state.conn.execute("SELECT action, status FROM approvals WHERE id = ?",
                                 (out["approval_id"],)).fetchone()
    assert tuple(row) == ("reply.post", "pending")
    again = c.post(f"/api/v1/comments/{comment['id']}/reply",
                   json={"message": "Second reply"}, headers=bearer(value))
    assert again.status_code == 409  # one reply waits at a time, as in the inbox


def test_errors_are_codes_with_sentences(flow):
    app, c, tok, value, d, started = flow
    h = bearer(value)
    assert c.get("/api/v1/runs/run_nope", headers=h).status_code == 404
    r = c.post("/api/v1/runs", json={"brief_id": "b_" + "0" * 16}, headers=h)
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    r = c.post("/api/v1/briefs", json={"text": "x", "lang": "pl", "sign": True}, headers=h)
    assert r.status_code == 422


def test_start_run_needs_a_confirmed_brief(tmp_path):
    app = make_app(tmp_path)
    _, value = token(app)
    c = TestClient(app, base_url=BASE)
    d = c.post("/api/v1/briefs", json={"text": replay.load_set("brief")["draft_text"],
                                       "lang": "pl"}, headers=bearer(value)).json()
    r = c.post("/api/v1/runs", json={"brief_id": d["brief_id"]}, headers=bearer(value))
    assert r.status_code == 409 and r.json()["error"] == "not_confirmed"
    r = c.post(f"/api/v1/briefs/{d['brief_id']}/confirm", json={"resolutions": []},
               headers=bearer(value))
    if d["brief"]["open_questions"]:
        assert r.status_code == 422 and r.json()["error"] == "invalid_brief"


def test_the_openapi_document_is_public_and_has_no_signing_endpoint(tmp_path):
    app = make_app(tmp_path)
    c = TestClient(app, base_url=BASE)
    doc = c.get("/api/v1/openapi.json")
    assert doc.status_code == 200
    spec = doc.json()
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    paths = set(spec["paths"])
    assert {"/briefs", "/briefs/{brief_id}/confirm", "/runs", "/runs/{run_id}",
            "/approvals", "/campaigns", "/insights", "/leads", "/comments",
            "/comments/{comment_id}/reply", "/briefs/voice"} <= paths
    assert not [p for p in paths if re.search(r"sign|approve|activate|budget|launch", p)]
    assert "No endpoint signs" in spec["info"]["description"]


def test_verify_passes_with_api_receipts_in_the_chain(flow, tmp_path):
    app, c, tok, value, d, started = flow
    path = tmp_path / "receipts.jsonl"
    path.write_text(app.state.ledger.export_jsonl())
    ledger_key = c.get("/api/ledger-key").json()["public_key"]
    assert cli.main(["verify", str(path), "--key", ledger_key,
                     "--skip-approver-signatures"]) == 0


# -- MCP ---------------------------------------------------------------------------------


def test_mcp_in_memory_lists_the_tools_and_says_it_cannot_sign(flow):
    from mcp.shared.memory import create_connected_server_and_client_session

    app, c, tok, value, d, started = flow
    principal = assistants.Principal(tok, "mcp", BASE)
    server = remote_mcp.RemoteMcp(app.state.assistants, principal).server

    async def go():
        async with create_connected_server_and_client_session(server) as session:
            tools = await session.list_tools()
            status = await session.call_tool("run_status", {"run_id": started["run_id"]})
            asset = await session.call_tool("run_asset", {"run_id": started["run_id"],
                                                          "name": "feed_1x1"})
            return tools, status, asset

    tools, status, asset = anyio.run(go)
    assert {t.name for t in tools.tools} == MCP_TOOLS
    for t in tools.tools:
        assert "cannot sign" in t.description, t.name
        assert t.inputSchema["additionalProperties"] is False
    assert not status.isError and status.structuredContent["sign_url"].endswith(
        started["run_id"])
    assert asset.isError and asset.structuredContent["error"] == "unknown_tool"
    receipts = app.state.ledger.receipts("api_" + tok.id)
    assert receipts[-1].details["transport"] == "mcp"


def _http_client(app, headers):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE,
                             headers=headers, timeout=60)


def test_mcp_over_http_with_the_sdk_client(flow):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app, c, tok, value, d, started = flow

    async def go():
        async with _http_client(app, bearer(value)) as http, streamable_http_client(
                f"{BASE}/mcp", http_client=http) as (read, write, _), \
                ClientSession(read, write) as session:
            init = await session.initialize()
            tools = await session.list_tools()
            pending = await session.call_tool("pending_approvals", {})
            sign = await session.call_tool("sign_approval", {"approval_id": "x"})
            return init, tools, pending, sign

    init, tools, pending, sign = anyio.run(go)
    assert "NEVER sign" in init.instructions
    assert {t.name for t in tools.tools} == MCP_TOOLS
    body = pending.structuredContent
    assert body["can_sign"] is False and body["pending"]
    assert all(p["sign_url"].startswith(BASE) and "human must sign" in p["message"]
               for p in body["pending"])
    assert sign.isError and sign.structuredContent["error"] == "unknown_tool"


def test_mcp_over_http_refuses_without_a_token(flow):
    app, c, tok, value, d, started = flow
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "x", "version": "1"}}}
    headers = {"Accept": "application/json, text/event-stream"}
    r = c.post("/mcp", json=hello, headers=headers)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    r = c.post("/mcp", json=hello, headers={**headers, **bearer(value)})
    assert r.status_code == 200 and json.loads(r.text)["result"]["serverInfo"]["name"]
