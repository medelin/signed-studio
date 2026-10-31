"""Billing with Stripe: a verified webhook records the subscription once, mails the
owner, and only a live key lets a subscription lift the account's tier."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import billing
from studio.app import create_app
from studio.billing import BillingConfig
from tests.test_ui_studio import PASSWORD, csrf, settings

HOOK = "whsec_" + "k" * 32
TEST = {"STRIPE_SECRET_KEY": "sk_test_" + "a" * 24, "STRIPE_WEBHOOK_SECRET": HOOK,
        "STRIPE_PRICE_ID": "price_123", "STUDIO_PUBLIC_URL": "https://testserver",
        "STUDIO_MAIL_TRANSPORT": "mock", "STUDIO_MAIL_FROM": "Muse <muse@example.org>"}


def signed(body: bytes, secret: str = HOOK, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def event(kind, obj, *, eid="evt_1", livemode=False, created=None):
    body = {"id": eid, "object": "event", "type": kind, "livemode": livemode,
            "data": {"object": obj}}
    if created is not None:
        body["created"] = created
    return json.dumps(body).encode()


def post(c, body, header=None):
    return c.post("/webhooks/stripe", content=body,
                  headers={"Stripe-Signature": header or signed(body),
                           "Content-Type": "application/json"})


def test_config_refuses_a_live_key_unless_asked_and_half_configs():
    assert not BillingConfig.from_env({}).enabled
    assert BillingConfig.from_env(TEST).enabled and not BillingConfig.from_env(TEST).live
    live = {**TEST, "STRIPE_SECRET_KEY": "sk_live_" + "a" * 24}
    with pytest.raises(ValueError, match="STUDIO_BILLING_LIVE"):
        BillingConfig.from_env(live)
    assert BillingConfig.from_env({**live, "STUDIO_BILLING_LIVE": "1"}).live
    with pytest.raises(ValueError, match="whsec_"):
        BillingConfig.from_env({**TEST, "STRIPE_WEBHOOK_SECRET": "x"})


@pytest.mark.parametrize("header,ok", [
    (None, True), ("t=1,v1=00", False), ("", False), ("v1=abc", False)])
def test_signature(header, ok):
    body = b'{"x": 1}'
    assert billing.verify_signature(body, header if header is not None else signed(body),
                                    HOOK) is ok


def test_an_old_signature_is_refused():
    body = b"{}"
    assert not billing.verify_signature(body, signed(body, ts=int(time.time()) - 600), HOOK)


def _app(tmp_path, **env):
    app = create_app(settings(tmp_path, **{**TEST, **env}))
    ola = app.state.accounts.create("ola", PASSWORD, email="ola@example.com")
    return app, ola


SUB = {"id": "sub_1", "object": "subscription", "customer": "cus_1", "status": "active",
       "cancel_at_period_end": False, "metadata": {},
       "items": {"data": [{"current_period_end": 1_800_000_000}]}}


def test_webhook_records_the_subscription_once_and_mails_the_owner(tmp_path):
    app, ola = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        done = event("checkout.session.completed",
                     {"id": "cs_1", "customer": "cus_1", "subscription": "sub_1",
                      "client_reference_id": ola.id, "metadata": {"account_id": ola.id},
                      "customer_details": {"email": "ola@example.com"}}, eid="evt_a")
        assert post(c, done).status_code == 200
        assert post(c, event("customer.subscription.created", SUB, eid="evt_b")).json() == \
            {"ok": True}
        assert post(c, event("customer.subscription.created", SUB, eid="evt_b")).json() == \
            {"ok": True}  # Stripe retries: applied once
        app.state.notifier.flush()
    row = billing.of(app.state.conn, ola.id)
    assert row.status == "active" and row.period_end == 1_800_000_000
    assert row.customer_id == "cus_1"
    subjects = [m.subject for m in app.state.notifier.outbox]
    assert subjects.count("Your Muse Studio Lite subscription is active") == 1
    # a test key never lifts the tier
    assert app.state.accounts.get(ola.id).tier == "full"  # operator-created stays full
    trial = app.state.accounts.create_external("google", "g1", email="t@example.com")
    with TestClient(app, base_url="https://testserver") as c:
        sub = {**SUB, "id": "sub_2", "customer": "cus_2", "metadata": {"account_id": trial.id}}
        post(c, event("customer.subscription.created", sub, eid="evt_c"))
    assert app.state.accounts.get(trial.id).tier == "trial"


def test_a_live_subscription_lifts_and_its_end_drops_the_tier(tmp_path):
    app, _ = _app(tmp_path, STRIPE_SECRET_KEY="sk_live_" + "b" * 24, STUDIO_BILLING_LIVE="1")
    trial = app.state.accounts.create_external("google", "g1", email="t@example.com")
    sub = {**SUB, "metadata": {"account_id": trial.id}}
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("customer.subscription.created", sub, eid="e1", livemode=True))
        assert app.state.accounts.get(trial.id).tier == "full"
        post(c, event("customer.subscription.deleted", sub, eid="e2", livemode=True))
        assert app.state.accounts.get(trial.id).tier == "trial"


def test_a_forged_webhook_changes_nothing(tmp_path):
    app, ola = _app(tmp_path)
    body = event("customer.subscription.created", {**SUB, "metadata": {"account_id": ola.id}})
    with TestClient(app, base_url="https://testserver") as c:
        assert post(c, body, header=signed(body, secret="whsec_wrong")).status_code == 400
    assert billing.of(app.state.conn, ola.id) is None


def test_paid_and_failed_invoices_are_mailed(tmp_path):
    app, ola = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("customer.subscription.created",
                      {**SUB, "metadata": {"account_id": ola.id}}, eid="s"))
        inv = {"id": "in_1", "customer": "cus_1",
               "hosted_invoice_url": "https://invoice.stripe.com/i/x"}
        post(c, event("invoice.paid", inv, eid="p"))
        post(c, event("invoice.payment_failed", inv, eid="f"))
        app.state.notifier.flush()
    assert billing.of(app.state.conn, ola.id).last_invoice_url.endswith("/i/x")
    subjects = [m.subject for m in app.state.notifier.outbox]
    assert "Payment received" in subjects and "Your payment did not go through" in subjects


def test_checkout_sends_the_account_to_stripe(tmp_path):
    app, ola = _app(tmp_path)
    seen = []

    def stripe(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/1"})

    app.state.stripe = billing.StripeApi(app.state.settings.billing,
                                         transport=httpx.MockTransport(stripe))
    with TestClient(app, base_url="https://testserver") as c:
        c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})
        page = c.get("/account").text
        assert 'id="billing"' in page and "4242 4242 4242 4242" in page
        r = c.post("/account/billing/checkout",
                   data={"csrf": csrf(c), "buyer": "person", "recurring_consent": "1",
                         "immediate_start": "1"},
                   follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(
            "https://checkout.stripe.com/")
        assert c.post("/account/billing/checkout", data={"csrf": "x"},
                      follow_redirects=False).status_code == 403
    seen = [r for r in seen if r.method == "POST"]  # the account page reads the price
    form = parse_qs(seen[0].content.decode())
    assert form["client_reference_id"] == [ola.id] and form["mode"] == ["subscription"]
    assert form["line_items[0][price]"] == ["price_123"]
    assert form["customer_email"] == ["ola@example.com"]
    assert seen[0].headers["authorization"].startswith("Basic ")


def test_no_billing_ui_without_configuration(tmp_path):
    app = create_app(settings(tmp_path))
    app.state.accounts.create("ola", PASSWORD)
    with TestClient(app, base_url="https://testserver") as c:
        c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})
        assert 'id="billing"' not in c.get("/account").text
        assert c.post("/webhooks/stripe", content=b"{}").status_code == 404


LIVE = {"STRIPE_SECRET_KEY": "sk_live_" + "b" * 24, "STUDIO_BILLING_LIVE": "1"}
ONCE = {"id": "cs_9", "mode": "payment", "payment_status": "paid", "customer": "cus_9"}


def test_a_live_one_off_month_lifts_the_tier_until_it_ends(tmp_path):
    app, _ = _app(tmp_path, **LIVE)
    trial = app.state.accounts.create_external("google", "g1", email="t@example.com")
    now = int(time.time())
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("checkout.session.completed",
                      {**ONCE, "metadata": {"account_id": trial.id}}, eid="o1",
                      livemode=True, created=now))
    assert app.state.accounts.get(trial.id).tier == "full"
    conn = app.state.conn
    assert billing.expire_access(conn, live_key=True, now=now + 29 * 86400) == 0
    assert billing.expire_access(conn, live_key=False, now=now + 31 * 86400) == 0
    assert billing.expire_access(conn, live_key=True, now=now + 31 * 86400) == 1
    assert app.state.accounts.get(trial.id).tier == "trial"
    assert billing.of(conn, trial.id).access_until == 0  # dropped once
    app.state.accounts.set_tier(trial.username, "full")  # a later operator grant stands
    assert billing.expire_access(conn, live_key=True, now=now + 32 * 86400) == 0
    assert app.state.accounts.get(trial.id).tier == "full"


def test_an_ended_one_off_month_is_swept_on_the_next_request(tmp_path):
    app, _ = _app(tmp_path, **LIVE)
    trial = app.state.accounts.create_external("google", "g1", email="t@example.com")
    app.state.accounts.set_tier(trial.username, "full")
    with app.state.conn.lock:
        app.state.conn.execute("INSERT INTO billing (account_id, livemode, access_until, "
                               "updated_ts) VALUES (?, 1, ?, ?)",
                               (trial.id, time.time() - 1, time.time()))
    with TestClient(app, base_url="https://testserver") as c:
        c.get("/pricing")
    assert app.state.accounts.get(trial.id).tier == "trial"


def test_an_older_subscription_event_does_not_overwrite_a_newer_one(tmp_path):
    app, ola = _app(tmp_path)
    sub = {**SUB, "metadata": {"account_id": ola.id}}
    now = int(time.time())
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("customer.subscription.updated", sub, eid="new", created=now))
        late = event("customer.subscription.updated", {**sub, "status": "incomplete"},
                     eid="old", created=now - 60)
        assert post(c, late).status_code == 200
    assert billing.of(app.state.conn, ola.id).status == "active"
    seen = app.state.conn.execute("SELECT id FROM stripe_events ORDER BY id").fetchall()
    assert [r[0] for r in seen] == ["new", "old"]  # recorded as seen all the same


def test_the_end_of_a_replaced_subscription_keeps_the_live_one(tmp_path):
    app, ola = _app(tmp_path)
    meta = {"account_id": ola.id}
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("customer.subscription.created", {**SUB, "id": "sub_new",
                                                        "metadata": meta}, eid="a"))
        post(c, event("customer.subscription.deleted", {**SUB, "id": "sub_old",
                                                        "metadata": meta}, eid="b"))
    row = app.state.conn.execute("SELECT status, subscription_id FROM billing "
                                 "WHERE account_id = ?", (ola.id,)).fetchone()
    assert tuple(row) == ("active", "sub_new")


def test_a_failure_after_the_event_is_recorded_is_retried_whole(tmp_path):
    """The tier change is part of the event's transaction: a failure rolls the event back,
    so Stripe's retry (same id) applies it instead of being dropped as a duplicate."""
    app, _ = _app(tmp_path, **LIVE)
    trial = app.state.accounts.create_external("google", "g1", email="t@example.com")
    body = event("customer.subscription.created", {**SUB, "metadata": {"account_id": trial.id}},
                 eid="e1", livemode=True)
    conn = app.state.conn
    with conn.lock:
        conn.execute("CREATE TEMP TRIGGER no_tier BEFORE UPDATE OF tier ON accounts "
                     "BEGIN SELECT RAISE(ABORT, 'down'); END")
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        assert post(c, body).status_code == 500
        with conn.lock:
            conn.execute("DROP TRIGGER no_tier")
        assert post(c, body).status_code == 200
    assert app.state.accounts.get(trial.id).tier == "full"


