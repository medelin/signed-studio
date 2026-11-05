"""The vendored PayPal Agent Toolkit (read tools only) on the studio's receipted sandbox
client: one ``paypal.call`` receipt per tool call (hashing the caller's answer), no payer
data, only the account's own money, and no write tool reachable."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from studio import db, funding
from studio.paypal import agent_tools
from studio.paypal.agent_tools import (
    READ_TOOLS,
    AccountRefs,
    ToolRefused,
    account_refs,
    run_read_tool,
    tool_specs,
)
from studio.paypal.client import PayPalClient
from studio.paypal.receipt_key import keyed_sha256, receipt_key
from studio.receipts import PAYPAL_KIND, verify

OWN = "a" * 64
OTHER = "b" * 64
OWN_ORDER = "5O190127TN364715T"
OWN_CAPTURE = "3C679366HH908993F"
OWN_TID = "9XX11111AB222222C"
CHAIN = "acct:acc_1:paypal-tools"
KEY = bytes(range(32))


def tx(custom: str, tid: str = "9XX11111AB222222C") -> dict:
    return {
        "transaction_info": {"transaction_id": tid, "custom_field": custom,
                             "transaction_amount": {"currency_code": "EUR", "value": "10.00"}},
        "payer_info": {"email_address": "buyer@example.com", "payer_name": {"given_name": "Ann"}},
        "shipping_info": {"name": "Ann Buyer", "address": {"line1": "1 Main St"}},
    }


def with_text(t: dict) -> dict:
    t["transaction_info"]["transaction_subject"] = "Dinner for Ann"
    t["transaction_info"]["transaction_note"] = "call me on 0700 123"
    return t


class Sandbox:
    def __init__(self, transactions=None, disputes=None) -> None:
        self.transactions = transactions if transactions is not None else []
        self.disputes = disputes if disputes is not None else []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        self.requests.append(request)
        if path == "/v1/reporting/transactions":
            return httpx.Response(200, json={"transaction_details": self.transactions,
                                             "total_items": len(self.transactions)})
        if path.startswith("/v2/checkout/orders/"):
            return httpx.Response(200, json={
                "id": path.rsplit("/", 1)[1], "status": "COMPLETED",
                "payer": {"email_address": "buyer@example.com"},
                "purchase_units": [{"payments": {"captures": [
                    {"amount": {"currency_code": "EUR", "value": "10.00"}}]}}]})
        if path == "/v1/customer/disputes":
            return httpx.Response(200, json={"items": self.disputes})
        if path.startswith("/v1/customer/disputes/"):
            return httpx.Response(200, json={
                "dispute_id": path.rsplit("/", 1)[1],
                "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE,
                                           "buyer": {"name": "Ann"}}]})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    def calls(self, prefix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.startswith(prefix)]


class World:
    def __init__(self, tmp, **kw) -> None:
        self.tmp = tmp
        self.conn = db.connect(tmp / "studio.db")
        db.init(self.conn)
        self.ledger = db.open_ledger(self.conn, tmp / "ledger.key")
        self.sandbox = Sandbox(**kw)
        self.client = PayPalClient("id", "secret", transport=httpx.MockTransport(self.sandbox),
                                   sleep=lambda s: None)
        self.key = KEY
        self.refs = AccountRefs(frozenset({OWN}), frozenset({OWN_ORDER, OWN_CAPTURE, OWN_TID}))

    def run(self, name, params=None, refs=None):
        return run_read_tool(client=self.client, ledger=self.ledger, conn=self.conn,
                             receipt_key=self.key, chain_id=CHAIN,
                             account_refs=refs or self.refs, name=name,
                             params=params or {})

    def receipts(self):
        return [r for r in self.ledger.receipts(CHAIN) if r.kind == PAYPAL_KIND]

    def export(self) -> str:
        return self.ledger.export_jsonl()


@pytest.fixture()
def w(tmp_path):
    return World(tmp_path)


def test_tool_specs_cover_exactly_the_read_tools():
    specs = tool_specs()
    assert {s["name"] for s in specs} == READ_TOOLS
    for s in specs:
        assert s["description"]
        assert s["input_schema"]["type"] == "object"


@pytest.mark.parametrize("name", ["create_order", "pay_order", "create_invoice",
                                  "accept_dispute_claim", "get_merchant_insights", "nope"])
def test_write_tools_are_refused_before_any_call(w, name):
    with pytest.raises(ToolRefused) as exc:
        w.run(name, {})
    assert exc.value.code == "not_allowed"
    assert w.sandbox.requests == []
    assert w.receipts() == []


def test_list_transactions_is_receipted(w):
    w.sandbox.transactions = [tx(OWN)]
    out = w.run("list_transactions", {})
    assert [t["transaction_info"]["custom_field"] for t in out["transaction_details"]] == [OWN]
    (r,) = w.receipts()
    d = r.details
    assert d["step"] == "paypal.agent_tool" and d["tool"] == "list_transactions"
    assert d["path"] == "/v1/reporting/transactions" and "status" not in d and d["ok"] is True
    assert len(d["body_sha256"]) == 64
    assert r.agent == "treasury" and r.provider == "paypal"
    assert "?" not in json.dumps(d)
    path = w.tmp / "receipts.jsonl"
    path.write_text(w.export())
    report = verify(path, w.ledger.public_key)
    assert report.ok, report.errors


def test_payer_data_never_leaves_the_adapter(w):
    w.sandbox.transactions = [with_text(tx(OWN))]
    out = w.run("list_transactions", {})
    assert out["transaction_details"] == [{"transaction_info": {
        "transaction_id": "9XX11111AB222222C", "custom_field": OWN,
        "transaction_amount": {"currency_code": "EUR", "value": "10.00"}}}]
    blob = json.dumps(out) + w.export()
    for secret in ("buyer@example.com", "shipping_info", "payer_info", "Ann Buyer",
                   "1 Main St", "Dinner for Ann", "call me on"):
        assert secret not in blob


def test_dispute_free_text_is_dropped(w):
    w.sandbox.disputes = [{"dispute_id": "PP-D-1", "dispute_reason": "ITEM_NOT_RECEIVED",
                           "messages": [{"content": "I am Ann, call 0700"}],
                           "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE}]}]
    out = w.run("list_disputes", {})
    assert out["items"] == [{"dispute_id": "PP-D-1", "dispute_reason": "ITEM_NOT_RECEIVED",
                             "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE}]}]


def test_foreign_transactions_are_filtered_out(w):
    w.sandbox.transactions = [tx(OWN, "9XX11111AB222222C"), tx(OTHER, "7YY99999ZZ888888Q")]
    out = w.run("list_transactions", {})
    assert [t["transaction_info"]["transaction_id"] for t in out["transaction_details"]] == [
        "9XX11111AB222222C"]


def test_content_hash_is_never_an_access_key(w):
    # same ad content funded by another account: custom_field equals ours, ids are not
    w.sandbox.transactions = [tx(OWN, "7YY99999ZZ888888Q")]
    out = w.run("list_transactions", {})
    assert out["transaction_details"] == []


def test_transaction_matched_by_paypal_id(w):
    w.sandbox.transactions = [tx("", OWN_CAPTURE), tx("", "7YY99999ZZ888888Q")]
    out = w.run("list_transactions", {})
    assert len(out["transaction_details"]) == 1


def test_order_details_of_a_foreign_order_is_refused(w):
    with pytest.raises(ToolRefused) as exc:
        w.run("get_order_details", {"order_id": "9Z99999999999999Z"})
    assert exc.value.code == "not_allowed"
    assert w.sandbox.calls("/v2/checkout/orders") == []


def test_own_order_details_are_redacted(w):
    out = w.run("get_order_details", {"order_id": OWN_ORDER})
    assert out["raw"]["id"] == OWN_ORDER
    assert "buyer@example.com" not in json.dumps(out) + w.export()
    assert [r.details["path"] for r in w.receipts()] == [f"/v2/checkout/orders/{OWN_ORDER}"]


def test_disputes_filtered_and_foreign_dispute_refused(w):
    w.sandbox.disputes = [
        {"dispute_id": "PP-D-1", "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE}]},
        {"dispute_id": "PP-D-2", "disputed_transactions": [{"seller_transaction_id": "ZZZ"}]}]
    out = w.run("list_disputes", {})
    assert [i["dispute_id"] for i in out["items"]] == ["PP-D-1"]
    assert w.run("get_dispute", {"dispute_id": "PP-D-1"})["dispute_id"] == "PP-D-1"
    with pytest.raises(ToolRefused) as exc:
        w.run("get_dispute", {"dispute_id": "PP-D-1"}, refs=AccountRefs(frozenset(), frozenset()))
    assert exc.value.code == "not_allowed"


def test_invalid_arguments_are_refused_before_any_call(w):
    with pytest.raises(ToolRefused) as exc:
        w.run("get_order_details", {})
    assert exc.value.code == "invalid_arguments"
    with pytest.raises(ToolRefused) as exc:
        w.run("list_transactions", {"transaction_status": "X"})
    assert exc.value.code == "invalid_arguments"
    assert w.sandbox.requests == []


def test_a_paypal_error_is_receipted_then_refused(w):
    def boom(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(403, json={"name": "NOT_AUTHORIZED",
                                         "details": [{"issue": "PERMISSION_DENIED"}]})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(boom),
                            sleep=lambda s: None)
    with pytest.raises(ToolRefused) as exc:
        w.run("list_transactions", {})
    assert exc.value.code == "paypal_error"
    (r,) = w.receipts()
    assert r.details["ok"] is False and "status" not in r.details
    assert r.details["body_sha256"] == ""


def test_transaction_search_by_id_pages_months_in_one_receipt(w):
    w.sandbox.transactions = []
    out = w.run("list_transactions", {"transaction_id": OWN_CAPTURE, "search_months": 2})
    assert out["transaction_details"] == []
    assert len(w.sandbox.calls("/v1/reporting/transactions")) == 2
    assert len(w.receipts()) == 1


def test_search_months_outside_1_to_12_is_refused_without_a_call(w):
    for months in (0, 13, -1):
        with pytest.raises(ToolRefused) as exc:
            w.run("list_transactions", {"transaction_id": OWN_CAPTURE, "search_months": months})
        assert exc.value.code == "invalid_arguments"
    assert w.sandbox.requests == []


def test_call_budget_stops_a_looping_handler(w, monkeypatch):
    class Loop:
        def __init__(self):
            self.n = 0

        def __call__(self, adapter, params):
            for _ in range(50):
                self.n += 1
                adapter.get("/v1/reporting/transactions")
            return {}

    loop = Loop()
    entry = agent_tools._entry("list_transactions")
    monkeypatch.setitem(entry, "execute", loop)
    with pytest.raises(ToolRefused):
        w.run("list_transactions", {})
    assert len(w.sandbox.requests) == agent_tools.MAX_CALLS_PER_TOOL
    assert loop.n == agent_tools.MAX_CALLS_PER_TOOL + 1


def test_no_request_after_a_failed_call(w, monkeypatch):
    seen = []

    def boom(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen.append(request)
        return httpx.Response(403, json={"name": "NOT_AUTHORIZED"})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(boom),
                            sleep=lambda s: None)
    with pytest.raises(ToolRefused) as exc:
        w.run("list_transactions", {"transaction_id": OWN_CAPTURE, "search_months": 5})
    assert exc.value.code == "paypal_error"
    assert len(seen) == 1


def test_redact_drops_keys_and_emails():
    out = agent_tools.redact({"a": {"payer": {"x": 1}, "ok": "mail me at a@b.co", "n": 3},
                              "name": "x", "l": [{"buyer": 1, "k": "fine"}]})
    assert out == {"a": {"ok": "[redacted]", "n": 3}, "l": [{"k": "fine"}]}


def _fund(conn, run, item, sha, order):
    q = funding.quote("EUR", 1000, 3, Decimal(5))
    f = funding.create(conn, run_id=run, item_id=item, content_sha256=sha, q=q, now=1.0)
    funding.advance(conn, f.id, "created", expect="created", now=2.0, order_id=order)


def _brief(conn, bid, run, *, api=None, channel="", owner=""):
    conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts, channel, "
                 "account_id) VALUES (?, ?, 't', 'text', 1, ?, ?)", (bid, run, channel, owner))
    if api:
        conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, created_ts) "
                     "VALUES (?, ?, 'tok', 1)", (bid, api))


def test_account_refs_follow_the_apps_ownership_rule(w):
    c = w.conn
    sha = lambda ch: ch * 64  # noqa: E731
    _brief(c, "b_api1", "run_api1", api="acc_1")
    _brief(c, "b_api2", "run_api2", api="acc_2")
    _brief(c, "b_wa1", "run_wa1", channel="whatsapp", owner="acc_1")
    _brief(c, "b_wa2", "run_wa2", channel="whatsapp", owner="acc_2")
    _brief(c, "b_ui", "run_ui")
    _fund(c, "run_api1", "cre_1", sha("1"), "1A111111111111111")
    _fund(c, "run_api2", "cre_2", sha("2"), "2A222222222222222")
    _fund(c, "run_wa1", "cre_3", sha("3"), "3A333333333333333")
    _fund(c, "run_wa2", "cre_4", sha("4"), "4A444444444444444")
    _fund(c, "run_ui", "cre_5", sha("5"), "5A555555555555555")
    _fund(c, "run_orphan", "cre_6", sha("6"), "6A666666666666666")  # no brief, no campaign
    one = account_refs(c, "acc_1")
    two = account_refs(c, "acc_2")
    # API + WhatsApp of the account, plus the studio's web-UI work
    assert one.content_sha256 == {sha("1"), sha("3"), sha("5")}
    assert one.paypal_ids == {"1A111111111111111", "3A333333333333333", "5A555555555555555"}
    assert two.content_sha256 == {sha("2"), sha("4"), sha("5")}
    assert account_refs(c, "acc_none").content_sha256 == {sha("5")}



def _paging_world(w, seen):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen.append(request)
        return httpx.Response(200, json={
            "transaction_details": [tx(OWN, OWN_TID)], "page": 1, "total_pages": 5,
            "total_items": 2500, "links": [{"href": "https://api-m.sandbox.paypal.com/x"}]})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None)


def test_paypal_tool_answer_has_no_paging_fields(w):
    _paging_world(w, [])
    out = w.run("list_transactions", {})
    for key in ("page", "total_pages", "total_items", "has_more", "links"):
        assert key not in out, key
    assert len(out["transaction_details"]) == 1


def test_agent_page_and_page_size_are_pinned(w):
    seen: list = []
    _paging_world(w, seen)
    w.run("list_transactions", {"page": 3, "page_size": 1})
    q = seen[0].url.params
    assert q["page"] == "1" and q["page_size"] == str(agent_tools.PAGE_SIZE)


def _handler_world(w, answer):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json=answer)

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None)


def test_by_id_search_of_a_foreign_id_makes_no_call(w):
    with pytest.raises(ToolRefused) as exc:
        w.run("list_transactions", {"transaction_id": "7YY99999ZZ888888Q",
                                    "search_months": 1})
    assert exc.value.code == "not_allowed"
    assert w.sandbox.requests == [] and w.receipts() == []


def test_own_id_search_still_works(w):
    w.sandbox.transactions = [tx(OWN, OWN_CAPTURE)]
    out = w.run("list_transactions", {"transaction_id": OWN_CAPTURE, "search_months": 1})
    assert out["found"] is True
    assert [t["transaction_info"]["transaction_id"] for t in out["transaction_details"]] == [
        OWN_CAPTURE]


def test_list_disputes_by_a_foreign_transaction_makes_no_call(w):
    with pytest.raises(ToolRefused) as exc:
        w.run("list_disputes", {"disputed_transaction_id": "7YY99999ZZ888888Q"})
    assert exc.value.code == "not_allowed"
    assert w.sandbox.requests == []
    assert w.run("list_disputes", {"disputed_transaction_id": OWN_CAPTURE}) == {"items": []}


def _dispute_world(w):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path.endswith("/PP-D-FOREIGN"):
            return httpx.Response(200, json={"dispute_id": "PP-D-FOREIGN",
                                             "disputed_transactions": [
                                                 {"seller_transaction_id": "ZZZ"}]})
        if request.url.path.endswith("/PP-D-OWN"):
            return httpx.Response(200, json={"dispute_id": "PP-D-OWN",
                                             "disputed_transactions": [
                                                 {"seller_transaction_id": OWN_CAPTURE}]})
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None)


def test_get_dispute_receipts_do_not_tell_unknown_from_foreign(w):
    _dispute_world(w)
    seen = []
    for dispute in ("PP-D-UNKNOWN", "PP-D-FOREIGN"):
        with pytest.raises(ToolRefused) as exc:
            w.run("get_dispute", {"dispute_id": dispute})
        seen.append((exc.value.code, str(exc.value)))
    assert seen[0] == seen[1] and seen[0][0] == "not_allowed"
    unknown, foreign = w.receipts()

    def shape(r):
        return {k: v for k, v in r.details.items() if k != "path"}, r.output_sha256

    assert shape(unknown) == shape(foreign)
    assert unknown.details["ok"] is False
    assert w.run("get_dispute", {"dispute_id": "PP-D-OWN"})["dispute_id"] == "PP-D-OWN"
    assert w.receipts()[-1].details["ok"] is True
    path = w.tmp / "receipts.jsonl"
    path.write_text(w.export())
    assert verify(path, w.ledger.public_key).ok


def test_list_answers_are_allowlisted(w):
    _handler_world(w, {"transaction_details": [tx(OWN, OWN_TID)], "found": True,
                       "message": "x", "total_items": 9, "links": [], "account_number": "A1",
                       "last_refreshed_datetime": "t"})
    out = w.run("list_transactions", {})
    assert set(out) == {"transaction_details"}
    w.sandbox.disputes = []
    _handler_world(w, {"items": [{"dispute_id": "PP-D-1", "disputed_transactions": [
        {"seller_transaction_id": OWN_CAPTURE}]}], "total_items": 40, "links": [],
        "message": "m"})
    assert set(w.run("list_disputes", {})) == {"items"}


FOREIGN_TID = "7YY99999ZZ888888Q"


@pytest.mark.parametrize("name,params,raw_a,raw_b", [
    ("list_transactions", {},
     {"transaction_details": [tx(OWN, OWN_TID)]},
     {"transaction_details": [tx(OWN, OWN_TID), tx(OTHER, FOREIGN_TID)], "total_items": 2}),
    ("list_disputes", {},
     {"items": [{"dispute_id": "PP-D-1",
                 "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE}]}]},
     {"items": [{"dispute_id": "PP-D-1",
                 "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE}]},
                {"dispute_id": "PP-D-9",
                 "disputed_transactions": [{"seller_transaction_id": FOREIGN_TID}]}]}),
    ("list_disputes", {}, {"items": []},
     {"items": [{"dispute_id": "PP-D-9",
                 "disputed_transactions": [{"seller_transaction_id": FOREIGN_TID}]}]}),
    ("get_dispute", {"dispute_id": "PP-D-1"},
     {"dispute_id": "PP-D-1", "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE}]},
     {"dispute_id": "PP-D-1", "disputed_transactions": [{"seller_transaction_id": OWN_CAPTURE},
                                                        {"seller_transaction_id": "ZZ"}],
      "update_time": "x", "messages": [{"content": "hi"}]}),
])
def test_agent_receipts_hash_the_callers_answer_not_paypals_body(tmp_path, name, params,
                                                                 raw_a, raw_b):

    hashes, answers = [], []
    for i, raw in enumerate((raw_a, raw_b)):
        (tmp_path / str(i)).mkdir()
        world = World(tmp_path / str(i))
        _handler_world(world, raw)
        out = world.run(name, params)
        answers.append(out)
        (r,) = world.receipts()
        assert "status" not in r.details and r.details["ok"] is True
        assert r.details["body_sha256"] == r.output_sha256
        hashes.append(r.details["body_sha256"])
        path = world.tmp / "receipts.jsonl"
        path.write_text(world.export())
        assert verify(path, world.ledger.public_key).ok
    if name != "get_dispute":  # get_dispute: the dispute itself is the caller's answer
        assert answers[0] == answers[1]
        assert hashes[0] == hashes[1]
    assert hashes == [keyed_sha256(KEY, a, CHAIN) for a in answers]


def test_order_details_receipt_hashes_the_answer(w):

    out = w.run("get_order_details", {"order_id": OWN_ORDER})
    (r,) = w.receipts()
    assert "status" not in r.details and r.details["ok"] is True
    assert r.details["body_sha256"] == keyed_sha256(KEY, out, CHAIN)


def test_by_id_search_is_one_receipt_for_the_tool_call(w):
    w.sandbox.transactions = []
    w.run("list_transactions", {"transaction_id": OWN_CAPTURE, "search_months": 3})
    assert len(w.sandbox.calls("/v1/reporting/transactions")) == 3
    (r,) = w.receipts()
    assert r.details["ok"] is True and "status" not in r.details


def test_list_disputes_paging_is_pinned(w):
    seen: list = []

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen.append(request)
        return httpx.Response(200, json={"items": [], "links": [
            {"rel": "next", "href": "https://api-m.sandbox.paypal.com/v1/customer/disputes"
                                    "?next_page_token=abc"}]})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None)
    out = w.run("list_disputes", {"page": 4, "page_size": 1, "next_page_token": "abc"})
    assert out == {"items": []}
    (req,) = seen
    q = dict(req.url.params)
    assert q == {"page_size": str(agent_tools.DISPUTES_PAGE_SIZE)}


def test_dispute_state_must_be_known(w):
    for state in ("NOPE", "resolved", "", 3):
        with pytest.raises(ToolRefused) as exc:
            w.run("list_disputes", {"dispute_state": state})
        assert exc.value.code == "invalid_arguments", state
    assert w.sandbox.requests == []
    for state in sorted(agent_tools.DISPUTE_STATES):
        assert w.run("list_disputes", {"dispute_state": state}) == {"items": []}


# -- keyed hashes (public receipts must not confirm a guessed answer) ---------------------


def test_agent_receipt_hash_is_keyed(tmp_path):
    import hashlib
    import hmac

    from studio.receipts import canonical

    out = None
    hashes = []
    for i, key in enumerate((KEY, bytes(32))):
        (tmp_path / str(i)).mkdir()
        world = World(tmp_path / str(i))
        world.key = key
        world.sandbox.transactions = []
        out = world.run("list_transactions", {})
        (r,) = world.receipts()
        assert r.details["hash"] == "hmac-sha256"
        hashes.append(r.details["body_sha256"])
    assert hashes[0] != hashes[1]
    mac = hmac.new(KEY, CHAIN.encode() + b"\x00" + canonical(out), hashlib.sha256)
    assert hashes[0] == mac.hexdigest()
    assert hashes[0] == keyed_sha256(KEY, out, CHAIN)
    # not the plain hash anyone could recompute from a guessed answer
    assert hashes[0] != agent_tools.answer_sha256(out)


def test_receipt_key_file_is_private(tmp_path):
    key = receipt_key(tmp_path)
    path = tmp_path / "receipt-hmac.key"
    assert len(key) == 32 and path.read_bytes() == key
    assert path.stat().st_mode & 0o777 == 0o600
    assert receipt_key(tmp_path) == key


def test_export_never_contains_the_key(w, tmp_path):
    key = receipt_key(tmp_path)
    w.key = key
    w.run("list_transactions", {})
    export = w.export()
    assert key.hex() not in export and key.hex().upper() not in export
    import base64
    assert base64.b64encode(key).decode() not in export
    path = tmp_path / "receipts.jsonl"
    path.write_text(export)
    assert verify(path, w.ledger.public_key).ok  # the hash is opaque to verify


def test_concurrent_first_creation_yields_one_key(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    for round_ in range(20):
        d = tmp_path / str(round_)
        d.mkdir()
        with ThreadPoolExecutor(8) as pool:
            keys = list(pool.map(receipt_key, [d] * 8))
        assert len(set(keys)) == 1 and len(keys[0]) == 32
        assert [p.name for p in d.iterdir()] == ["receipt-hmac.key"]  # no temp left


def test_a_leftover_empty_key_file_is_reported_clearly(tmp_path):
    (tmp_path / "receipt-hmac.key").write_bytes(b"")
    with pytest.raises(RuntimeError, match=r"receipt-hmac\.key.*32 bytes"):
        receipt_key(tmp_path)


def test_same_answer_in_two_chains_hashes_differently(tmp_path):
    hashes = []
    for i, chain in enumerate(("acct:acc_1:paypal-tools", "acct:acc_2:paypal-tools")):
        (tmp_path / str(i)).mkdir()
        world = World(tmp_path / str(i))
        out = run_read_tool(client=world.client, ledger=world.ledger, conn=world.conn,
                            receipt_key=KEY, chain_id=chain, account_refs=world.refs,
                            name="list_transactions", params={})
        assert out == {"transaction_details": []}
        (r,) = world.ledger.receipts(chain)
        hashes.append(r.details["body_sha256"])
    assert hashes[0] != hashes[1]
