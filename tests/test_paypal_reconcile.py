"""Reconciliation of an account's PayPal moves against PayPal's transaction list: matched
by PayPal ids only, ``pending`` while Transaction Search lags, per account."""

from __future__ import annotations

from decimal import Decimal

import pytest

from studio import funding
from studio.paypal.reconcile import RECON_LAG_S, reconcile, summary
from tests.test_paypal_agent_tools import World, _brief

NOW = 1_800_000_000.0
SHA = "c" * 64
CAP = "3C679366HH908993F"
AUTH = "7AU11111AB222222C"


def listed(tid: str, value: str = "220.50", custom: str = SHA, ref: str = "") -> dict:
    info = {"transaction_id": tid, "custom_field": custom,
            "transaction_amount": {"currency_code": "EUR", "value": value}}
    if ref:
        info["paypal_reference_id"] = ref
    return {"transaction_info": info,
            "payer_info": {"email_address": "buyer@example.com"}}


def captured(conn, run, item, *, sha=SHA, auth=AUTH, cap=CAP, ts=NOW - 5 * 3600,
             order=None):
    q = funding.quote("EUR", 21000, 1, Decimal(5))  # total 220.50
    f = funding.create(conn, run_id=run, item_id=item, content_sha256=sha, q=q, now=ts)
    funding.advance(conn, f.id, "created", expect="created", now=ts,
                    order_id=order or ("O" + auth))
    funding.advance(conn, f.id, "payer_approved", expect="created", now=ts)
    f = funding.advance(conn, f.id, "authorized", expect="payer_approved", now=ts,
                        authorization_id=auth, authorized_minor=q.total_minor)
    funding.record_move(conn, f, "authorization", paypal_id=auth, amount_minor=q.total_minor,
                        now=ts)
    f = funding.advance(conn, f.id, "captured", expect="authorized", now=ts,
                        capture_id=cap, captured_minor=q.total_minor)
    funding.record_move(conn, f, "capture", paypal_id=cap, amount_minor=q.total_minor, now=ts)
    return f


@pytest.fixture()
def w(tmp_path):
    world = World(tmp_path)
    _brief(world.conn, "b_ui", "run_ui")  # the studio's own work: every account sees it
    return world


def run(w, account="acc_1", now=NOW):
    return reconcile(conn=w.conn, client=w.client, ledger=w.ledger, account_id=account,
                     now=now)


def by_move(lines):
    return {ln.move: ln.status for ln in lines}


def test_capture_is_matched_by_id(w):
    captured(w.conn, "run_ui", "cre_1")
    w.sandbox.transactions = [listed(CAP, ref=AUTH)]
    lines = run(w)
    assert by_move(lines) == {"authorization": "matched", "capture": "matched"}
    assert summary(lines)["matched"] == 2
    assert len(w.sandbox.calls("/v1/reporting/transactions")) == 1


def test_custom_field_alone_never_matches(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 600)
    w.sandbox.transactions = [listed("9ZZ99999ZZ999999Z")]  # same content hash, other ids
    assert set(by_move(run(w)).values()) == {"pending"}
    w.sandbox.transactions = [listed("9ZZ99999ZZ999999Z")]
    old = run(w, now=NOW + 5 * 3600)
    assert set(by_move(old).values()) == {"missing"}


def test_recent_move_is_pending_not_missing(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 600)
    lines = run(w)
    assert set(by_move(lines).values()) == {"pending"}
    assert summary(lines)["pending"] == 2 and summary(lines)["missing"] == 0


def test_old_absent_move_is_missing(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 4 * 3600)
    assert 4 * 3600 > RECON_LAG_S
    assert set(by_move(run(w)).values()) == {"missing"}


def test_amount_mismatch(w):
    captured(w.conn, "run_ui", "cre_1")
    w.sandbox.transactions = [listed(CAP, value="100.00", ref=AUTH)]
    assert by_move(run(w))["capture"] == "amount_mismatch"


def test_reconcile_is_per_account(w):
    _brief(w.conn, "b_api1", "run_api1", api="acc_1")
    _brief(w.conn, "b_api2", "run_api2", api="acc_2")
    captured(w.conn, "run_api1", "cre_1", sha="1" * 64, auth="1AU11111AB222222C",
             cap="1CA11111AB222222C")
    captured(w.conn, "run_api2", "cre_2", sha="2" * 64, auth="2AU11111AB222222C",
             cap="2CA11111AB222222C")
    w.sandbox.transactions = [listed("1CA11111AB222222C", custom="1" * 64),
                              listed("2CA11111AB222222C", custom="2" * 64)]
    one = run(w, "acc_1")
    two = run(w, "acc_2")
    assert {ln.paypal_id for ln in one} == {"1AU11111AB222222C", "1CA11111AB222222C"}
    assert {ln.paypal_id for ln in two} == {"2AU11111AB222222C", "2CA11111AB222222C"}
    assert by_move(one)["capture"] == "matched"


def test_no_fundings_means_no_call(w):
    assert run(w, "acc_none") == []
    assert w.sandbox.requests == []


def test_the_call_is_receipted_in_the_accounts_tools_chain(w):
    captured(w.conn, "run_ui", "cre_1")
    run(w)
    (r,) = w.ledger.receipts("acct:acc_1:paypal-tools")
    assert r.details["tool"] == "list_transactions"
