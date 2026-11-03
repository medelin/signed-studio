"""PayPal client: sandbox-only config, OAuth token cache, idempotency keys, retries, error
mapping, and parsing of PayPal's documented answers (fixtures/cassettes, synthetic)."""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import httpx
import pytest

from studio.funding import quote
from studio.paypal import SANDBOX_BASE, PayPalConfig, SandboxOnly, fee_pct_of, orders
from studio.paypal.client import (
    REQUEST_ID_MAX,
    PayPalClient,
    PayPalError,
    path_id,
    request_id,
)
from tests.cassettes import load

CID, SECRET = "client-id-test", "client-secret-test-value"
H = "9b2f4c1d8e7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c"


def fixture(name: str) -> dict:
    for i in load("paypal/orders.SYNTHETIC")["interactions"]:
        if i["name"] == name:
            return i
    raise KeyError(name)


class Recorder:
    """A transport that answers from a script and records every request."""

    def __init__(self, *answers: tuple[int, dict]) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": f"tok{len(self.requests)}",
                                             "expires_in": 3600, "token_type": "Bearer"})
        status, body = self.answers.pop(0)
        return httpx.Response(status, json=body)

    def calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/v1/oauth2/token"]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def client(rec: Recorder, clock: Clock | None = None) -> PayPalClient:
    return PayPalClient(CID, SECRET, transport=httpx.MockTransport(rec),
                        clock=clock or Clock(), sleep=lambda s: None)


# -- config -------------------------------------------------------------------------------


def test_config_defaults_to_off_with_a_five_percent_fee():
    c = PayPalConfig.from_env({})
    assert not c.enabled and c.fee_pct == Decimal(5) and c.fee_pct_text == "5"


@pytest.mark.parametrize("env", ["live", "production", "LIVE", "prod"])
def test_a_live_environment_refuses_to_start(env):
    with pytest.raises(SandboxOnly, match="sandbox only"):
        PayPalConfig.from_env({"PAYPAL_ENV": env, "PAYPAL_ADAPTER": "mock"})


def test_the_base_url_is_the_sandbox_and_cannot_be_chosen():
    assert SANDBOX_BASE == "https://api-m.sandbox.paypal.com"
    assert PayPalClient.base == SANDBOX_BASE
    with pytest.raises(TypeError):
        PayPalClient(CID, SECRET, base_url="https://api-m.paypal.com")  # type: ignore[call-arg]


def test_rest_needs_credentials_and_hides_the_secret():
    with pytest.raises(ValueError, match="PAYPAL_CLIENT_ID"):
        PayPalConfig.from_env({"PAYPAL_ADAPTER": "rest"})
    c = PayPalConfig.from_env({"PAYPAL_ADAPTER": "rest", "PAYPAL_CLIENT_ID": CID,
                               "PAYPAL_CLIENT_SECRET": SECRET, "PAYPAL_ENV": "sandbox"})
    assert c.enabled and SECRET not in repr(c) and CID not in repr(c)
    assert SECRET not in repr(PayPalClient(CID, SECRET))


@pytest.mark.parametrize("raw,ok", [("5", Decimal(5)), ("2.5", Decimal("2.5")), ("0", 0),
                                    ("30", 30), ("31", None), ("-1", None), ("5%", None),
                                    ("1.234", None), ("NaN", None)])
def test_fee_percentage(raw, ok):
    if ok is None:
        with pytest.raises(ValueError):
            fee_pct_of(raw)
    else:
        assert fee_pct_of(raw) == Decimal(ok)


def test_bad_adapter_and_webhook_id_are_refused():
    with pytest.raises(ValueError):
        PayPalConfig.from_env({"PAYPAL_ADAPTER": "live"})
    with pytest.raises(ValueError):
        PayPalConfig.from_env({"PAYPAL_WEBHOOK_ID": "../x"})


# -- OAuth --------------------------------------------------------------------------------


