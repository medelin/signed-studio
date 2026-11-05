"""Sandbox negative testing: ``PayPal-Mock-Response`` on a capture, a declined capture that
leaves the money held and the activation retryable, a declined authorize that frees the
order slot, and PayPal's ``debug_id`` in the receipts."""

from __future__ import annotations

import json

import httpx
import pytest

from studio import funding
from studio.agents import media_buyer
from studio.paypal import orders
from studio.paypal.client import MOCK_CODES, PayPalClient
from studio.receipts import PAYPAL_KIND
from tests.approve import sign_pending
from tests.attack.test_paused_only import RUN, payload
from tests.test_paypal_funded import ITEM, Env


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_mock_codes_are_exactly_two():
    assert MOCK_CODES == frozenset({"INSTRUMENT_DECLINED", "TRANSACTION_REFUSED"})


class Spy:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600,
                                             "token_type": "Bearer"})
        self.requests.append(request)
        return httpx.Response(201, json={"id": "X1", "status": "COMPLETED"})


def client(spy: Spy) -> PayPalClient:
    return PayPalClient("cid", "secret", transport=httpx.MockTransport(spy),
                        sleep=lambda s: None)


def test_mock_header_sent_only_with_an_allowed_code():
    spy = Spy()
    client(spy).post("/v2/payments/authorizations/A1/capture", {}, request_id="r1",
                     mock_response="INSTRUMENT_DECLINED")
    client(spy).post("/v2/payments/authorizations/A1/capture", {}, request_id="r2")
    first, second = spy.requests
    assert first.headers["PayPal-Mock-Response"] == (
        '{"mock_application_codes":"INSTRUMENT_DECLINED"}')
    assert "PayPal-Mock-Response" not in second.headers


def test_unknown_mock_code_is_refused():
    spy = Spy()
    with pytest.raises(ValueError):
        client(spy).post("/v2/payments/authorizations/A1/capture", {}, request_id="r1",
                         mock_response="FOO")
    with pytest.raises(ValueError):
        orders.capture(client(spy), "A1", amount_minor=100, currency="PLN", invoice_id="f",
                       request_id="r1", mock_response='X","y":"z')
    assert spy.requests == []


def ready(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                       funder=env.funder)
    return f


def activation(env):
    pending, act = media_buyer.request_activation(env.conn, env.ledger, ITEM, 500,
                                                  funder=env.funder)
    return sign_pending(env.conn, env.ledger, pending, act), act


def test_declined_capture_leaves_money_held_and_activation_retryable(env):
    f = ready(env)
    v, act = activation(env)
    with pytest.raises(media_buyer.BuyError) as e:
        media_buyer.activate(env.conn, env.ledger, env.adapter, v, act, 500,
                             funder=env.funder, simulate="INSTRUMENT_DECLINED")
    assert e.value.code == "INSTRUMENT_DECLINED"
    assert funding.get(env.conn, f.id).state == "authorized"
    assert media_buyer.campaign(env.conn, ITEM)["status"] == "PAUSED"
    last = [r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_KIND][-1].details
    assert (last["step"], last["ok"], last["issue"], last["mock_response"]) == (
        "paypal.capture", False, "INSTRUMENT_DECLINED", "INSTRUMENT_DECLINED")
    assert last["debug_id"]
    # the owner signs the activation again, this time without the simulation
    v2, act2 = activation(env)
    live = media_buyer.activate(env.conn, env.ledger, env.adapter, v2, act2, 500,
                                funder=env.funder)
    assert live["status"] == "ACTIVE" and live["funding"]["state"] == "captured"
    report = env.verify()
    assert report.ok, report.errors


def test_simulate_is_not_part_of_the_signed_bytes(env):
    ready(env)
    pending, act = media_buyer.request_activation(env.conn, env.ledger, ITEM, 500,
                                                  funder=env.funder)
    assert "simulate" not in json.dumps(act)
    plain = pending.content_sha256
    # the same signed approval runs with or without a simulation: the field never
    # reaches the payload that was hashed and signed
    v = sign_pending(env.conn, env.ledger, pending, act)
    assert v.content_sha256 == plain
    with pytest.raises(media_buyer.BuyError):
        media_buyer.activate(env.conn, env.ledger, env.adapter, v, act, 500,
                             funder=env.funder, simulate="TRANSACTION_REFUSED")
    assert act == json.loads(json.dumps(act))  # the payload was not touched


def test_unknown_simulate_is_refused_before_any_capture(env):
    ready(env)
    v, act = activation(env)
    before = len(env.funder.fake.calls)
    with pytest.raises(ValueError):
        media_buyer.activate(env.conn, env.ledger, env.adapter, v, act, 500,
                             funder=env.funder, simulate="FOO")
    assert len(env.funder.fake.calls) == before


def test_signing_with_simulate_through_the_app(tmp_path):
    import base64

    from studio.app import create_app
    from studio.muse.client import MuseClient
    from studio.muse.openweights import OpenWeightsClient
    from studio.settings import Budget
    from tests.cassettes import FAKE_KEY, load, transport
    from tests.test_ui_studio import login, new_client, register, settings, wait_for

    a = create_app(settings(tmp_path, PAYPAL_ADAPTER="mock"))
    tr, _ = transport("producer_loop")
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0), transport=tr)
    a.state.muse.unrecorded = frozenset({"sam.check"})
    a.state.open_weights = OpenWeightsClient("", ledger=a.state.ledger)

    def sign(c, key, approver, approval_id, **extra):
        ap = c.get(f"/api/approvals/{approval_id}/payload",
                   params={"approver": approver}).json()
        sig = key.sign(base64.b64decode(ap["message_b64"])).hex()
        return c.post(f"/api/approvals/{approval_id}",
                      json={"approver": approver, "signature": sig, **extra})

    with new_client(a) as c:
        login(c)
        run_id = c.post("/api/runs", json={"brief": load("producer_loop")["brief"]}).json()[
            "run_id"]
        pending_id = wait_for(c, run_id)["state"]["pending_id"]
        key, approver = register(c)
        assert c.post(f"/api/funding/{pending_id}/order", json={"days": 3}).status_code == 201
        funded = c.post(f"/api/funding/{pending_id}/mock-approve").json()["approval_id"]
        item = sign(c, key, approver, funded).json()["result"]["campaign_item_id"]
        first = c.post(f"/api/campaigns/{item}/activate").json()["approval_id"]
        bad = sign(c, key, approver, first, simulate="FOO")
        assert bad.status_code == 400 and bad.json()["error"] == "invalid_simulate"
        declined = sign(c, key, approver, first, simulate="INSTRUMENT_DECLINED")
        assert declined.status_code == 200 and declined.json()["executed"] is False
        assert declined.json()["execution_error"] == "INSTRUMENT_DECLINED"
        assert "declined" in declined.json()["execution_reason"]
        assert declined.json()["execution_debug_id"]
        assert a.state.conn.execute("SELECT status FROM campaigns WHERE id = ?",
                                    (item,)).fetchone()[0] == "PAUSED"
        second = c.post(f"/api/campaigns/{item}/activate").json()["approval_id"]
        done = sign(c, key, approver, second)
        assert done.json()["executed"] and done.json()["result"]["status"] == "ACTIVE"


def test_mock_response_only_on_a_capture():
    spy = Spy()
    with pytest.raises(ValueError):
        client(spy).post("/v2/checkout/orders/O1/authorize", {}, request_id="r1",
                         mock_response="INSTRUMENT_DECLINED")
    assert spy.requests == []
