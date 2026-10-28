"""Studio UI and its API contract (D2-8): run → live events → payload → signed approval."""

import base64
import json
import re
import secrets
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.approvals import Pending
from studio.muse.client import MuseClient
from studio.muse.openweights import OpenWeightsClient
from studio.receipts import public_key_raw, verify
from studio.settings import Budget, Settings
from tests.cassettes import FAKE_KEY, load, transport

SECRET = secrets.token_urlsafe(32)
PASSWORD = "Jd7-" + secrets.token_urlsafe(24)


def settings(tmp_path, **extra):
    env = {"STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": PASSWORD,
           "SESSION_SECRET": SECRET, "COOKIE_SECURE": "1"}
    env.update(extra)
    return Settings.load(env)


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.fixture
def app(tmp_path):
    a = create_app(settings(tmp_path))
    tr, sent = transport("producer_loop")
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=tr)
    a.state.open_weights = OpenWeightsClient("", ledger=a.state.ledger)
    a.state.sent = sent
    return a


def new_client(app):
    return TestClient(app, base_url="https://testserver")


def login(client):
    r = client.post("/login", data={"username": "judge", "password": PASSWORD})
    assert r.status_code == 200


def register(client):
    key = Ed25519PrivateKey.generate()
    r = client.post("/api/keys", json={"public_key": b64url(public_key_raw(key.public_key()))})
    assert r.status_code == 200, r.text
    return key, r.json()["approver"]


def wait_for(client, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["status"] != "running":
            return data
        time.sleep(0.05)
    raise AssertionError("run did not finish")


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    """One replayed run shared by the approval tests (the SAM decode takes seconds)."""
    tmp = tmp_path_factory.mktemp("ui")
    a = create_app(settings(tmp))
    tr, _ = transport("producer_loop")
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=tr)
    a.state.open_weights = OpenWeightsClient("", ledger=a.state.ledger)
    with new_client(a) as c:
        login(c)
        r = c.post("/api/runs", json={"brief": load("producer_loop")["brief"]})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        data = wait_for(c, run_id)
    return a, run_id, data


def test_studio_page_renders_accessible_form(app):
    with new_client(app) as c:
        r = c.get("/studio")
    assert r.status_code == 200
    html = r.text
    assert '<label for="brief"' in html and 'id="brief"' in html
    assert 'src="/static/app.js"' in html
    assert not re.search(r"<script(?![^>]*src=)", html)  # CSP: no inline scripts
    assert 'aria-live="polite"' in html and 'role="status"' in html
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_app_js_signs_with_non_extractable_ed25519_and_no_inner_html():
    from studio.app import UI

    js = (UI / "static" / "signer.js").read_text()
    assert "innerHTML" not in (UI / "static" / "app.js").read_text()
    assert 'generateKey({ name: "Ed25519" }, false, ["sign", "verify"])' in js
    assert '"/api/keys"' in js and "/payload?approver=" in js
    assert "crypto.subtle.sign" in js
    assert "innerHTML" not in js


def test_run_requires_login(app):
    with new_client(app) as c:
        assert c.post("/api/runs", json={"brief": "x"}).status_code == 401


def test_run_refuses_cross_origin_and_bad_briefs(app):
    with new_client(app) as c:
        login(c)
        r = c.post("/api/runs", json={"brief": "x"}, headers={"origin": "https://evil.test"})
        assert r.status_code == 403
        assert c.post("/api/runs", json={"brief": ""}).status_code == 400
        assert c.post("/api/runs", json={"brief": "x" * 2001}).status_code == 413


def test_read_only_blocks_runs(tmp_path):
    a = create_app(settings(tmp_path, STUDIO_READ_ONLY="1"))
    with new_client(a) as c:
        login(c)
        assert c.post("/api/runs", json={"brief": "x"}).status_code == 503


def test_one_run_at_a_time(app):
    app.state.active_run = "run_busy"
    with new_client(app) as c:
        login(c)
        r = c.post("/api/runs", json={"brief": "x"})
    assert r.status_code == 409 and r.json()["run_id"] == "run_busy"