def test_token_is_fetched_once_with_basic_auth_and_cached():
    rec = Recorder((200, {}), (200, {}))
    c = client(rec)
    c.get("/v2/checkout/orders/ABC")
    c.get("/v2/checkout/orders/ABC")
    tokens = [r for r in rec.requests if r.url.path == "/v1/oauth2/token"]
    assert len(tokens) == 1
    assert tokens[0].headers["Authorization"] == "Basic " + base64.b64encode(
        f"{CID}:{SECRET}".encode()).decode()
    assert tokens[0].content == b"grant_type=client_credentials"
    assert all(str(r.url).startswith(SANDBOX_BASE) for r in rec.requests)


def test_token_is_refreshed_sixty_seconds_before_it_expires():
    rec, clock = Recorder((200, {}), (200, {}), (200, {})), Clock()
    c = client(rec, clock)
    c.get("/x")
    clock.t += 3600 - 61
    c.get("/x")
    clock.t += 2  # now inside the 60 s margin
    c.get("/x")
    assert sum(r.url.path == "/v1/oauth2/token" for r in rec.requests) == 2


def test_a_401_drops_the_token_and_retries_once():
    rec = Recorder((401, {"name": "AUTHENTICATION_FAILURE"}), (200, {"ok": 1}))
    assert client(rec).get("/x").json == {"ok": 1}
    assert sum(r.url.path == "/v1/oauth2/token" for r in rec.requests) == 2
    rec = Recorder((401, {}), (401, {}))
    with pytest.raises(PayPalError) as e:
        client(rec).get("/x")
    assert e.value.status == 401


def test_bad_client_credentials_raise_without_the_secret():
    i = fixture("token_invalid_client")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(i["response"]["status"], json=i["response"]["json"])

    c = PayPalClient(CID, SECRET, transport=httpx.MockTransport(handler))
    with pytest.raises(PayPalError) as e:
        c.access_token()
    assert e.value.status == 401 and e.value.name == "INVALID_CLIENT"
    assert SECRET not in str(e.value) and CID not in str(e.value)


# -- writes -------------------------------------------------------------------------------


def test_every_write_carries_a_request_id_and_prefers_the_representation():
    rec = Recorder((201, {"id": "X"}))
    c = client(rec)
    c.post("/v2/checkout/orders", {"a": 1}, request_id="ss-abc")
    (r,) = rec.calls()
    assert r.headers["PayPal-Request-Id"] == "ss-abc"
    assert r.headers["Prefer"] == "return=representation"
    assert r.headers["Authorization"].startswith("Bearer ")
    assert json.loads(r.content) == {"a": 1}
    with pytest.raises(ValueError):
        c.post("/x", {}, request_id="")
    with pytest.raises(ValueError):
        c.post("/x", {}, request_id="x" * (REQUEST_ID_MAX + 1))


def test_transient_errors_are_retried_with_the_same_request_id():
    rec = Recorder((503, {}), (500, {}), (201, {"id": "OK"}))
    assert client(rec).post("/x", {}, request_id="ss-same").json == {"id": "OK"}
    assert [r.headers["PayPal-Request-Id"] for r in rec.calls()] == ["ss-same"] * 3


def test_retries_end_in_a_transient_error():
    rec = Recorder(*[(503, {})] * 4)
    with pytest.raises(PayPalError) as e:
        client(rec).post("/x", {}, request_id="ss-1")
    assert e.value.transient and e.value.status == 503


