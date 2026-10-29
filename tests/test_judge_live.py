"""The shared judge account on a live Meta adapter: it may create paused campaigns (the
real objects judges can inspect), but it cannot turn one on, change one, or post or hide
anything on the Page. The refusal comes before the signature is consumed; on the mock
adapter nothing is real, so judges keep the whole flow."""

import time

import pytest
from fastapi.testclient import TestClient

from studio import db
from studio.app import create_app
from studio.approvals import Pending
from studio.receipts import canonical
from studio import leadads
from studio.meta import objectives
from studio.meta.adapter import MOCK_PAGE
from studio.routes_studio import demo_destinations_ok, demo_link_ok, demo_refused
from tests.test_pixel_gate import RUN, _sign
from tests.test_ui_studio import PASSWORD, csrf, login, register, settings

LIVE = {"META_ADAPTER": "graph", "META_ACCESS_TOKEN": "t" * 40,
        "META_AD_ACCOUNT_ID": "act_1", "META_PAGE_ID": "2",
        "META_LANDING_URL": "https://muse.example/"}
PAYLOAD = {"action": "campaign.activate", "campaign_id": "cmp_j1", "run_id": RUN}


def _pending(app, action):
    conn, ledger = app.state.conn, app.state.ledger
    ledger.append(RUN, agent="producer", kind="model_call")
    p = {**PAYLOAD, "action": action}
    pending = Pending.create(action, "cmp_j1", p, ledger=ledger, run_id=RUN)
    with db.transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (pending.id, RUN, canonical(p).decode(),
                                             time.time()))
    return pending.id


@pytest.mark.parametrize("action,meta,google,refused", [
    ("campaign.create", "graph", "mock", False),
    ("campaign.activate", "graph", "mock", True),
    ("campaign.update", "graph", "mock", True),
    ("reply.post", "graph", "mock", True),
    ("comment.hide", "graph", "mock", True),
    ("campaign.activate", "mock", "mock", False),
    ("google.create", "mock", "rest", False),
    ("google.activate", "mock", "rest", True),
    ("google.activate", "graph", "mock", False),
    ("campaign.something_new", "graph", "mock", True),
    ("google.something_new", "mock", "rest", True),
])
def test_what_the_judge_account_may_sign(action, meta, google, refused):
    assert demo_refused(action, meta_adapter=meta, google_adapter=google) is refused


@pytest.mark.parametrize("action", ["campaign.activate", "campaign.update", "reply.post"])
def test_judge_cannot_sign_a_live_write_and_nothing_is_consumed(tmp_path, action):
    app = create_app(settings(tmp_path, **LIVE))
    ran = []
    app.state.executors[action] = lambda v, p: ran.append(action)
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        key, approver = register(c)
        aid = _pending(app, action)
        _, r = _sign(c, key, approver, aid)
        assert r.status_code == 403 and r.json() == {"error": "demo_live_write"}
        conn = app.state.conn
        assert conn.execute("SELECT status FROM approvals WHERE id = ?",
                            (aid,)).fetchone()[0] == "pending"
        assert conn.execute("SELECT count(*) FROM nonces").fetchone()[0] == 0
        assert ran == []


def test_the_owner_account_signs_the_same_activation(tmp_path):
    app = create_app(settings(tmp_path, **LIVE))
    ran = []
    app.state.executors["campaign.activate"] = lambda v, p: ran.append(v.action) or {}
    app.state.accounts.create("ola", PASSWORD)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/login", data={"username": "ola", "password": PASSWORD,
                                   "csrf": csrf(c)})
        assert r.status_code == 200
        key, approver = register(c)
        aid = _pending(app, "campaign.activate")
        _, r = _sign(c, key, approver, aid)
        assert r.status_code == 200, r.text
        assert ran == ["campaign.activate"]


@pytest.mark.parametrize("link,ok", [
    ("https://muse.example/", True),
    ("https://muse.example/try?utm=judge", True),
    ("https://evil.example/", False),
    ("http://muse.example/", False),
    ("https://muse.example.evil.example/", False),
    ("https://user@muse.example/", False),
    ("https://muse.example:8443/", False),
    (None, False),
])
def test_a_judge_live_campaign_links_only_to_the_landing_host(link, ok):
    assert demo_link_ok(link, LIVE["META_LANDING_URL"]) is ok


def test_judge_cannot_sign_a_live_campaign_aimed_elsewhere(tmp_path):
    app = create_app(settings(tmp_path, **LIVE))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        key, approver = register(c)
        conn, ledger = app.state.conn, app.state.ledger
        ledger.append(RUN, agent="producer", kind="model_call")
        p = {"action": "campaign.create", "run_id": RUN, "link": "https://evil.example/"}
        pending = Pending.create("campaign.create", "cmp_j2", p, ledger=ledger, run_id=RUN)
        with db.transaction(conn):
            pending.save(conn)
            conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, "
                         "created_ts) VALUES (?, ?, ?, ?)",
                         (pending.id, RUN, canonical(p).decode(), time.time()))
        _, r = _sign(c, key, approver, pending.id)
        assert r.status_code == 403 and r.json() == {"error": "demo_landing"}
        assert conn.execute("SELECT count(*) FROM nonces").fetchone()[0] == 0


HOME = LIVE["META_LANDING_URL"]


def _traffic(link):
    return {"delivery": objectives.delivery("traffic", page_id=MOCK_PAGE), "link": link}


def _form(privacy, follow_up):
    spec = objectives.delivery("leads", "instant_form", page_id=MOCK_PAGE,
                               promotion={"privacy_policy_url": privacy})
    return {"delivery": spec, "lead_form": leadads.form_spec("form", spec["lead_fields"],
                                                             privacy, follow_up)}


@pytest.mark.parametrize("payload,ok", [
    (_traffic(HOME + "try"), True),
    (_traffic("https://evil.example/"), False),
    ({"link": HOME}, False),  # no valid delivery block
    (_form(HOME + "privacy", HOME), True),
    (_form("https://evil.example/privacy", HOME), False),
    (_form(HOME + "privacy", "https://evil.example/"), False),
])
def test_every_destination_of_a_judge_live_campaign_is_checked(payload, ok):
    assert demo_destinations_ok(payload, HOME) is ok
