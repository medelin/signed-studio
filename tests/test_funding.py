"""The money model: amounts in minor units, the quote and fee, the signed funding block,
the state machine, reconciliation, and the funding store."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from studio import db, funding
from studio.funding import (
    TRANSITIONS,
    FundingError,
    format_amount,
    funding_block,
    funding_block_problem,
    funding_sha256,
    parse_amount,
    quote,
    reconcile,
)

H = "ab" * 32


@pytest.mark.parametrize("minor,cur,text", [(4800, "GBP", "48.00"), (5, "USD", "0.05"),
                                            (0, "EUR", "0.00"), (4800, "JPY", "4800"),
                                            (123456, "PLN", "1234.56")])
def test_amounts_round_trip(minor, cur, text):
    assert format_amount(minor, cur) == text
    assert parse_amount(text, cur) == minor


@pytest.mark.parametrize("value,cur", [("48.005", "GBP"), ("1.5", "JPY"), ("-1.00", "GBP"),
                                       ("1e3", "GBP"), (48.0, "GBP"), ("01.00", "GBP"),
                                       ("1.00", "XYZ"), ("", "GBP")])
def test_bad_amounts_are_refused_never_rounded(value, cur):
    with pytest.raises(FundingError):
        parse_amount(value, cur)


def test_one_decimal_is_read_as_tenths():
    assert parse_amount("48.5", "GBP") == 4850


def test_quote_budget_fee_and_total():
    q = quote("GBP", 1500, 3, Decimal(5))
    assert (q.budget_minor, q.fee_minor, q.total_minor) == (4500, 225, 4725)
    assert q.amount() == "47.25" and q.amount("fee") == "2.25"


def test_fee_rounds_half_up_to_the_minor_unit():
    assert quote("GBP", 10, 1, Decimal(5)).fee_minor == 1  # 0.5 → 1
    assert quote("GBP", 9, 1, Decimal(5)).fee_minor == 0  # 0.45 → 0
    assert quote("GBP", 1000, 1, Decimal("2.5")).fee_minor == 25


@pytest.mark.parametrize("kwargs,code", [
    ({"currency": "XYZ"}, "currency"), ({"daily_budget_minor": 0}, "amount"),
    ({"days": 0}, "amount"), ({"days": 91}, "days"), ({"daily_budget_minor": True}, "amount"),
    ({"max_daily_minor": 1000}, "ceiling"), ({"fee_pct": Decimal(31)}, "fee"),
    ({"daily_budget_minor": 5_000_000, "days": 3}, "ceiling")])
def test_quote_refuses(kwargs, code):
    args = {"currency": "GBP", "daily_budget_minor": 1500, "days": 3, "fee_pct": Decimal(5),
            **kwargs}
    cur, daily, days, pct = (args.pop(k) for k in ("currency", "daily_budget_minor", "days",
                                                   "fee_pct"))
    with pytest.raises(FundingError) as e:
        quote(cur, daily, days, pct, **args)
    assert e.value.code == code


def block(**over):
    q = quote("GBP", 1500, 3, Decimal(5))
    b = funding_block(item_id="cre_1", content_sha256=H, order_id="5O190127TN364715T", q=q)
    return {**b, **over}


def test_the_funding_block_is_closed_and_hash_stable():
    b = block()
    assert funding_block_problem(b) is None
    assert b["amount_minor"] == 4725 and b["intent"] == "AUTHORIZE"
    assert funding_sha256(b) == funding_sha256(dict(reversed(list(b.items()))))
    assert funding_sha256(b) != funding_sha256(block(amount_minor=4726, fee_minor=226))


@pytest.mark.parametrize("over,problem", [
    ({"extra": 1}, "fields"), ({"order_id": "x/../y"}, "order_id"),
    ({"intent": "CAPTURE"}, "intent"), ({"content_sha256": "AB" * 32}, "content_sha256"),
    ({"amount_minor": 4726}, "amount_minor"), ({"currency": "XYZ"}, "currency"),
    ({"payee": "someone@example.com"}, "payee"), ({"fee_minor": -1}, "fee_minor"),
    ({"schema": "funding/v2"}, "schema")])
def test_malformed_blocks_name_the_field(over, problem):
    assert funding_block_problem(block(**over)) == problem


def test_the_state_graph_has_no_way_back():
    assert TRANSITIONS["captured"] >= {"refunded"}
    assert "authorized" not in TRANSITIONS["captured"]
    assert "captured" not in TRANSITIONS["voided"]
    assert all(not TRANSITIONS[s] for s in funding.FINAL)
    reachable = {t for nxt in TRANSITIONS.values() for t in nxt} | {"created"}
    assert reachable == set(funding.STATES)


def test_reconcile_returns_unspent_budget_with_its_share_of_the_fee():
    # funded 45.00 + 5 % = 47.25; Meta spent 31.20 → fee due 1.56; back 14.49
    s = reconcile(currency="GBP", budget_minor=4500, fee_minor=225, captured_minor=4725,
                  spend_minor=3120, refunded_minor=0, fee_pct=Decimal(5))
    assert (s.spend_counted_minor, s.fee_due_minor, s.refundable_minor) == (3120, 156, 1449)
    assert s.captured_minor == s.spend_counted_minor + s.fee_due_minor + s.refundable_minor
    assert s.overspend_minor == 0


def test_reconcile_with_overspend_and_earlier_refunds():
    s = reconcile(currency="GBP", budget_minor=4500, fee_minor=225, captured_minor=4725,
                  spend_minor=5000, refunded_minor=0, fee_pct=Decimal(5))
    assert (s.refundable_minor, s.overspend_minor, s.fee_due_minor) == (0, 500, 225)
    s = reconcile(currency="GBP", budget_minor=4500, fee_minor=225, captured_minor=4725,
                  spend_minor=3120, refunded_minor=1000, fee_pct=Decimal(5))
    assert s.refundable_minor == 449
    s = reconcile(currency="GBP", budget_minor=4500, fee_minor=225, captured_minor=4725,
                  spend_minor=0, refunded_minor=0, fee_pct=Decimal(5))
    assert s.refundable_minor == 4725


def test_reconcile_refuses_negative_numbers():
    with pytest.raises(FundingError):
        reconcile(currency="GBP", budget_minor=1, fee_minor=0, captured_minor=1,
                  spend_minor=-1, refunded_minor=0, fee_pct=Decimal(5))


# -- store --------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    return c


def test_create_advance_and_moves(conn):
    q = quote("GBP", 1500, 3, Decimal(5))
    f = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256=H, q=q)
    assert f.state == "created" and f.order_id is None and f.total_minor == 4725
    assert f.fee_pct == Decimal(5)
    f = funding.advance(conn, f.id, "created", expect="created", order_id="5O190127TN364715T")
    assert f.order_id == "5O190127TN364715T"
    assert funding.by_order(conn, "5O190127TN364715T") == f
    f = funding.advance(conn, f.id, "payer_approved", expect="created")
    f = funding.advance(conn, f.id, "authorized", expect="payer_approved",
                        authorization_id="0VF52814937998046", authorized_minor=4725)
    funding.record_move(conn, f, "authorization", paypal_id="0VF52814937998046",
                        amount_minor=4725, receipt_id="r1")
    assert [m["move"] for m in funding.moves(conn, f.id)] == ["authorization"]
    assert funding.of_item(conn, "cre_1") == [f]
    assert f.block()["order_id"] == "5O190127TN364715T"


def test_advance_refuses_edges_outside_the_graph_and_stale_states(conn):
    f = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256=H,
                       q=quote("GBP", 1000, 1, Decimal(5)))
    with pytest.raises(FundingError) as e:
        funding.advance(conn, f.id, "captured", expect="created")
    assert e.value.code == "wrong_state"
    funding.advance(conn, f.id, "payer_approved", expect="created")
    with pytest.raises(FundingError) as e:  # someone else moved it first
        funding.advance(conn, f.id, "payer_approved", expect="created")
    assert e.value.code in ("conflict", "wrong_state")
    with pytest.raises(ValueError):
        funding.advance(conn, f.id, "authorized", expect="payer_approved", state="captured")


def test_the_database_refuses_capturing_more_than_was_authorized(conn):
    f = funding.create(conn, run_id="run_1", item_id="cre_1", content_sha256=H,
                       q=quote("GBP", 1000, 1, Decimal(5)))
    funding.advance(conn, f.id, "payer_approved", expect="created")
    funding.advance(conn, f.id, "authorized", expect="payer_approved",
                    authorization_id="0VF52814937998046", authorized_minor=1050)
    with pytest.raises(sqlite3.IntegrityError):
        funding.advance(conn, f.id, "captured", expect="authorized",
                        capture_id="3C679366HH908993F", captured_minor=1051)
    assert funding.get(conn, f.id).state == "authorized"


def test_create_refuses_a_non_hash_and_a_free_text_item(conn):
    q = quote("GBP", 1000, 1, Decimal(5))
    with pytest.raises(FundingError):
        funding.create(conn, run_id="r", item_id="cre_1", content_sha256="nothex", q=q)
    with pytest.raises(FundingError):
        funding.create(conn, run_id="r", item_id="call me 600123456", content_sha256=H, q=q)