def test_a_network_error_after_sending_a_write_is_marked_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise httpx.ReadTimeout("slow", request=request)

    c = PayPalClient(CID, SECRET, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(PayPalError) as e:
        c.post("/x", {}, request_id="ss-1")
    assert e.value.unknown and e.value.transient and e.value.name == "NETWORK_ERROR"


def test_unprocessable_maps_the_issue_to_a_reason_and_keeps_the_body_out():
    i = fixture("authorize_not_approved")
    rec = Recorder((i["response"]["status"], i["response"]["json"]))
    with pytest.raises(PayPalError) as e:
        client(rec).post(i["request"]["path"], None, request_id="ss-1")
    err = e.value
    assert (err.status, err.name, err.issue) == (422, "UNPROCESSABLE_ENTITY",
                                                 "ORDER_NOT_APPROVED")
    assert err.reason == "the buyer has not approved the order in PayPal yet"
    assert err.debug_id == "f8d2a1b3c4e5" and not err.transient
    assert "redirect the payer" not in str(err)  # PayPal's free text stays out


def test_request_ids_are_stable_distinct_and_short():
    a = request_id("fund_1", "capture", "apr_1")
    assert a == request_id("fund_1", "capture", "apr_1")
    assert a != request_id("fund_1", "capture", "apr_2")
    assert a != request_id("fund_1capture", "apr_1")
    assert len(a) <= REQUEST_ID_MAX and a.startswith("ss-")
    with pytest.raises(ValueError):
        request_id("fund_1", "")


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "a?b=c", "x" * 65])
def test_path_ids_are_checked(bad):
    with pytest.raises(ValueError):
        path_id(bad)


# -- PayPal's documented answers, parsed ---------------------------------------------------