def test_full_run_ends_in_pending_approval(finished):
    a, run_id, data = finished
    assert data["status"] == "awaiting_approval"
    state = data["state"]
    assert state["pending_id"] and len(state["creatives"]["images"]) == 3
    assert {r["kind"] for r in data["receipts"]} >= {"model_call", "tool_call", "review"}
    assert all(set(r) >= {"hash", "cost_usd"} for r in data["receipts"])


def test_events_stream_receipts_state_and_end(finished):
    a, run_id, data = finished
    with new_client(a) as c:
        login(c)
        with c.stream("GET", f"/api/runs/{run_id}/events") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            body = "".join(r.iter_text())
    events = re.findall(r"^event: (\w+)$", body, re.M)
    assert events.count("receipt") == len(data["receipts"])
    assert "state" in events and events[-1] == "end"


def test_assets_served_and_names_restricted(finished):
    a, run_id, _ = finished
    with new_client(a) as c:
        r = c.get(f"/assets/{run_id}/feed_1x1.png")
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
        assert c.get(f"/assets/{run_id}/cutout.png").status_code == 200
        assert c.get(f"/assets/{run_id}/secret.png").status_code == 404
        assert c.get("/assets/..%2Fvar/feed_1x1.png").status_code == 404


def test_payload_needs_a_session(finished):
    a, _, data = finished
    with new_client(a) as c:
        r = c.get(f"/api/approvals/{data['state']['pending_id']}/payload?approver=x")
    assert r.status_code == 401


def test_sign_exact_payload_then_replay_is_refused(finished, tmp_path):
    a, run_id, data = finished
    pending_id = data["state"]["pending_id"]
    with new_client(a) as c:
        login(c)
        key, approver = register(c)
        p = c.get(f"/api/approvals/{pending_id}/payload", params={"approver": approver})
        assert p.status_code == 200
        body = p.json()
        message = base64.b64decode(body["message_b64"])
        pending = Pending.load(a.state.conn, pending_id)
        assert message == pending.message(approver)  # the exact signed bytes
        assert body["content"]["campaign"]["status"] == "PAUSED"

        bad = c.post(f"/api/approvals/{pending_id}",
                     json={"approver": approver, "signature": "00" * 64})
        assert bad.status_code == 403 and bad.json()["error"] == "bad_signature"

        sig = key.sign(message).hex()
        other = new_client(a)
        with other:
            login(other)  # another session cannot use this session's key
            r = other.post(f"/api/approvals/{pending_id}",
                           json={"approver": approver, "signature": sig})
            assert r.status_code == 403 and r.json()["error"] == "wrong_session"

        ok = c.post(f"/api/approvals/{pending_id}", json={"approver": approver,
                                                           "signature": sig})
        assert ok.status_code == 200, ok.text
        assert ok.json()["signature"] == sig
        result = ok.json()["result"]
        assert ok.json()["executed"] is True and result["status"] == "PAUSED"
        item = result["campaign_item_id"]
        campaign = c.get(f"/api/campaigns/{item}").json()
        assert campaign["status"] == "PAUSED" and campaign["adapter"] == "mock"
        assert campaign["preview"]["kind"] == "inline"
        act = c.post(f"/api/campaigns/{item}/activate")
        assert act.status_code == 201 and act.json()["content"]["ceiling_minor"] == 5000
        ap = c.get(f"/api/approvals/{act.json()['approval_id']}/payload",
                   params={"approver": approver}).json()
        done = c.post(f"/api/approvals/{ap['approval_id']}", json={
            "approver": approver, "signature": key.sign(base64.b64decode(
                ap["message_b64"])).hex()})
        assert done.status_code == 200 and done.json()["result"]["status"] == "ACTIVE"
        assert c.get(f"/api/campaigns/{item}").json()["status"] == "ACTIVE"
        again = c.post(f"/api/approvals/{pending_id}", json={"approver": approver,
                                                              "signature": sig})
        assert again.status_code == 409 and again.json()["error"] == "nonce_used"
        assert c.get(f"/api/runs/{run_id}").json()["status"] == "approved"
        registry = c.get("/api/approver-keys").json()

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    path = tmp_path / "receipts.jsonl"
    path.write_text(a.state.ledger.export_jsonl())
    keys = {k: Ed25519PublicKey.from_public_bytes(bytes.fromhex(v)) for k, v in registry.items()}
    report = verify(path, a.state.ledger.public_key, approver_keys=keys)
    assert report.ok, report.errors
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    approvals = [r for r in lines if r.get("kind") == "approval"]
    assert len(approvals) == 2 and approvals[0]["details"]["sig"] == sig
    writes = [r for r in lines if r.get("kind") == "external_write"]
    assert [w["details"]["action"] for w in writes] == ["campaign.create", "campaign.activate"]
    assert report.signatures == 2


