"""A declined authorize keeps PayPal's issue and reason, frees the order slot (the funding
becomes ``failed``) and the failure receipt carries PayPal's ``debug_id``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from studio import funding
from studio.agents import media_buyer
from studio.receipts import PAYPAL_KIND
from tests.approve import sign_pending
from tests.attack.test_paused_only import RUN, payload
from tests.test_paypal_funded import ITEM, Env


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_declined_authorize_frees_the_order_slot(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    env.funder.fake.decline_authorize = "INSTRUMENT_DECLINED"
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    result = media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                                funder=env.funder)
    assert result["funding_error"] == "INSTRUMENT_DECLINED"
    assert "declined" in result["funding_reason"]
    assert funding.get(env.conn, f.id).state == "failed"
    last = [r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_KIND][-1].details
    assert last["issue"] == "INSTRUMENT_DECLINED" and last["debug_id"]
    # a fresh order for the same ad is possible
    again = env.funder.money.open(run_id=RUN, item_id=ITEM, content_sha256=f.content_sha256,
                                  currency="PLN", daily_budget_minor=3000, days=3,
                                  fee_pct=Decimal(5))
    assert again.funding.id != f.id and again.funding.state == "created"
    report = env.verify()
    assert report.ok, report.errors


def test_an_ambiguous_authorize_is_not_failed(env):
    p = payload()
    fresh, funded, f = env.fund(p)
    env.funder.fake.fail_next(*[503] * 4)
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    result = media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                                funder=env.funder)
    assert "funding_error" in result
    assert funding.get(env.conn, f.id).state == "payer_approved"