def replay(*names: str) -> tuple[PayPalClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    script = [fixture(n) for n in names]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            t = fixture("token")["response"]
            return httpx.Response(t["status"], json=t["json"])
        seen.append(request)
        i = script.pop(0)
        assert (request.method, request.url.path) == (i["request"]["method"],
                                                      i["request"]["path"])
        return httpx.Response(i["response"]["status"], json=i["response"]["json"])

    return PayPalClient(CID, SECRET, transport=httpx.MockTransport(handler)), seen


def test_order_body_binds_the_ad_hash_and_adds_up():
    q = quote("GBP", 1500, 3, Decimal(5))
    body = orders.order_body(q, content_sha256=H, invoice_id="fund_0a1b2c3d4e5f6a7b",
                             reference_id="cre_0a1b2c3d")
    unit = body["purchase_units"][0]
    assert body["intent"] == "AUTHORIZE"
    assert unit["custom_id"] == H and len(H) <= 127
    assert unit["amount"] == {"currency_code": "GBP", "value": "47.25",
                              "breakdown": {"item_total": {"currency_code": "GBP",
                                                           "value": "47.25"}}}
    assert [(i["name"], i["unit_amount"]["value"]) for i in unit["items"]] == [
        ("Ad budget", "45.00"), ("Studio fee", "2.25")]
    assert len(unit["soft_descriptor"]) <= 22
    recorded = fixture("create_order")["response"]["json"]["purchase_units"][0]
    assert {k: unit[k] for k in ("custom_id", "amount", "invoice_id")} == {
        k: recorded[k] for k in ("custom_id", "amount", "invoice_id")}


def test_no_fee_item_when_the_fee_is_zero():
    body = orders.order_body(quote("GBP", 1000, 1, Decimal(0)), content_sha256=H,
                             invoice_id="f", reference_id="r")
    assert [i["name"] for i in body["purchase_units"][0]["items"]] == ["Ad budget"]


def test_the_documented_flow_parses():
    c, seen = replay("create_order", "get_order_approved", "authorize", "capture", "refund",
                     "void")
    created = orders.create(c, {"intent": "AUTHORIZE"}, request_id="ss-1")
    assert (created.id, created.status, created.amount_minor, created.currency,
            created.custom_id) == ("5O190127TN364715T", "PAYER_ACTION_REQUIRED", 4725, "GBP", H)
    assert created.approve_url.startswith("https://www.sandbox.paypal.com/")
    got = orders.get(c, created.id)
    assert got.status == "APPROVED"
    auth = orders.authorize(c, created.id, request_id="ss-2")
    assert (auth.kind, auth.id, auth.status, auth.amount_minor, auth.custom_id) == (
        "authorization", "0VF52814937998046", "CREATED", 4725, H)
    cap = orders.capture(c, auth.id, amount_minor=4725, currency="GBP",
                         invoice_id="fund_0a1b2c3d4e5f6a7b", request_id="ss-3")
    assert (cap.kind, cap.id, cap.status, cap.amount_minor) == (
        "capture", "3C679366HH908993F", "COMPLETED", 4725)
    assert json.loads(seen[3].content) == {"amount": {"currency_code": "GBP", "value": "47.25"},
                                           "invoice_id": "fund_0a1b2c3d4e5f6a7b",
                                           "final_capture": True}
    ref = orders.refund(c, cap.id, amount_minor=1440, currency="GBP",
                        invoice_id="fund_0a1b2c3d4e5f6a7b-r1", request_id="ss-4")
    assert (ref.kind, ref.id, ref.amount_minor) == ("refund", "1JU08902781691411", 1440)
    void = orders.void(c, auth.id, request_id="ss-5")
    assert void.status == "VOIDED"


def test_an_answer_without_an_amount_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(201, json={"id": "3C679366HH908993F", "status": "COMPLETED"})

    c = PayPalClient(CID, SECRET, transport=httpx.MockTransport(handler))
    with pytest.raises(PayPalError, match="UNEXPECTED_RESPONSE"):
        orders.capture(c, "0VF52814937998046", amount_minor=1, currency="GBP",
                       invoice_id="f", request_id="ss-1")


def test_void_answered_with_no_content():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(204)

    c = PayPalClient(CID, SECRET, transport=httpx.MockTransport(handler))
    assert orders.void(c, "0VF52814937998046", request_id="ss-1").status == "VOIDED"


# -- the real sandbox recording (scripts/paypal_record.py) ---------------------------------


def test_the_recorded_sandbox_answers_parse_and_hold_no_credentials():
    data = load("paypal/sandbox")
    assert data["_provenance"] == "recorded"
    text = json.dumps(data)
    assert "A21AA" not in text and "access_token" not in text and '"payer"' not in text
    (create, get, auth) = data["interactions"]
    sent = create["request"]["json"]
    script = [create, get, auth]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        i = script.pop(0)
        assert (request.method, request.url.path) == (i["request"]["method"],
                                                      i["request"]["path"])
        return httpx.Response(i["response"]["status"], json=i["response"]["json"])

    c = PayPalClient(CID, SECRET, transport=httpx.MockTransport(handler))
    order = orders.create(c, sent, request_id="ss-1")
    unit = sent["purchase_units"][0]
    assert order.custom_id == unit["custom_id"]  # PayPal keeps the ad hash
    assert (order.amount_minor, order.currency) == (4725, "GBP")
    assert order.status == "PAYER_ACTION_REQUIRED" and order.approve_url
    assert orders.get(c, order.id).custom_id == unit["custom_id"]
    with pytest.raises(PayPalError) as e:
        orders.authorize(c, order.id, request_id="ss-2")
    assert e.value.issue == "ORDER_NOT_APPROVED"


def test_paypal_cassettes_hold_no_credentials():
    from tests.cassettes import CASSETTES

    files = sorted((CASSETTES / "paypal").glob("*.json"))
    assert {f.name for f in files} == {"orders.SYNTHETIC.json", "sandbox.json"}
    for path in files:
        data = json.loads(path.read_text())
        text = json.dumps(data["interactions"])
        assert "Basic " not in text and "Bearer A" not in text
        assert "client_secret" not in text.lower()
        for item in data["interactions"]:
            assert "headers" not in item["request"]  # never an Authorization header
            token = item["response"]["json"].get("access_token")
            assert token is None or "synthetic" in token, path.name
        if data["_provenance"] == "recorded":
            assert "access_token" not in text and '"payer"' not in text
