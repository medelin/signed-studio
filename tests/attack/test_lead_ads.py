"""Lead ads end to end: the instant form is part of the signed payload and names the
owner's own privacy page; its leads (polled, or pushed by a signed webhook) land in the
lead store once and are scored by the rules; receipts never carry contact data; an
unsigned or wrongly signed webhook is refused."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from studio import db, leadads
from studio.agents import lead_scorer, media_buyer
from studio.app import create_app
from studio.approvals import Keys
from studio.meta import objectives
from studio.meta.adapter import MOCK_PAGE, NotApproved
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve
from tests.attack.test_paused_only import PNG, RUN, payload
from tests.test_ui_studio import login, settings

PRIVACY = "https://example.com/privacy"
SECRET = "app-secret-for-tests"  # noqa: S105


def lead_payload(**over):
    spec = objectives.delivery("leads", "instant_form", page_id=MOCK_PAGE,
                               promotion={"privacy_policy_url": PRIVACY,
                                          "lead_fields": ["FULL_NAME", "EMAIL", "PHONE",
                                                          "CITY"]})
    p = payload(delivery=spec, campaign={**payload()["campaign"],
                                         "objective": "OUTCOME_LEADS"},
                lead_form=leadads.form_spec("Muse Studio · form", spec["lead_fields"],
                                            PRIVACY, "https://example.com/"))
    p.update(over)
    return p


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    assets = tmp_path / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, MockAdapter(), tmp_path


def test_form_spec_uses_the_owners_page_and_adds_nothing():
    form = leadads.form_spec("x", ["FULL_NAME", "EMAIL"], "https://shop.example/legal/privacy/",
                             "https://shop.example/")
    assert form["privacy_policy"] == {"url": "https://shop.example/legal/privacy/",
                                      "link_text": "shop.example/legal/privacy"}
    assert [q["type"] for q in form["questions"]] == ["FULL_NAME", "EMAIL"]
    assert set(form) == {"name", "questions", "privacy_policy", "follow_up_action_url"}
    with pytest.raises(ValueError):
        leadads.form_spec("x", ["EMAIL"], "", "https://shop.example/")


def test_instant_form_campaign_then_poll_scores_each_lead_once(env, tmp_path):
    conn, ledger, mock, data = env
    p = lead_payload()
    v = approve(conn, ledger, "campaign.create", "cmp_lead1", p, RUN)
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    assert out["status"] == "PAUSED"
    form_id = out["lead_form_id"]
    form = mock.objects[form_id]
    assert form["privacy_policy"]["url"] == PRIVACY
    creative = next(o for o in mock.objects.values() if o.get("type") == "creative")
    link = creative["body"]["object_story_spec"]["link_data"]
    assert link["link"] == "https://fb.me/"
    assert link["call_to_action"] == {"type": "SIGN_UP", "value": {"lead_gen_form_id": form_id}}
    steps = [r.details["tool"] for r in ledger.receipts(RUN) if r.agent == "media_buyer"
             and r.kind == "tool_call"]
    assert steps == ["image", "campaign", "adset", "lead_form", "creative", "ad"]

    first = leadads.poll(conn, ledger, mock)
    again = leadads.poll(conn, ledger, mock)
    assert first == {"forms": 1, "fetched": 3, "new": 3}
    assert again["new"] == 0
    with conn.lock:
        rows = conn.execute("SELECT id, source, email, phone, city, score, temp FROM leads "
                            "WHERE source = 'lead_form' ORDER BY created_ts").fetchall()
    assert len(rows) == 3
    one = rows[0]
    assert one[2] == "one@example.com" and one[3] == "+48 000 000 101" and one[4] == "Kraków"
    assert one[5] == lead_scorer.rules({"email": one[2], "phone": one[3], "city": one[4]}).score
    export = ledger.export_jsonl()
    for secret in ("one@example.com", "000 000 101", "Demo Lead One"):
        assert secret not in export  # contact data stays in the lead store
    path = tmp_path / "e.jsonl"
    path.write_text(export)
    assert verify(path, ledger.public_key, approver_keys=Keys.registry(conn)).ok


def test_a_lead_payload_without_the_form_or_privacy_page_calls_nothing(env):
    conn, ledger, mock, data = env
    for bad in (lead_payload(lead_form=None),
                lead_payload(lead_form={**lead_payload()["lead_form"], "privacy_policy": {}})):
        if bad["lead_form"] is None:
            del bad["lead_form"]
        v = approve(conn, ledger, "campaign.create", "cmp_lead_bad", bad, RUN)
        with pytest.raises(NotApproved):
            mock.create_lead_form(bad, v)
    assert [c for c in mock.calls if c[0] == "create_lead_form"] == []


def test_field_mapping_to_the_scorer_inputs():
    lead = leadads.RemoteLead("1", "2", 0.0, {"full_name": "A B", "email": "a@example.com",
                                              "company_name": "Shop Ltd", "city": "Leeds"})
    row = leadads.lead_row(lead)
    assert row == {"name": "A B", "email": "a@example.com", "phone": "", "city": "Leeds",
                   "company": "Shop Ltd", "country": "", "kind": "business"}
    hits = lead_scorer.rules(row).hits
    assert "+15 UK" in hits and "+10 business" in hits


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(tmp_path):
    app = create_app(settings(tmp_path, META_APP_SECRET=SECRET,
                              META_WEBHOOK_VERIFY_TOKEN="verify-me"))
    with TestClient(app, base_url="https://testserver") as c:
        yield app, c


def _event(form_id: str, lead_id: str) -> bytes:
    return json.dumps({"object": "page", "entry": [{"id": MOCK_PAGE, "changes": [
        {"field": "leadgen", "value": {"leadgen_id": lead_id, "form_id": form_id,
                                       "page_id": MOCK_PAGE}}]}]}).encode()


def test_webhook_refuses_unsigned_and_wrongly_signed_requests(client):
    app, c = client
    body = _event("mock-form-1", "x")
    assert c.post("/api/meta/webhook", content=body).status_code == 403
    assert c.post("/api/meta/webhook", content=body,
                  headers={"X-Hub-Signature-256": _sign(body, "other")}).status_code == 403
    assert c.post("/api/meta/webhook", content=body,
                  headers={"X-Hub-Signature-256": "sha1=abc"}).status_code == 403
    ok = c.post("/api/meta/webhook", content=body,
                headers={"X-Hub-Signature-256": _sign(body)})
    assert ok.status_code == 200 and ok.json() == {"received": 0}  # not a form of ours


def test_webhook_without_an_app_secret_refuses_everything(tmp_path):
    app = create_app(settings(tmp_path))
    with TestClient(app, base_url="https://testserver") as c:
        body = _event("f", "l")
        r = c.post("/api/meta/webhook", content=body, headers={"X-Hub-Signature-256":
                                                              _sign(body, "")})
        assert r.status_code == 403


def test_signed_webhook_for_our_form_ingests_the_lead_once(client):
    app, c = client
    mock = app.state.adapter
    conn = app.state.conn
    form_id = "mock-form-0a1b2c3d4e"  # as the media buyer made it (see the test above)
    mock.objects[form_id] = {"type": "lead_form"}
    leadads.record_form(conn, form_id, "cmp_hook1", RUN, mock.name)
    lead_id = f"lead-demo-2-{form_id[-6:]}"
    body = _event(form_id, lead_id)
    for _ in range(2):
        r = c.post("/api/meta/webhook", content=body,
                   headers={"X-Hub-Signature-256": _sign(body)})
        assert r.status_code == 200
    with conn.lock:
        rows = conn.execute("SELECT company, kind FROM leads WHERE external_id = ?",
                            (lead_id,)).fetchall()
    assert rows == [("Demo Office", "business")]


def test_webhook_subscription_check(client):
    _, c = client
    ok = c.get("/api/meta/webhook", params={"hub.mode": "subscribe",
                                            "hub.verify_token": "verify-me",
                                            "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = c.get("/api/meta/webhook", params={"hub.mode": "subscribe",
                                             "hub.verify_token": "nope",
                                             "hub.challenge": "1"})
    assert bad.status_code == 403


def test_poll_endpoint_needs_a_session(client):
    _, c = client
    assert c.post("/api/leadforms/poll", json={}).status_code == 401
    login(c)
    assert c.post("/api/leadforms/poll", json={}).json() == {"forms": 0, "fetched": 0,
                                                             "new": 0}
