"""``studio mcp``: the tools over the studio's own database, a receipt per call (agent
``mcp:<client>``), drafts and runs from recorded answers that end awaiting a human
signature in the web UI, and ``studio verify`` on a chain holding MCP receipts and the
owner's signature."""

import base64
import json
import secrets
from pathlib import Path

import anyio
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from studio import cli, demo, mcp_server, replay
from studio.accounts import Accounts
from studio.app import create_app
from studio.receipts import public_key_raw
from studio.settings import ReadOnlyMode, Settings

CLIENT = "test-client"
ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {"list_briefs", "read_brief", "list_campaigns", "read_run", "read_campaign",
                  "draft_brief", "run_pipeline", "request_launch", "read_receipts",
                  "read_report", "verify_export"}


def make_studio(tmp, **env):
    """A studio app on ``tmp`` (replay set ``brief``) and an MCP server on its database,
    bound to the ``judge`` account."""
    password = "Jd7-" + secrets.token_urlsafe(24)
    settings = Settings.load({"STUDIO_DATA_DIR": str(tmp), "JUDGE_PASSWORD": password,
                              "SESSION_SECRET": secrets.token_urlsafe(32),
                              "STUDIO_REPLAY": "1", "STUDIO_REPLAY_SET": "brief", **env})
    app = create_app(settings)
    account = Accounts(app.state.conn).by_username("judge")
    studio = mcp_server.StudioMcp(settings, app.state.conn, app.state.ledger, account,
                                  ui_url="https://studio.example")
    return app, studio, password


def draft_and_run(studio, client=CLIENT):
    meta = replay.load_set("brief")
    d = studio.call(client, "draft_brief", {"text": meta["draft_text"], "lang": "pl"})
    assert "error" not in d, d
    res = demo.resolutions(d["brief"]["open_questions"], meta["answers"])
    return d, studio.call(client, "run_pipeline", {"brief_id": d["brief_id"],
                                                   "resolutions": res})


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    app, studio, password = make_studio(tmp_path_factory.mktemp("mcp"))
    d, run = draft_and_run(studio)
    return app, studio, password, d, run


def mcp_receipts(studio):
    return studio.ledger.receipts(studio.run_id)


def test_the_tool_list_reads_and_drafts_only():
    assert set(mcp_server.TOOLS) == EXPECTED_TOOLS
    for tool in mcp_server.TOOLS.values():
        assert tool.side_effects in ("none", "local"), tool.name
        assert tool.schema["additionalProperties"] is False, tool.name
    local = {t.name for t in mcp_server.TOOLS.values() if t.side_effects == "local"}
    assert local == {"draft_brief", "run_pipeline"}


def test_actor_is_an_opaque_receipt_agent():
    assert mcp_server.actor("claude-code") == "mcp:claude-code"
    assert mcp_server.actor("Claude Code 2.1") == "mcp:Claude-Code-2.1"
    assert mcp_server.actor("") == "mcp:client"
    assert mcp_server.actor("me@example.com") == "mcp:me-example.com"
    assert mcp_server.actor("+48 600 123 456") == "mcp:client"  # redaction would change it
    assert mcp_server.actor("x" * 200) == "mcp:" + "x" * 60


def test_a_run_ends_awaiting_a_human_signature_with_the_approval_page(flow):
    app, studio, _, d, run = flow
    assert run["status"] == mcp_server.AWAITING
    assert run["run_status"] == "awaiting_approval" and run["action"] == "campaign.create"
    assert run["approval_url"] == f"https://studio.example/studio?run={run['run_id']}"
    assert run["approval_url"] in run["message"]
    row = app.state.conn.execute("SELECT status, action FROM approvals WHERE id = ?",
                                 (run["approval_id"],)).fetchone()
    assert tuple(row) == ("pending", "campaign.create")
    kinds = [r.kind for r in studio.ledger.receipts(run["run_id"])]
    assert "approval" not in kinds and "external_write" not in kinds
    assert app.state.conn.execute("SELECT count(*) FROM campaigns WHERE run_id = ?",
                                  (run["run_id"],)).fetchone()[0] == 0


def test_the_brief_is_confirmed_by_the_client_and_runs_as_untrusted_text(flow):
    _, studio, _, d, run = flow
    receipts = studio.ledger.receipts(run["run_id"])
    (confirmed,) = [r for r in receipts if r.kind == "brief.confirmed"]
    assert confirmed.agent == "mcp:" + CLIENT
    assert confirmed.details["brief_sha256"] == run["brief_sha256"]
    assert confirmed.details["voice"] == ""  # untrusted like a transcript, not a voice brief
    draft_call = receipts[0]
    assert draft_call.kind == "model_call" and draft_call.envelope
    assert draft_call.tier == "standard"
    (copy,) = [r for r in receipts if r.agent == "copywriter"]  # as for a voice transcript
    assert copy.tier == "standard" and copy.envelope


