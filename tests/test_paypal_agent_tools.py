"""The vendored PayPal Agent Toolkit (read tools only) on the studio's receipted sandbox
client: one ``paypal.call`` receipt per HTTP call, no payer data, only the account's own
money, and no write tool reachable."""

from __future__ import annotations

import json

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
from studio.receipts import PAYPAL_KIND, verify

OWN = "a" * 64
OTHER = "b" * 64
OWN_ORDER = "5O190127TN364715T"
OWN_CAPTURE = "3C679366HH908993F"
CHAIN = "acct:acc_1:paypal-tools"


def tx(custom: str, tid: str = "9XX11111AB222222C") -> dict:
    return {
        "transaction_info": {"transaction_id": tid, "custom_field": custom,
                             "transaction_amount": {"currency_code": "EUR", "value": "10.00"}},
        "payer_info": {"email_address": "buyer@example.com", "payer_name": {"given_name": "Ann"}},
        "shipping_info": {"name": "Ann Buyer", "address": {"line1": "1 Main St"}},
    }


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
        self.refs = AccountRefs(frozenset({OWN}), frozenset({OWN_ORDER, OWN_CAPTURE}))

    def run(self, name, params=None, refs=None):
        return run_read_tool(client=self.client, ledger=self.ledger, conn=self.conn,
                             chain_id=CHAIN, account_refs=refs or self.refs, name=name,
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
    assert d["path"] == "/v1/reporting/transactions" and d["status"] == 200 and d["ok"] is True
    assert len(d["body_sha256"]) == 64
    assert r.agent == "treasury" and r.provider == "paypal"
    assert "?" not in json.dumps(d)
    path = w.tmp / "receipts.jsonl"
    path.write_text(w.export())
    report = verify(path, w.ledger.public_key)
    assert report.ok, report.errors


def test_payer_data_never_leaves_the_adapter(w):
    w.sandbox.transactions = [tx(OWN)]
    out = w.run("list_transactions", {})
    blob = json.dumps(out) + w.export()
    assert "buyer@example.com" not in blob
    assert "shipping_info" not in blob and "payer_info" not in blob
    assert "Ann" not in blob


def test_foreign_transactions_are_filtered_out(w):
    w.sandbox.transactions = [tx(OWN, "9XX11111AB222222C"), tx(OTHER, "7YY99999ZZ888888Q")]
    out = w.run("list_transactions", {})
    assert [t["transaction_info"]["transaction_id"] for t in out["transaction_details"]] == [
        "9XX11111AB222222C"]


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
    assert r.details["ok"] is False and r.details["status"] == 403


def test_transaction_search_by_id_pages_months_and_receipts_each_call(w):
    w.sandbox.transactions = []
    out = w.run("list_transactions", {"transaction_id": OWN_CAPTURE, "search_months": 2})
    assert out["transaction_details"] == []
    assert len(w.receipts()) == 2


def test_redact_drops_keys_and_emails():
    out = agent_tools.redact({"a": {"payer": {"x": 1}, "ok": "mail me at a@b.co", "n": 3},
                              "name": "x", "l": [{"buyer": 1, "k": "fine"}]})
    assert out == {"a": {"ok": "[redacted]", "n": 3}, "l": [{"k": "fine"}]}


def test_account_refs_come_from_the_accounts_fundings(w):
    conn = w.conn
    q = funding.quote("EUR", 1000, 3, __import__("decimal").Decimal(5))
    if True:
        conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts) VALUES "
                     "('b1', 'run_a', 't', 'text', 1)")
        conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, created_ts) "
                     "VALUES ('b1', 'acc_1', 'tok', 1)")
        conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts) VALUES "
                     "('b2', 'run_b', 't', 'text', 1)")
        conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, created_ts) "
                     "VALUES ('b2', 'acc_2', 'tok', 1)")
    f1 = funding.create(conn, run_id="run_a", item_id="cre_1", content_sha256=OWN, q=q, now=1.0)
    f2 = funding.create(conn, run_id="run_b", item_id="cre_2", content_sha256=OTHER, q=q, now=1.0)
    funding.advance(conn, f1.id, "created", expect="created", now=2.0, order_id=OWN_ORDER)
    funding.advance(conn, f2.id, "created", expect="created", now=2.0, order_id="2B222222222222222")
    refs = account_refs(conn, "acc_1")
    assert refs.content_sha256 == {OWN} and refs.paypal_ids == {OWN_ORDER}
    assert account_refs(conn, "acc_none") == AccountRefs(frozenset(), frozenset())

