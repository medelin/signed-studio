"""The PayPal routes through the app (``PAYPAL_ADAPTER=mock``): a replayed brief run ends
with a campaign approval; the owner creates the PayPal order, the (simulated) buyer
approves it, the owner signs the approval that now carries the funding block, the
campaign is created PAUSED with the money held, and the activation captures it."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from studio import funding
from studio.app import create_app
from studio.muse.client import MuseClient
from studio.muse.openweights import OpenWeightsClient
from studio.receipts import PAYPAL_KIND, PAYPAL_WEBHOOK_KIND, verify
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, load, transport
from tests.test_ui_studio import login, new_client, register, settings, wait_for


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("paypal-ui")
    a = create_app(settings(tmp, PAYPAL_ADAPTER="mock"))
    tr, _ = transport("producer_loop")
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0), transport=tr)
    a.state.muse.unrecorded = frozenset({"sam.check"})
    a.state.open_weights = OpenWeightsClient("", ledger=a.state.ledger)
    with new_client(a) as c:
        login(c)
        r = c.post("/api/runs", json={"brief": load("producer_loop")["brief"]})
        run_id = r.json()["run_id"]
        data = wait_for(c, run_id)
    return a, run_id, data, tmp


def sign(c, key, approver, approval_id):
    ap = c.get(f"/api/approvals/{approval_id}/payload", params={"approver": approver}).json()
    sig = key.sign(base64.b64decode(ap["message_b64"])).hex()
    return ap, c.post(f"/api/approvals/{approval_id}", json={"approver": approver,
                                                            "signature": sig})


def test_pay_sign_activate(run):
    a, run_id, data, tmp = run
    pending_id = data["state"]["pending_id"]
    with new_client(a) as c:
        login(c)
        key, approver = register(c)
        cfg = c.get("/api/paypal/config").json()
        assert cfg == {"enabled": True, "adapter": "mock", "client_id": "", "fee_pct": "5",
                       "sandbox": True}
        # signing the unfunded approval executes nothing: no funding, no campaign
        order = c.post(f"/api/funding/{pending_id}/order", json={"days": 3})
        assert order.status_code == 201, order.text
        o = order.json()
        assert o["state"] == "created" and o["days"] == 3 and o["order_id"]
        f = funding.get(a.state.conn, o["funding_id"])
        assert o["total_minor"] == funding.quote(o["currency"], f.daily_budget_minor, 3,
                                                 funding.Decimal(5)).total_minor
        again = c.post(f"/api/funding/{pending_id}/order", json={"days": 9})
        assert again.json()["order_id"] == o["order_id"]  # one open order per ad
        early = c.post(f"/api/funding/{pending_id}/payer-approved")
        assert early.status_code == 409 and early.json()["error"] == "not_approved_yet"
        approved = c.post(f"/api/funding/{pending_id}/mock-approve")
        assert approved.status_code == 200, approved.text
        funded_id = approved.json()["approval_id"]
        assert funded_id != pending_id
        # the old approval is gone; the new one signs the funding with the ad
        stale, r = sign(c, key, approver, pending_id)
        assert r.status_code == 409 and r.json()["error"] == "superseded"
        ap, r = sign(c, key, approver, funded_id)
        assert ap["fields"]["funding_sha256"] == approved.json()["funding_sha256"]
        assert ap["content"]["funding"]["order_id"] == o["order_id"]
        assert b'"funding_sha256"' in base64.b64decode(ap["message_b64"])
        assert r.status_code == 200 and r.json()["executed"], r.text
        result = r.json()["result"]
        assert result["status"] == "PAUSED" and result["funding"]["state"] == "authorized"
        item = result["campaign_item_id"]
        act = c.post(f"/api/campaigns/{item}/activate")
        assert act.status_code == 201 and act.json()["content"]["paypal"]["amount_minor"] == \
            o["total_minor"]
        _, done = sign(c, key, approver, act.json()["approval_id"])
        assert done.status_code == 200, done.text
        assert done.json()["result"]["status"] == "ACTIVE"
        assert done.json()["result"]["funding"]["state"] == "captured"
        seen = c.get(f"/api/funding/item/{item}").json()["fundings"][0]
        assert [m["move"] for m in seen["moves"]] == ["order", "payer_approved",
                                                      "authorization", "capture"]
        registry = c.get("/api/approver-keys").json()
    kinds = [r.kind for r in a.state.ledger.receipts(run_id)]
    assert kinds.count(PAYPAL_KIND) == 4 and kinds.count(PAYPAL_WEBHOOK_KIND) == 2
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    path = tmp / "receipts.jsonl"
    path.write_text(a.state.ledger.export_jsonl())
    keys = {k: Ed25519PublicKey.from_public_bytes(bytes.fromhex(v)) for k, v in registry.items()}
    report = verify(path, a.state.ledger.public_key, approver_keys=keys)
    assert report.ok, report.errors


def test_an_unfunded_campaign_approval_creates_nothing(tmp_path):
    from tests.approve import approve
    from tests.attack.test_paused_only import RUN, payload

    a = create_app(settings(tmp_path, PAYPAL_ADAPTER="mock"))
    conn, ledger = a.state.conn, a.state.ledger
    ledger.append(RUN, agent="producer", kind="model_call")
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_nofund1", p, RUN)
    with pytest.raises(Exception) as e:
        a.state.executors["campaign.create"](v, p)
    assert getattr(e.value, "code", "") == "funding_required"


def test_routes_need_a_session_and_refuse_strangers(run):
    a, _, data, _ = run
    with TestClient(a, base_url="https://testserver") as c:
        assert c.get("/api/paypal/config").status_code == 401
        r = c.post(f"/api/funding/{data['state']['pending_id']}/order", json={},
                   headers={"origin": "https://testserver"})
        assert r.status_code in (401, 403)


def test_csp_opens_paypal_only_in_rest_mode(tmp_path):
    rest = create_app(settings(tmp_path / "r", PAYPAL_ADAPTER="rest",
                               PAYPAL_CLIENT_ID="sandbox-client-id",
                               PAYPAL_CLIENT_SECRET="sandbox-secret-value"))
    off = create_app(settings(tmp_path / "o"))
    with TestClient(rest, base_url="https://testserver") as c:
        csp = c.get("/healthz").headers["content-security-policy"]
        assert "https://www.paypal.com" in csp and "frame-ancestors 'none'" in csp
    with TestClient(off, base_url="https://testserver") as c:
        assert "paypal" not in c.get("/healthz").headers["content-security-policy"]


def test_csp_lets_the_save_my_paypal_form_redirect_to_paypal_only(tmp_path):
    # "Save my PayPal" is a form POST answered by a 303 to PayPal's approve link; Chromium
    # checks form-action on that redirect too, so 'self' alone left the owner on /account.
    rest = create_app(settings(tmp_path / "r", PAYPAL_ADAPTER="rest",
                               PAYPAL_CLIENT_ID="sandbox-client-id",
                               PAYPAL_CLIENT_SECRET="sandbox-secret-value"))
    with TestClient(rest, base_url="https://testserver") as c:
        csp = c.get("/healthz").headers["content-security-policy"]
        assert c.get("/healthz").headers["x-studio-csp"] == csp  # for the Apache vhost
    action = next(d for d in csp.split("; ") if d.startswith("form-action "))
    assert action.split()[1:] == ["'self'", "https://www.sandbox.paypal.com"]


def test_a_live_paypal_env_stops_the_start(tmp_path):
    from studio.settings import ConfigError

    with pytest.raises(ConfigError, match="sandbox only"):
        settings(tmp_path, PAYPAL_ADAPTER="mock", PAYPAL_ENV="live")


# -- who may see the money (security review, Day 2) -----------------------------------------


def test_another_account_cannot_see_or_refund_this_accounts_funding(tmp_path):
    """A run owned by one account (a brief sent for it): its funding and money moves are
    404 for another signed-in account, and visible to the owner."""
    import secrets
    import time as _time

    from studio.approvals import transaction
    from studio.receipts import canonical
    from tests.attack.test_paused_only import RUN, payload
    from tests.forms import csrf

    pw = "Lp4-" + secrets.token_hex(12)
    a = create_app(settings(tmp_path, PAYPAL_ADAPTER="mock"))
    conn, ledger, funder = a.state.conn, a.state.ledger, a.state.funder
    owner = a.state.accounts.create("ownerola", pw)
    a.state.accounts.create("strangerbo", pw)
    with transaction(conn):
        conn.execute("INSERT INTO briefs (id, run_id, text, created_ts, channel, account_id) "
                     "VALUES ('brf_owned1', ?, 'mug', ?, 'whatsapp', ?)",
                     (RUN, _time.time(), owner.id))
    ledger.append(RUN, agent="producer", kind="model_call")
    p = payload()
    from studio.approvals import Pending

    pending = Pending.create("campaign.create", "cmp_owned1", p, ledger=ledger, run_id=RUN)
    with transaction(conn):
        pending.save(conn)
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (pending.id, RUN, canonical(p).decode(), 0.0))
    step = funder.money.open(run_id=RUN, item_id="cmp_owned1",
                             content_sha256=pending.content_sha256, currency="PLN",
                             daily_budget_minor=3000, days=1, fee_pct=funding.Decimal(5))

    def login_as(c, user):
        r = c.post("/login", data={"username": user, "password": pw, "csrf": csrf(c)})
        assert r.status_code in (200, 303), r.text

    with new_client(a) as c:
        login_as(c, "strangerbo")
        assert c.get("/api/funding/item/cmp_owned1").status_code == 404
        r = c.post(f"/api/funding/{step.funding.id}/refund", json={"amount_minor": 100})
        assert r.status_code == 404
        r = c.post(f"/api/funding/{pending.id}/order", json={})
        assert r.status_code == 404
        assert c.get("/api/funding/item/cmp_nosuch1").status_code == 404
    with new_client(a) as c:
        login_as(c, "ownerola")
        r = c.get("/api/funding/item/cmp_owned1")
        assert r.status_code == 200 and r.json()["fundings"][0]["funding_id"] == step.funding.id
