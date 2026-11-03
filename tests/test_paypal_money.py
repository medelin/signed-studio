"""Money steps against the fake PayPal: every step checks PayPal's answer, moves the
funding's state, records a money move and leaves a ``paypal.call`` receipt in the run's
chain; failures leave an ``ok: false`` receipt; nothing moves twice."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from studio import db, funding
from studio.funding import FundingError
from studio.paypal.client import PayPalClient, PayPalError
from studio.paypal.mock import FakePayPal
from studio.paypal.money import Money
from studio.receipts import PAYPAL_KIND, verify

H = "c0ffee" + "0" * 58
RUN = "run_paypal1"


class World:
    def __init__(self, tmp, **fake) -> None:
        self.tmp = tmp
        self.conn = db.connect(tmp / "studio.db")
        db.init(self.conn)
        self.ledger = db.open_ledger(self.conn, tmp / "ledger.key")
        self.fake = FakePayPal(**fake)
        self.client = PayPalClient("mock-client", "mock-secret",
                                   transport=self.fake.transport(), sleep=lambda s: None)
        self.money = Money(self.conn, self.ledger, self.client)

    def open(self, **kw):
        args = {"run_id": RUN, "item_id": "cre_1", "content_sha256": H, "currency": "GBP",
                "daily_budget_minor": 1500, "days": 3, "fee_pct": Decimal(5), **kw}
        return self.money.open(**args)

    def paypal_receipts(self):
        return [r for r in self.ledger.receipts(RUN) if r.kind == PAYPAL_KIND]

    def verify(self):
        path = self.tmp / "receipts.jsonl"
        path.write_text(self.ledger.export_jsonl())
        return verify(path, self.ledger.public_key)


@pytest.fixture()
def w(tmp_path):
    return World(tmp_path)


def to_authorized(w: World):
    step = w.open()
    w.fake.approve(step.funding.order_id)
    w.money.sync_order(step.funding.id)
    return w.money.authorize(step.funding.id, approval_id="apr_create1")


def test_the_whole_money_loop(w):
    opened = w.open()
    f = opened.funding
    assert f.state == "created" and f.order_id and f.total_minor == 4725
    order = w.fake.orders[f.order_id].body["purchase_units"][0]
    assert order["custom_id"] == H and order["invoice_id"] == f.id
    assert w.money.sync_order(f.id) is None  # the buyer has not approved yet
    w.fake.approve(f.order_id)
    assert w.money.sync_order(f.id).funding.state == "payer_approved"
    auth = w.money.authorize(f.id, approval_id="apr_create1")
    assert auth.funding.state == "authorized" and auth.funding.authorized_minor == 4725
    cap = w.money.capture(f.id, approval_id="apr_activate1")
    assert cap.funding.state == "captured" and cap.funding.captured_minor == 4725
    st = w.money.statement(f.id, spend_minor=3120)
    part = w.money.refund(f.id, st.refundable_minor, approval_id="apr_refund1")
    assert part.funding.state == "partially_refunded"
    assert part.funding.refunded_minor == st.refundable_minor == 1449
    rest = w.money.refund(f.id, 4725 - 1449, approval_id="apr_refund2")
    assert rest.funding.state == "refunded"
    assert [m["move"] for m in funding.moves(w.conn, f.id)] == [
        "order", "payer_approved", "authorization", "capture", "refund", "refund"]
    steps = [r.details["step"] for r in w.paypal_receipts()]
    assert steps == ["paypal.create_order", "paypal.get_order", "paypal.authorize",
                     "paypal.capture", "paypal.refund", "paypal.refund"]
    assert all(r.details["ok"] is True for r in w.paypal_receipts())
    report = w.verify()
    assert report.ok, report.errors


def test_receipts_name_paypal_ids_amounts_and_the_ad_hash_but_no_payer(w):
    to_authorized(w)
    w.money.capture(funding.of_item(w.conn, "cre_1")[0].id, approval_id="apr_activate1")
    for r in w.paypal_receipts():
        d = r.details
        assert d["content_sha256"] == H and d["currency"] == "GBP"
        assert d["funding_sha256"] and r.provider == "paypal"
        assert r.output_sha256 == d["body_sha256"]
    cap = w.paypal_receipts()[-1].details
    f = funding.of_item(w.conn, "cre_1")[0]
    assert cap["capture_id"] == f.capture_id and cap["authorization_id"] not in ("",)
    assert cap["custom_id_sha256"] == H and cap["amount_minor"] == 4725
    assert cap["approval_id"] == "apr_activate1"
    assert cap["funding_sha256"] == funding.funding_sha256(f.block())
    text = w.ledger.export_jsonl()
    for leak in ("sb-buyer", "example.com", "Sandbox", "MOCKPAYER", "payer"):
        assert leak not in text


def test_paypal_ids_with_long_digit_runs_survive_redaction(w):
    to_authorized(w)
    ids = [r.details.get("authorization_id") for r in w.paypal_receipts()]
    f = funding.of_item(w.conn, "cre_1")[0]
    assert f.authorization_id in ids and "[phone]" not in json.dumps(ids)


def test_a_step_out_of_order_is_refused_before_paypal_is_called(w):
    f = w.open().funding
    calls = len(w.fake.calls)
    with pytest.raises(FundingError) as e:
        w.money.capture(f.id, approval_id="apr_1")
    assert e.value.code == "wrong_state" and len(w.fake.calls) == calls


def test_authorizing_before_the_buyer_approved_leaves_a_failed_receipt(w):
    f = w.open().funding
    funding.advance(w.conn, f.id, "payer_approved", expect="created")  # a lying client
    with pytest.raises(PayPalError) as e:
        w.money.authorize(f.id, approval_id="apr_create1")
    assert e.value.issue == "ORDER_NOT_APPROVED"
    last = w.paypal_receipts()[-1].details
    assert (last["ok"], last["error"], last["issue"], last["step"]) == (
        False, "UNPROCESSABLE_ENTITY", "ORDER_NOT_APPROVED", "paypal.authorize")
    assert funding.get(w.conn, f.id).state == "payer_approved"
    assert w.verify().ok


def test_money_steps_need_an_approval_id(w):
    to_authorized(w)
    f = funding.of_item(w.conn, "cre_1")[0]
    for bad in ("", "call 600 123 456"):
        with pytest.raises(FundingError) as e:
            w.money.capture(f.id, approval_id=bad)
        assert e.value.code == "no_approval"


def test_capture_twice_is_refused_and_paypal_sees_one_capture(w):
    to_authorized(w)
    f = funding.of_item(w.conn, "cre_1")[0]
    w.money.capture(f.id, approval_id="apr_activate1")
    with pytest.raises(FundingError):
        w.money.capture(f.id, approval_id="apr_activate1")
    assert len(w.fake.captures) == 1


def test_a_retried_capture_reuses_the_request_id_and_paypal_acts_once(tmp_path):
    w = World(tmp_path)
    to_authorized(w)
    f = funding.of_item(w.conn, "cre_1")[0]
    w.fake.fail_next(503, 500)
    w.money.capture(f.id, approval_id="apr_activate1")
    assert len(w.fake.captures) == 1
    posts = [p for m, p in w.fake.calls if p.endswith("/capture")]
    assert len(posts) == 3


def test_a_lost_answer_then_a_second_attempt_gets_the_first_capture(w):
    """The process dies after PayPal captured but before the studio recorded it: the
    same approval's retry carries the same request id, so PayPal returns the first
    capture instead of taking the money again."""
    to_authorized(w)
    f = funding.of_item(w.conn, "cre_1")[0]
    from studio.paypal import orders
    from studio.paypal.client import request_id

    rid = request_id(f.id, "capture", "apr_activate1")
    first = orders.capture(w.client, f.authorization_id, amount_minor=4725, currency="GBP",
                           invoice_id=f.id, request_id=rid)
    step = w.money.capture(f.id, approval_id="apr_activate1")
    assert step.result.id == first.id and len(w.fake.captures) == 1


def test_void_releases_the_hold(w):
    to_authorized(w)
    f = funding.of_item(w.conn, "cre_1")[0]
    step = w.money.void(f.id, reason="activation_refused")
    assert step.funding.state == "voided"
    assert w.fake.auths[f.authorization_id].status == "VOIDED"
    assert step.receipt.details["reason"] == "activation_refused"
    with pytest.raises(FundingError):
        w.money.capture(f.id, approval_id="apr_activate1")
    assert w.verify().ok


def test_refund_above_what_is_left_is_refused_locally(w):
    to_authorized(w)
    f = funding.of_item(w.conn, "cre_1")[0]
    w.money.capture(f.id, approval_id="apr_activate1")
    for amount in (0, -5, 4726):
        with pytest.raises(FundingError) as e:
            w.money.refund(f.id, amount, approval_id="apr_refund1")
        assert e.value.code in ("refund_amount", "wrong_state")
    assert not w.fake.refunds


def test_a_paypal_answer_naming_another_ad_is_a_mismatch(w):
    step = w.open()
    f = step.funding
    w.fake.approve(f.order_id)
    w.fake.orders[f.order_id].body["purchase_units"][0]["custom_id"] = "f" * 64
    with pytest.raises(FundingError) as e:
        w.money.sync_order(f.id)
    assert e.value.code == "mismatch"
    last = w.paypal_receipts()[-1].details
    assert (last["ok"], last["error"], last["issue"]) == (False, "MISMATCH", "CUSTOM_ID")
    assert funding.get(w.conn, f.id).state == "created"


def test_a_paypal_answer_with_another_amount_is_a_mismatch(w):
    step = w.open()
    f = step.funding
    w.fake.approve(f.order_id)
    unit = w.fake.orders[f.order_id].body["purchase_units"][0]
    unit["amount"] = {**unit["amount"], "value": "4.73"}
    with pytest.raises(FundingError) as e:
        w.money.authorize(funding.advance(w.conn, f.id, "payer_approved",
                                          expect="created").id, approval_id="apr_1")
    assert e.value.code == "mismatch"
    assert funding.get(w.conn, f.id).state == "payer_approved"


def test_the_ledger_rejects_a_receipt_with_an_edited_amount(w):
    to_authorized(w)
    path = w.tmp / "receipts.jsonl"
    lines = w.ledger.export_jsonl().splitlines()
    for i, line in enumerate(lines):
        if '"paypal.authorize"' in line:
            lines[i] = line.replace('"amount_minor": 4725', '"amount_minor": 47')
    path.write_text("\n".join(lines) + "\n")
    report = verify(path, w.ledger.public_key)
    assert not report.ok and any("hash mismatch" in e for e in report.errors)


def test_open_respects_the_daily_ceiling(w):
    with pytest.raises(FundingError) as e:
        w.open(daily_budget_minor=60_00, max_daily_minor=50_00)
    assert e.value.code == "ceiling" and not w.fake.orders
