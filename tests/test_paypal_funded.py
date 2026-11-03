"""Signed funding end to end, against the fake PayPal: the owner's approval signs the
PayPal order with the ad; the campaign is created PAUSED and the money is held
(authorized); the activation signature captures it before anything goes live; a signed
refund returns part of it. Every step is a receipt, PayPal's (mock-signed) webhooks are
verified and receipted, and ``studio verify`` accepts the whole log."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from studio import db, funding
from studio.agents import media_buyer
from studio.approvals import (
    ContentMismatch,
    Keys,
    Pending,
    attach_funding,
    transaction,
)
from studio.paypal import PayPalConfig, hooks
from studio.receipts import PAYPAL_KIND, PAYPAL_WEBHOOK_KIND, canonical, verify
from tests.approve import approve, sign_pending
from tests.attack.test_paused_only import PNG, RUN, payload

ITEM = "cmp_paid1"


class Env:
    def __init__(self, tmp_path) -> None:
        self.tmp = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.conn = db.connect(tmp_path / "studio.db")
        db.init(self.conn)
        self.ledger = db.open_ledger(self.conn, tmp_path / "ledger.key")
        assets = tmp_path / "assets" / RUN
        assets.mkdir(parents=True)
        (assets / "feed_1x1.png").write_bytes(PNG)
        self.ledger.append(RUN, agent="producer", kind="model_call")
        from studio.meta.mock import MockAdapter

        self.adapter = MockAdapter()
        self.funder = hooks.make(PayPalConfig(adapter="mock"), self.conn, self.ledger)
        assert self.funder is not None

    def pending(self, p, item=ITEM):
        pending = Pending.create("campaign.create", item, p, ledger=self.ledger, run_id=RUN)
        with transaction(self.conn):
            pending.save(self.conn)
            self.conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, "
                              "created_ts) VALUES (?, ?, ?, ?)",
                              (pending.id, RUN, canonical(p).decode(), time.time()))
        return pending

    def fund(self, p, item=ITEM, days=3):
        """Order → buyer approves → the approval is replaced by one carrying the block."""
        pending = self.pending(p, item)
        step = self.funder.money.open(
            run_id=RUN, item_id=item, content_sha256=pending.content_sha256,
            currency="PLN", daily_budget_minor=p["campaign"]["daily_budget_minor"],
            days=days, fee_pct=Decimal(5))
        self.funder.fake.approve(step.funding.order_id)
        self.funder.money.sync_order(step.funding.id)
        f = funding.get(self.conn, step.funding.id)
        fresh, funded = attach_funding(self.conn, pending.id, p, f.block(), ledger=self.ledger)
        return fresh, funded, f

    def verify(self):
        path = self.tmp / "receipts.jsonl"
        path.write_text(self.ledger.export_jsonl())
        return verify(path, self.ledger.public_key, approver_keys=Keys.registry(self.conn))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def run_whole_loop(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    created = media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                                 funder=env.funder)
    pending, act = media_buyer.request_activation(env.conn, env.ledger, ITEM, 500,
                                                  funder=env.funder)
    v2 = sign_pending(env.conn, env.ledger, pending, act)
    live = media_buyer.activate(env.conn, env.ledger, env.adapter, v2, act, 500,
                                funder=env.funder)
    return f, created, act, live


def test_the_signed_funding_loop(env):
    f, created, act, live = run_whole_loop(env)
    assert f.total_minor == 9450  # 30.00 PLN × 3 days + 5 %
    assert created["status"] == "PAUSED"
    assert created["funding"]["state"] == "authorized"
    assert created["funding"]["amount_minor"] == 9450
    assert act["paypal"] == {"funding_id": f.id, "authorization_id":
                             created["funding"]["authorization_id"], "amount_minor": 9450,
                             "currency": "PLN"}
    assert live["status"] == "ACTIVE" and live["funding"]["state"] == "captured"
    assert funding.get(env.conn, f.id).captured_minor == 9450
    calls = [r.details["step"] for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_KIND]
    assert calls == ["paypal.create_order", "paypal.get_order", "paypal.authorize",
                     "paypal.capture"]
    hooks_seen = [r.details["event_type"] for r in env.ledger.receipts(RUN)
                  if r.kind == PAYPAL_WEBHOOK_KIND]
    assert hooks_seen == ["PAYMENT.AUTHORIZATION.CREATED", "PAYMENT.CAPTURE.COMPLETED"]
    cap = next(r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND
               and r.details["event_type"] == "PAYMENT.CAPTURE.COMPLETED")
    assert cap.details["custom_id_sha256"] == f.content_sha256  # PayPal attests the ad
    assert cap.details["verified"] == "rsa"
    report = env.verify()
    assert report.ok, report.errors


def test_the_approval_signs_the_funding_and_its_receipt_carries_the_block(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    assert fresh.funding_sha256 == funding.funding_sha256(f.block())
    assert b'"funding_sha256"' in fresh.message("ed25519:" + "0" * 32)
    sign_pending(env.conn, env.ledger, fresh, funded)
    receipt = [r for r in env.ledger.receipts(RUN) if r.kind == "approval"][-1]
    assert receipt.details["funding"] == f.block()
    assert receipt.details["funding_sha256"] == fresh.funding_sha256
    # PayPal's order carries the ad's hash: the payload without its funding block
    unit = env.funder.fake.orders[f.order_id].body["purchase_units"][0]
    assert unit["custom_id"] == f.content_sha256 == funded["funding"]["content_sha256"]


def test_attach_refuses_a_block_naming_another_ad(env):
    p = payload()
    pending = env.pending(p)
    other = payload(ad={**p["ad"], "headline": "Another ad"})
    other_hash = env.pending(other, item="cmp_other").content_sha256
    step = env.funder.money.open(run_id=RUN, item_id=ITEM, content_sha256=other_hash,
                                 currency="PLN", daily_budget_minor=3000, days=1,
                                 fee_pct=Decimal(5))
    with pytest.raises(ContentMismatch):
        attach_funding(env.conn, pending.id, p, step.funding.block(), ledger=env.ledger)


def test_no_funding_no_campaign(env):
    p = payload()
    v = approve(env.conn, env.ledger, "campaign.create", ITEM, p, RUN)
    with pytest.raises(media_buyer.BuyError) as e:
        media_buyer.create(env.conn, env.ledger, env.adapter, v, p, env.tmp,
                           funder=env.funder)
    assert e.value.code == "funding_required" and not env.adapter.calls


def test_an_order_the_buyer_did_not_approve_creates_nothing(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    funding.advance(env.conn, f.id, "expired", expect="payer_approved")  # lapsed meanwhile
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    with pytest.raises(media_buyer.BuyError) as e:
        media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                           funder=env.funder)
    assert e.value.code == "funding_changed" and not env.adapter.calls


def test_an_unfunded_campaign_cannot_be_activated(env):
    p = payload()
    v = approve(env.conn, env.ledger, "campaign.create", ITEM, p, RUN)
    media_buyer.create(env.conn, env.ledger, env.adapter, v, p, env.tmp)  # PayPal off then
    with pytest.raises(media_buyer.BuyError) as e:
        media_buyer.request_activation(env.conn, env.ledger, ITEM, 500, funder=env.funder)
    assert e.value.code == "not_funded"


def test_no_capture_no_activation(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                       funder=env.funder)
    pending, act = media_buyer.request_activation(env.conn, env.ledger, ITEM, 500,
                                                  funder=env.funder)
    v2 = sign_pending(env.conn, env.ledger, pending, act)
    env.funder.fake.fail_next(*[503] * 4)
    with pytest.raises(media_buyer.BuyError):
        media_buyer.activate(env.conn, env.ledger, env.adapter, v2, act, 500,
                             funder=env.funder)
    assert media_buyer.campaign(env.conn, ITEM)["status"] == "PAUSED"
    assert not [c for c in env.adapter.calls if c[0] == "activate"]


def test_a_lapsed_hold_is_voided(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                       funder=env.funder)
    assert env.funder.void_lapsed(now=time.time()) == []
    assert env.funder.void_lapsed(now=time.time() + hooks.HONOUR_S + 60) == [f.id]
    assert funding.get(env.conn, f.id).state == "voided"
    assert env.verify().ok


def test_a_signed_refund(env):
    f, *_ = run_whole_loop(env)
    pending, body = env.funder.request_refund(f.id, 2000)
    v = sign_pending(env.conn, env.ledger, pending, body)
    out = env.funder.execute_refund(v, body)
    assert out["state"] == "partially_refunded" and out["refunded_minor"] == 2000
    receipt = [r for r in env.ledger.receipts(RUN) if r.kind == "approval"][-1]
    assert receipt.details["refund"]["amount_minor"] == 2000
    events = [r.details["event_type"] for r in env.ledger.receipts(RUN)
              if r.kind == PAYPAL_WEBHOOK_KIND]
    assert events[-1] == "PAYMENT.CAPTURE.REFUNDED"
    report = env.verify()
    assert report.ok, report.errors


def test_a_refund_above_what_is_left_is_not_even_offered(env):
    f, *_ = run_whole_loop(env)
    with pytest.raises(hooks.FundingRefused):
        env.funder.request_refund(f.id, 9451)