def test_malformed_approval_body_is_400(finished):
    a, _, data = finished
    with new_client(a) as c:
        login(c)
        r = c.post(f"/api/approvals/{data['state']['pending_id']}",
                   json={"approver": "x", "signature": "zz"})
    assert r.status_code == 400


def test_run_state_and_events_need_a_session(finished):
    a, run_id, _ = finished
    with new_client(a) as c:
        assert c.get(f"/api/runs/{run_id}").status_code == 401
        assert c.get(f"/api/runs/{run_id}/events").status_code == 401


def test_parallel_streams_are_capped(finished, monkeypatch):
    from studio import routes_studio

    a, run_id, _ = finished
    monkeypatch.setattr(routes_studio, "MAX_STREAMS", 0)
    with new_client(a) as c:
        login(c)
        r = c.get(f"/api/runs/{run_id}/events")
    assert r.status_code == 429 and r.json()["error"] == "too_many_streams"


def test_stream_reads_receipts_only_when_the_ledger_changed(tmp_path, monkeypatch):
    from studio import db, routes_studio

    monkeypatch.setattr(routes_studio, "SSE_POLL_S", 0.01)
    monkeypatch.setattr(routes_studio, "SSE_MAX_S", 0.5)
    a = create_app(settings(tmp_path))
    with db.transaction(a.state.conn):
        a.state.conn.execute("INSERT INTO run_state (run_id, status, state, updated_ts) "
                             "VALUES ('run_idle', 'running', '{}', 1)")
    reads = []
    real = a.state.ledger.receipts
    monkeypatch.setattr(a.state.ledger, "receipts", lambda *x: reads.append(x) or real(*x))
    with new_client(a) as c:
        login(c)
        body = c.get("/api/runs/run_idle/events").text
    assert '"timeout"' in body
    assert len(reads) <= 1  # dozens of polls, one read of an unchanged ledger


def _events_endpoint(a):
    return next(r.endpoint for r in a.routes
                if getattr(r, "path", "") == "/api/runs/{run_id}/events")


def _session_request(a, c):
    from starlette.requests import Request
    cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                    "headers": [(b"cookie", cookie.encode())]})


def test_stream_slot_is_freed_when_the_client_leaves_before_the_stream_starts(tmp_path):
    import asyncio

    from studio import db
    a = create_app(settings(tmp_path))
    with db.transaction(a.state.conn):
        a.state.conn.execute("INSERT INTO run_state (run_id, status, state, updated_ts) "
                             "VALUES ('run_gone', 'running', '{}', 1)")
    with new_client(a) as c:
        login(c)
        request = _session_request(a, c)
    endpoint = _events_endpoint(a)

    async def scenario():
        # 1) the response is built (slot taken) but never sent: the client is gone
        resp = await endpoint("run_gone", request)
        assert a.state.streams == 1
        await resp.body_iterator.aclose()  # what the server does with an unsent body
        sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            raise OSError("client went away")

        try:
            await resp({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        except Exception:  # noqa: BLE001, S110 - the disconnect surfaces as an error
            pass
        assert a.state.streams == 0
        # 2) disconnect right at the first send of a fresh stream
        resp2 = await endpoint("run_gone", request)
        try:
            await resp2({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        except Exception:  # noqa: BLE001, S110
            pass
        assert a.state.streams == 0

    asyncio.run(scenario())
    for _ in range(12):  # well past MAX_STREAMS: no slot leaked
        asyncio.run(_build_and_drop(endpoint, request))
    assert a.state.streams == 0


async def _build_and_drop(endpoint, request):
    resp = await endpoint("run_gone", request)
    await resp.body_iterator.aclose()
    del resp
