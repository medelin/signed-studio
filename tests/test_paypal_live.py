"""Live PayPal sandbox smoke test. Opt-in: skipped unless ``RUN_PAYPAL_LIVE_TESTS=1`` **and** the sandbox credentials file exist:
``~/.config/muse-pay/paypal-sandbox.env`` (mode 600) with ``PAYPAL_CLIENT_ID`` and
``PAYPAL_CLIENT_SECRET`` of a sandbox REST app (``MUSE_PAY_PAYPAL_ENV`` names another
file). It talks to ``api-m.sandbox.paypal.com`` only, moves no money (an order nobody
approves expires) and checks what the offline tests assume: the OAuth token, an
``AUTHORIZE`` order that keeps the ad hash as ``custom_id``, idempotent creation, and the
``ORDER_NOT_APPROVED`` refusal."""

from __future__ import annotations

import os
import secrets
from decimal import Decimal
from pathlib import Path

import pytest

from studio.funding import quote
from studio.paypal import orders
from studio.paypal.client import PayPalClient, PayPalError, request_id
from studio.settings import read_env_file

ENV = Path(os.environ.get("MUSE_PAY_PAYPAL_ENV", "")
           or Path.home() / ".config" / "muse-pay" / "paypal-sandbox.env")

RUN_LIVE = os.environ.get("RUN_PAYPAL_LIVE_TESTS") == "1" and ENV.is_file()

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not RUN_LIVE, reason="set RUN_PAYPAL_LIVE_TESTS=1 and provide "
                                 f"the PayPal sandbox file {ENV}")]


@pytest.fixture(scope="module")
def client():
    env = read_env_file(ENV)  # refuses a file readable by others
    c = PayPalClient(env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"])
    yield c
    c.close()


def test_sandbox_order_round_trip(client):
    assert client.access_token()
    content = secrets.token_hex(32)
    tag = secrets.token_hex(6)
    q = quote("GBP", 500, 2, Decimal(5))
    body = orders.order_body(q, content_sha256=content, invoice_id=f"fund_smoke{tag}",
                             reference_id=f"smoke_{tag}")
    rid = request_id("smoke", tag, "create")
    created = orders.create(client, body, request_id=rid)
    assert created.status in ("CREATED", "PAYER_ACTION_REQUIRED")
    assert (created.custom_id, created.amount_minor, created.currency) == (content, 1050, "GBP")
    assert created.approve_url.startswith("https://www.sandbox.paypal.com/")
    again = orders.create(client, body, request_id=rid)
    assert again.id == created.id  # same PayPal-Request-Id → the same order
    got = orders.get(client, created.id)
    assert got.custom_id == content
    with pytest.raises(PayPalError) as e:
        orders.authorize(client, created.id, request_id=request_id("smoke", tag, "auth"))
    assert e.value.status == 422 and e.value.issue in ("ORDER_NOT_APPROVED",
                                                       "PAYER_ACTION_REQUIRED")