def test_every_call_leaves_a_receipt_in_the_servers_own_run(flow):
    _, studio, _, d, run = flow
    before = len(mcp_receipts(studio))
    calls = [("list_briefs", {}), ("read_brief", {"brief_id": d["brief_id"]}),
             ("list_campaigns", {"limit": 5}), ("read_run", {"run_id": run["run_id"]}),
             ("read_campaign", {"item_id": "nope"}), ("request_launch",
                                                       {"run_id": run["run_id"]}),
             ("read_receipts", {"run_id": run["run_id"], "limit": 3}), ("read_report", {}),
             ("verify_export", {}), ("sign_approval", {"approval_id": run["approval_id"]}),
             ("list_briefs", {"limit": "all"})]
    results = [studio.call(CLIENT, name, args) for name, args in calls]
    after = mcp_receipts(studio)[before:]
    assert [r.id for r in after] == [x["receipt_id"] for x in results]
    for r, (name, _) in zip(after, calls, strict=True):
        assert r.kind == "tool_call" and r.agent == "mcp:" + CLIENT
        assert r.details["account"] == studio.account_id
        assert r.details["tool"] == ("mcp." + name if name in EXPECTED_TOOLS else "mcp.unknown")
        assert r.details["side_effects"] == "none"
        assert len(r.input_sha256) == 1 and len(r.output_sha256) == 64
    assert results[4]["error"] == "not_found" and after[4].details["status"] == "refused"
    assert results[5]["status"] == mcp_server.AWAITING
    assert after[5].details["status"] == mcp_server.AWAITING
    assert after[5].details["approval_id"] == run["approval_id"]
    assert results[9]["error"] == "unknown_tool"
    assert results[10]["error"] == "invalid_arguments"
    assert {r.run_id for r in after} == {studio.run_id}  # never in a campaign run


def test_reads_return_what_the_ui_shows(flow):
    _, studio, _, d, run = flow
    briefs = studio.call(CLIENT, "list_briefs", {})["briefs"]
    assert briefs[0]["brief_id"] == d["brief_id"] and briefs[0]["state"] == "confirmed"
    brief = studio.call(CLIENT, "read_brief", {"brief_id": d["brief_id"]})
    assert brief["brief_sha256"] == run["brief_sha256"]
    assert brief["brief_v1"]["open_questions"] == []
    runs = studio.call(CLIENT, "list_campaigns", {})["runs"]
    assert runs[0]["run_id"] == run["run_id"] and runs[0]["approval_url"] == run["approval_url"]
    got = studio.call(CLIENT, "read_run", {"run_id": run["run_id"]})
    assert got["state"]["pending_id"] == run["approval_id"]
    assert got["signature"]["status"] == mcp_server.AWAITING
    rec = studio.call(CLIENT, "read_receipts", {"run_id": run["run_id"], "kind": "brief.confirmed"})
    assert rec["matched"] == 1 and rec["receipts"][0]["hash"]


def test_field_errors_are_the_uis(flow):
    _, studio, _, _, _ = flow
    d = studio.call(CLIENT, "draft_brief", {"text": replay.load_set("brief")["draft_text"]})
    brief = dict(d["brief"])
    brief["budget"] = {**brief["budget"], "amount": 10_000}
    res = demo.resolutions(d["brief"]["open_questions"], {})
    out = studio.call(CLIENT, "run_pipeline", {"brief_id": d["brief_id"], "resolutions": res,
                                               "brief": brief})
    assert out["error"] == "invalid_brief" and out["http_status"] == 422
    assert any(k.startswith("budget") for k in out["fields"])
    out = studio.call(CLIENT, "run_pipeline", {"brief_id": d["brief_id"], "resolutions": []})
    if d["brief"]["open_questions"]:
        assert out["error"] == "invalid_brief"
        assert set(out["fields"].values()) == {"unresolved"}


def test_verify_passes_with_mcp_receipts_and_the_owners_signature(flow, tmp_path):
    app, studio, password, _, run = flow
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/login", json={"username": "judge", "password": password}
                      ).status_code == 200
        key = Ed25519PrivateKey.generate()
        raw = base64.urlsafe_b64encode(public_key_raw(key.public_key())).rstrip(b"=").decode()
        approver = c.post("/api/keys", json={"public_key": raw}).json()["approver"]
        signed = demo._sign(c, key, approver, run["approval_id"])
        assert signed["executed"] is True
        after = studio.call(CLIENT, "request_launch", {"run_id": run["run_id"]})
        assert after["status"] == mcp_server.AWAITING and after["action"] == "campaign.activate"
        item = studio.call(CLIENT, "read_campaign", {"item_id": after["item_id"]})
        assert item["status"] == "PAUSED" and item["signature"]["action"] == "campaign.activate"
        export = c.get("/api/receipts.jsonl").text
        keys = c.get("/api/approver-keys").json()
        ledger_key = c.get("/api/ledger-key").json()["public_key"]
    path = tmp_path / "receipts.jsonl"
    path.write_text(export)
    assert '"agent": "mcp:test-client"' in export
    (tmp_path / "keys.json").write_text(json.dumps(keys))
    assert cli.main(["verify", str(path), "--key", ledger_key,
                     "--approver-keys", str(tmp_path / "keys.json")]) == 0
    out = studio.call(CLIENT, "verify_export", {"path": str(path)})
    assert out["ok"] and out["signatures"] >= 1 and out["exit"] == 0
    assert studio.call(CLIENT, "verify_export", {"path": str(tmp_path / "keys.json")}
                       )["error"] == "path_not_allowed"


