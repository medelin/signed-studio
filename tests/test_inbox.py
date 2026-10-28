"""Inbox routes (D3-2): read-only view, simulate, triage (draft only), signed reply."""

import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.muse.client import MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY
from tests.test_ui_studio import login, register, settings

TRIAGE = {"intent": "price", "sentiment": "neutral", "risk": "low", "requires_human": False,
          "draft": "A mug is 89 PLN; delivery is free above 150 PLN.",
          "reason": "asks for the price", "confidence": 0.9}


def _fake_transport(content):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 60}})

    return httpx.MockTransport(handler), sent


@pytest.fixture
def app(tmp_path):
    a = create_app(settings(tmp_path))
    tr, sent = _fake_transport(TRIAGE)
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=tr)
    a.state.sent = sent
    return a


def client(app):
    return TestClient(app, base_url="https://testserver")


def test_inbox_is_public_and_redacts_contact_data(app):
    with client(app) as c:
        r = c.get("/inbox")
        assert r.status_code == 200
        assert "ignore previous instructions" in r.text  # seeded demo comment
        assert "visitor.b@example.com" not in r.text and "[email]" in r.text
        assert 'src="/static/inbox.js"' in r.text
        data = c.get("/api/comments").json()["comments"]
        assert {x["id"] for x in data} >= {"c-1", "c-2", "c-5"}
        assert all(x["actions"] == ["draft", "reply.post", "comment.hide"] for x in data)


def test_writes_need_a_session(app):
    with client(app) as c:
        assert c.post("/api/comments/simulate", json={"text": "hi"}).status_code == 401
        assert c.post("/api/comments/c-1/triage").status_code == 401
        assert c.post("/api/comments/c-1/reply", json={"message": "x"}).status_code == 401


def test_triage_stores_a_draft_and_sends_nothing(app):
    with client(app) as c:
        login(c)
        r = c.post("/api/comments/c-1/triage")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "injection_suspected" in body["flags"]
        assert body["actions"] == ["draft", "reply.post", "comment.hide"]
        assert c.get("/api/comments").json()["comments"]
    kinds = [x.kind for x in app.state.ledger.receipts()]
    assert kinds == ["model_call", "tool_call"]
    assert app.state.ledger.receipts()[0].envelope
    assert app.state.conn.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


def test_reply_is_signed_over_the_exact_edited_text(app):
    with client(app) as c:
        login(c)
        key, approver = register(c)
        c.post("/api/comments/c-1/triage")
        edited = "Kubek kosztuje 89 PLN, dostawa gratis od 150 PLN."
        r = c.post("/api/comments/c-1/reply", json={"message": edited})
        assert r.status_code == 201
        approval_id = r.json()["approval_id"]
        p = c.get(f"/api/approvals/{approval_id}/payload?approver={approver}").json()
        assert p["content"]["message"] == edited and p["fields"]["action"] == "reply.post"
        sig = key.sign(base64.b64decode(p["message_b64"])).hex()
        r = c.post(f"/api/approvals/{approval_id}", json={"approver": approver,
                                                          "signature": sig})
        assert r.status_code == 200 and r.json()["action"] == "reply.post"
        assert r.json()["executed"] is True and r.json()["result"]["adapter"] == "mock"
        assert app.state.adapter.replies == {"c-1": [edited]}
        comments = {x["id"]: x for x in c.get("/api/comments").json()["comments"]}
        assert comments["c-1"]["status"] == "replied"
        (write,) = [x for x in app.state.ledger.receipts() if x.kind == "external_write"]
        assert write.details["approval_id"] == approval_id
        assert write.details["action"] == "reply.post" and write.details["item_id"] == "c-1"
        assert write.output_sha256 == p["content_sha256"]
        again = c.post(f"/api/approvals/{approval_id}", json={"approver": approver,
                                                              "signature": sig})
        assert again.status_code == 409


