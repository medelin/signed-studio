"""Attack suite (day 3 fix round, I5 + I7): what a visitor without a session can read.

- ``GET /api/campaigns/{id}``: no raw Meta object ids and no Meta preview URL (it carries
  a short-lived token); the mock's inline preview has nothing to hide and stays.
- ``/api/comments``, ``/api/leads`` and their pages: no real Facebook names, only
  initials (or "Facebook user")."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from studio import db
from studio.agents import community
from studio.app import create_app
from tests.test_ui_studio import login, settings

PREVIEW = ("https://www.facebook.com/ads/api/preview_iframe.php?d=AQKsecretpreview"
           "&t=AQLsecrettoken")
CAMPAIGN_ID, ADSET_ID, AD_ID = "120200000000000101", "120200000000000202", "120200000000000404"


@pytest.fixture
def app(tmp_path):
    a = create_app(settings(tmp_path))
    conn = a.state.conn
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO campaigns (id, run_id, name, objective, status, daily_budget_minor, "
            "adapter, external_id, adset_external_id, ad_external_id, created_ts) VALUES "
            "('cmp_guest1', 'run_g1', 'Muse Studio · Slow', 'OUTCOME_TRAFFIC', 'PAUSED', 3000,"
            " 'graph', ?, ?, ?, ?)", (CAMPAIGN_ID, ADSET_ID, AD_ID, time.time()))
        conn.execute(
            "INSERT INTO executions (approval_id, action, item_id, run_id, status, result, "
            "created_ts, updated_ts) VALUES ('a' || hex(randomblob(15)), 'campaign.create', "
            "'cmp_guest1', 'run_g1', 'done', ?, 0, 0)",
            (json.dumps({"campaign_id": CAMPAIGN_ID, "adset_id": ADSET_ID, "ad_id": AD_ID,
                         "preview": {"kind": "iframe", "src": PREVIEW, "fields": {}}}),))
    community.ingest(conn, "300000000000001_400000000000009", "Where do you ship?",
                     "Joanna Kowalska-Nowak", time.time(), "200000000000001_300000000000001")
    return a


def _client(app):
    return TestClient(app, base_url="https://testserver")


def test_guest_campaign_view_has_no_meta_ids_or_preview_url(app):
    with _client(app) as c:
        r = c.get("/api/campaigns/cmp_guest1")
        assert r.status_code == 200
        body = r.text
        for secret in (CAMPAIGN_ID, ADSET_ID, AD_ID, "AQLsecrettoken", "AQKsecretpreview"):
            assert secret not in body, secret
        data = r.json()
        assert data["status"] == "PAUSED" and data["preview"] is None
        assert data["external_id"].startswith("sha256:")
        login(c)
        full = c.get("/api/campaigns/cmp_guest1").json()
        assert full["external_id"] == CAMPAIGN_ID and full["preview"]["src"] == PREVIEW


def test_guest_sees_initials_not_facebook_names(app):
    conn = app.state.conn
    (cid,) = [r[0] for r in conn.execute(
        "SELECT id FROM comments WHERE author_name = 'Joanna Kowalska-Nowak'")]
    with _client(app) as c:
        for path in ("/api/comments", "/inbox"):
            assert "Joanna" not in c.get(path).text and "Kowalska" not in c.get(path).text
        comments = c.get("/api/comments").json()["comments"]
        (mine,) = [x for x in comments if x["id"] == cid]
        assert mine["author_name"] == "J. K."
        with db.transaction(conn):
            conn.execute("INSERT INTO leads (id, name, email, phone, source, message, "
                         "comment_id, created_ts) VALUES ('l-guest1', "
                         "'Joanna Kowalska-Nowak', '', '', 'comment', 'x', ?, ?)",
                         (cid, time.time()))
        for path in ("/api/leads", "/leads"):
            assert "Joanna" not in c.get(path).text and "Kowalska" not in c.get(path).text
        login(c)
        full = c.get("/api/comments").json()["comments"]
        assert [x for x in full if x["id"] == cid][0]["author_name"] == (
            "Joanna Kowalska-Nowak")
        leads = c.get("/api/leads").json()["leads"]
        assert [x for x in leads if x["id"] == "l-guest1"][0]["name"] == (
            "Joanna Kowalska-Nowak")


def test_initials_of_odd_names():
    from studio.routes_inbox import initials
    assert initials("") == "Facebook user"
    assert initials("   ") == "Facebook user"
    assert initials("Ola") == "O."
    assert initials("Łukasz Żak") == "Ł. Ż."
    assert initials("123 456 789") == "Facebook user"  # no letters: nothing to keep
    assert initials("Anna Maria Wiśniewska Kowal") == "A. M."
