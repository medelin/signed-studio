"""Google Search through the app: the routes, the Report rows in both languages, the
brief form's channel fields and the currency check on confirmation."""

import base64
import json

from fastapi.testclient import TestClient

from studio.agents import google_buyer
from studio.app import create_app
from studio.google import store
from tests.approve import approve
from tests.google import RUN, payload
from tests.test_ui_studio import login, register, settings


def _app(tmp_path, **extra):
    app = create_app(settings(tmp_path, **{"GOOGLE_ADS_ACCOUNT_CURRENCY": "GBP", **extra}))
    app.state.ledger.append(RUN, agent="producer", kind="model_call")
    return app


def _created(app):
    conn, ledger = app.state.conn, app.state.ledger
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    google_buyer.create(conn, ledger, app.state.google, v, p)
    return v.item_id


def test_report_shows_google_rows_in_both_languages(tmp_path):
    app = _app(tmp_path)
    _created(app)
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        pulled = c.post("/api/insights/pull", json={}).json()
        assert pulled["google_campaigns"] == 1
        en = c.get("/report?lang=en").text
        pl = c.get("/report?lang=pl").text
    assert "Google Search" in en and "Impressions" in en and "demo fixture, not Google data" in en
    assert "Wyszukiwarka Google" in pl and "Wyświetlenia" in pl
    assert "2026" in en and "Slow Ceramics" in en


def test_a_campaign_is_read_and_a_visitor_sees_no_resource_names(tmp_path):
    app = _app(tmp_path)
    item = _created(app)
    with TestClient(app, base_url="https://testserver") as visitor:
        seen = visitor.get(f"/api/google/{item}").json()
    assert seen["status"] == "PAUSED" and seen["daily_budget_micros"] == 6_000_000
    assert all(v.startswith("sha256:") for v in seen["resources"].values())
    assert "customer_id" not in seen
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        own = c.get(f"/api/google/{item}").json()
    assert own["resources"]["campaign"].startswith("customers/")
    assert c.get("/api/google/nope!").status_code in (404, 422)


def test_activation_is_signed_in_the_app(tmp_path):
    app = _app(tmp_path)
    item = _created(app)
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post(f"/api/google/{item}/activate", json={}).status_code == 401
        login(c)
        key, approver = register(c)
        r = c.post(f"/api/google/{item}/activate", json={})
        assert r.status_code == 201 and r.json()["content"]["action"] == "google.activate"
        aid = r.json()["approval_id"]
        msg = c.get(f"/api/approvals/{aid}/payload", params={"approver": approver}).json()
        sig = key.sign(base64.b64decode(msg["message_b64"])).hex()
        done = c.post(f"/api/approvals/{aid}", json={"approver": approver, "signature": sig})
        assert done.status_code == 200 and done.json()["executed"] is True
        assert done.json()["result"]["status"] == "ENABLED"
        assert c.post(f"/api/google/{item}/activate", json={}).status_code == 409
    assert store.campaign(app.state.conn, item)["status"] == "ENABLED"


def test_the_studio_page_offers_the_channel_in_both_languages(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        en = c.get("/studio?lang=en").text
        pl = c.get("/studio?lang=pl").text
        help_en = c.get("/help?lang=en").text
        help_pl = c.get("/help?lang=pl").text
    assert 'data-path="channels.google_search"' in en
    assert 'data-path="channels.google_daily_budget"' in en
    assert "Also run on Google Search" in en and "Pokaż też w Google" in pl
    assert 'id="google-panel"' in en
    assert "Can the same brief also run on Google Search?" in help_en
    assert "Czy ten sam brief może działać też w wyszukiwarce Google?" in help_pl


def test_confirming_a_brief_checks_the_google_currency(tmp_path):
    app = _app(tmp_path, GOOGLE_ADS_ACCOUNT_CURRENCY="EUR", STUDIO_ACCOUNT_CURRENCY="GBP")
    from studio import brief as bv
    from tests.test_brief import sample

    brief = sample().model_copy(update={"channels": bv.Channels(
        google_search=True, google_daily_budget=6)})
    assert app.state.google.account_info()["currency"] == "EUR"
    probs = bv.problems(brief, currency="GBP", google_currency="EUR", ceiling_daily=50)
    assert probs["budget.currency"] == "google_account_currency"
    json.dumps(probs)