def test_read_only_refuses_every_call_before_anything_is_written(tmp_path):
    _, studio, _ = make_studio(tmp_path, STUDIO_READ_ONLY="1")
    seq = studio.ledger.seq()
    with pytest.raises(ReadOnlyMode):
        studio.call(CLIENT, "list_briefs", {})
    assert studio.ledger.seq() == seq


def test_a_disabled_account_is_refused_with_a_receipt(tmp_path):
    app, studio, _ = make_studio(tmp_path)
    app.state.conn.execute("UPDATE accounts SET status = 'disabled' WHERE id = ?",
                           (studio.account_id,))
    app.state.conn.commit()
    out = studio.call(CLIENT, "list_briefs", {})
    assert out["error"] == "account_inactive"
    assert mcp_receipts(studio)[-1].details["status"] == "refused"


def test_open_studio_needs_the_existing_database_and_an_active_account(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(f"STUDIO_DATA_DIR={tmp_path / 'var'}\n")
    env.chmod(0o600)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="no studio database"):
        mcp_server.open_studio(env, "judge")
    make_studio(tmp_path / "var")
    with pytest.raises(ValueError, match="no active account"):
        mcp_server.open_studio(env, "nobody")
    assert mcp_server.open_studio(env, "judge").account_id
    assert cli.main(["mcp", "--account", "nobody", "--env-file", str(env)]) == cli.EXIT_ERROR


def test_over_the_protocol_the_client_name_becomes_the_receipt_agent(tmp_path):
    from mcp import types
    from mcp.shared.memory import create_connected_server_and_client_session

    _, studio, _ = make_studio(tmp_path)
    server = mcp_server.build_server(studio)

    async def go():
        async with create_connected_server_and_client_session(
                server, client_info=types.Implementation(name="claude-code", version="2")
        ) as session:
            tools = await session.list_tools()
            listed = await session.call_tool("list_briefs", {"limit": 3})
            bad = await session.call_tool("activate_campaign", {"item_id": "x"})
            return tools, listed, bad

    tools, listed, bad = anyio.run(go)
    assert {t.name for t in tools.tools} == EXPECTED_TOOLS
    assert not listed.isError and "briefs" in listed.structuredContent
    assert bad.isError and bad.structuredContent["error"] == "unknown_tool"
    agents = [r.agent for r in mcp_receipts(studio)]
    assert agents == ["mcp:claude-code", "mcp:claude-code"]


def test_studio_mcp_speaks_the_protocol_on_stdio_and_nothing_else(tmp_path):
    import sys

    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.stdio import stdio_client

    make_studio(tmp_path / "var")
    env = tmp_path / ".env"
    env.write_text(f"STUDIO_DATA_DIR={tmp_path / 'var'}\n")
    env.chmod(0o600)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "studio.cli", "mcp", "--account", "judge",
                                      "--env-file", str(env)], cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)})

    async def go():
        async with stdio_client(params) as (read, write), ClientSession(
                read, write, client_info=types.Implementation(name="stdio-test", version="1")
        ) as session:
            await session.initialize()
            return await session.call_tool("verify_export", {})

    out = anyio.run(go)
    assert not out.isError and out.structuredContent["ok"] is True


def test_each_run_replays_the_recorded_set_from_its_start(tmp_path):
    _, studio, _ = make_studio(tmp_path)
    runs = [draft_and_run(studio)[1] for _ in range(3)]
    assert [r["status"] for r in runs] == [mcp_server.AWAITING] * 3
    assert len({r["approval_id"] for r in runs}) == 3


def test_a_google_search_brief_gets_its_own_pending_approval_from_the_mock(tmp_path):
    app, studio, _ = make_studio(tmp_path)
    meta = replay.load_set("brief")
    d = studio.call(CLIENT, "draft_brief", {"text": meta["draft_text"], "lang": "pl"})
    brief = {**d["brief"], "channels": {"google_search": True, "google_daily_budget": 20}}
    res = demo.resolutions(d["brief"]["open_questions"], meta["answers"])
    run = studio.call(CLIENT, "run_pipeline", {"brief_id": d["brief_id"], "resolutions": res,
                                               "brief": brief})
    assert run["status"] == mcp_server.AWAITING, run
    assert studio.google.name == "mock" and not hasattr(studio.google, "create_search_campaign")
    google = run.get("google_approval_id")
    if google:  # the recorded copy can make a Responsive Search Ad
        row = app.state.conn.execute("SELECT status, action FROM approvals WHERE id = ?",
                                     (google,)).fetchone()
        assert tuple(row) == ("pending", "google.create")
    else:
        assert run["state"]["google_error"]
