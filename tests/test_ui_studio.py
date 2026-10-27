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

    js = (UI / "static" / "app.js").read_text()
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
    with new_client(a) as c, c.stream("GET", f"/api/runs/{run_id}/events") as r:
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
    assert len(approvals) == 1 and approvals[0]["details"]["sig"] == sig


def test_malformed_approval_body_is_400(finished):
    a, _, data = finished
    with new_client(a) as c:
        login(c)
        r = c.post(f"/api/approvals/{data['state']['pending_id']}",
                   json={"approver": "x", "signature": "zz"})
    assert r.status_code == 400