def test_simulate_adds_a_comment_and_bad_replies_are_refused(app):
    with client(app) as c:
        login(c)
        r = c.post("/api/comments/simulate", json={"text": "Czy wysyłacie do Gdańska?"})
        assert r.status_code == 201
        cid = r.json()["comment_id"]
        assert c.post("/api/comments/simulate", json={"text": ""}).status_code == 400
        assert c.post(f"/api/comments/{cid}/reply", json={"message": ""}).status_code == 400
        assert c.post(f"/api/comments/{cid}/reply",
                      json={"message": "x" * 601}).status_code == 400
        assert c.post("/api/comments/nope/reply", json={"message": "x"}).status_code == 400


LEAD_TRIAGE = {**TRIAGE, "intent": "lead", "draft": "Thanks! We will e-mail you today."}


def test_a_lead_comment_becomes_a_scored_crm_card(tmp_path):
    a = create_app(settings(tmp_path))

    def handler(request):
        body = json.loads(request.content)
        if "Rule score" in body["messages"][1]["content"]:
            content = {"score": 1, "temp": "COLD", "persona": "office buyer",
                       "reasons": ["asked for four mugs"], "next_action": "e-mail a quote"}
        else:
            content = LEAD_TRIAGE
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 60}})

    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(handler))
    with client(a) as c:
        login(c)
        r = c.post("/api/comments/c-2/triage")
        assert r.status_code == 200, r.text
        lead = r.json()["lead"]
        assert lead["score"] == 50 and lead["temp"] == "COLD"
        assert lead["persona"] == "office buyer"
        page = c.get("/leads").text
        assert "office buyer" in page and "visitor.b@example.com" in page
    with client(a) as anon:
        page = anon.get("/leads").text
        assert "visitor.b@example.com" not in page and "lead.one@example.com" not in page


def test_anonymous_views_redact_every_string_field_recursively(app):
    conn = app.state.conn
    with conn.lock:
        conn.execute(
            "UPDATE leads SET reasons = ?, persona = ?, next_action = ? WHERE id = 'l-1'",
            (json.dumps(["wrote from lead.one@example.com", "call +48 000 000 101"]),
             "reach me at lead.one@example.com", "ring 600 100 200"))
        conn.execute("UPDATE comments SET reason = 'quotes visitor.b@example.com', "
                     "flags = '[\"x 600 100 200\"]', draft = 'mail visitor.b@example.com' "
                     "WHERE id = 'c-2'")
    with client(app) as c:
        leads = c.get("/api/leads").json()["leads"]
        comments = c.get("/api/comments").json()["comments"]
        page = c.get("/leads").text + c.get("/inbox").text
    blob = json.dumps(leads + comments) + page
    for secret in ("lead.one@example.com", "visitor.b@example.com", "000 000 101",
                   "600 100 200"):
        assert secret not in blob
    l1 = next(x for x in leads if x["id"] == "l-1")
    assert l1["reasons"] == ["wrote from [email]", "call [phone]"]
    assert l1["email"] == "[email]" and l1["phone"] == "[phone]"
    with client(app) as c:  # with a session the CRM shows the data
        login(c)
        full = c.get("/api/leads").json()["leads"]
    assert next(x for x in full if x["id"] == "l-1")["email"] == "lead.one@example.com"


def test_poll_ingests_comments_from_the_adapter_once(app):
    from studio.meta.adapter import RemoteComment
    from studio.meta.mock import MockAdapter

    app.state.adapter = MockAdapter(comments=[RemoteComment(
        "300_400", "Czy macie zestaw 4 kubków?", "Demo Commenter", 1.0, "200_300")])
    with client(app) as c:
        assert c.post("/api/comments/poll", json={"object_id": "200_300"}).status_code == 401
        login(c)
        assert c.post("/api/comments/poll", json={"object_id": "../x"}).status_code == 400
        first = c.post("/api/comments/poll", json={"object_id": "200_300"}).json()
        second = c.post("/api/comments/poll", json={"object_id": "200_300"}).json()
    assert len(first["new"]) == 1 and second["new"] == []


def test_draft_with_a_unicode_phone_needs_a_human():
    from studio.agents import community
    from studio.agents.schemas import Triage

    t = Triage(intent="question", sentiment="neutral", risk="low", requires_human=False,
               draft="Zadzwoń: 600‑123‑456", reason="r", confidence=0.9)
    checked, flags = community.enforce(t, "Jak się skontaktować?")
    assert checked.requires_human and "pii_in_draft" in flags