def test_a_lost_mail_does_not_fail_the_webhook(tmp_path, monkeypatch):
    app, _ = _app(tmp_path, **LIVE)
    trial = app.state.accounts.create_external("google", "g1", email="t@example.com")

    def down(*a, **kw):
        raise RuntimeError("smtp")

    monkeypatch.setattr(app.state.notifier, "security", down)
    with TestClient(app, base_url="https://testserver") as c:
        assert post(c, event("customer.subscription.created",
                             {**SUB, "metadata": {"account_id": trial.id}}, eid="e1",
                             livemode=True)).status_code == 200
    assert app.state.accounts.get(trial.id).tier == "full"


def test_a_disabled_account_keeps_its_billing_current_without_mails(tmp_path):
    app, _ = _app(tmp_path, **LIVE)
    store = app.state.accounts
    bob = store.create("bob", PASSWORD, email="bob@example.com")
    store.set_tier("bob", "trial")
    store.set_status("bob", "disabled")
    sub = {**SUB, "metadata": {"account_id": bob.id}}
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("customer.subscription.created", sub, eid="e1", livemode=True))
        app.state.notifier.flush()
    row = billing.of(app.state.conn, bob.id)
    assert row is not None and row.status == "active"
    assert app.state.notifier.outbox == []  # mails held back while disabled
    assert store.set_status("bob", "active").tier == "full"  # re-enabled: not stale


def test_an_invoice_with_null_subscription_details_is_handled(tmp_path):
    app, ola = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        post(c, event("customer.subscription.created",
                      {**SUB, "metadata": {"account_id": ola.id}}, eid="s"))
        inv = {"id": "in_1", "customer": "cus_1", "subscription_details": None,
               "parent": None, "hosted_invoice_url": "https://invoice.stripe.com/i/y"}
        assert post(c, event("invoice.paid", inv, eid="p")).status_code == 200
        up = {**inv, "lines": {"data": []}}
        assert post(c, event("invoice.upcoming", up, eid="u")).status_code == 200
    assert billing.of(app.state.conn, ola.id).last_invoice_url.endswith("/i/y")
