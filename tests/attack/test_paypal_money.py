"""Tampered PayPal money logs (attack classes 1-9 of the PayPal plan).

The attacker is the server itself: it holds the ledger key, so every forged receipt below
has a valid ledger signature and a correct hash chain. Only the owner's Ed25519 signature
(which the server does not have) and the PayPal rules of ``studio verify`` (P-V1…P-V4,
P-V6) stand in the way. Runtime refusals (forged or replayed webhooks, a PayPal answer
naming another ad) are checked too.
"""

from __future__ import annotations

import json

import pytest

from studio import funding
from studio.paypal import webhooks
from studio.paypal.mock import WebhookSigner
from studio.receipts import PAYPAL_KIND, PAYPAL_WEBHOOK_KIND, Receipt, verify
from tests.attack.test_paused_only import RUN
from tests.test_paypal_funded import ITEM, Env, run_whole_loop


@pytest.fixture
def log(tmp_path):
    env = Env(tmp_path)
    f, *_ = run_whole_loop(env)
    assert env.verify().ok
    env.f = funding.get(env.conn, f.id)
    return env


def approvals(env, action):
    return [r for r in env.ledger.receipts(RUN) if r.kind == "approval"
            and r.details["action"] == action]


def call(env, step):
    return next(r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_KIND
                and r.details.get("step") == step and r.details.get("ok"))


def forge(env, kind, details):
    """The server appends a receipt of its own making (valid ledger signature)."""
    env.ledger.append(RUN, agent="treasury", kind=kind, provider="paypal", details=details)


def resign(env, mutate, order=None):
    """The server edits receipts it already wrote and re-signs the log with its own
    ledger key: same ids, re-chained hashes, a fresh manifest. Returns the verify report
    (the owner's approver keys are the real ones)."""
    from studio.approvals import Keys
    from studio.receipts import GENESIS, _manifest_bytes

    key = env.ledger._key
    lines = [json.loads(x) for x in env.ledger.export_jsonl().splitlines()]
    receipts = [x for x in lines if "manifest" not in x]
    if order is not None:  # the same receipts, recorded in another order
        by_id = {x["id"]: x for x in receipts}
        receipts = [by_id[i] for i in order]
    heads: dict[str, str] = {}
    counts: dict[str, int] = {}
    for x in receipts:
        mutate(x)
        x["prev"] = heads.get(x["run_id"], GENESIS)
        x["hash"] = Receipt(**{k: v for k, v in x.items() if k not in ("hash", "sig")}
                            ).compute_hash()
        x["sig"] = key.sign(bytes.fromhex(x["hash"])).hex()
        heads[x["run_id"]] = x["hash"]
        counts[x["run_id"]] = counts.get(x["run_id"], 0) + 1
    old = next(x["manifest"] for x in lines if "manifest" in x)
    body = {"seq": old["seq"], "ts": old["ts"], "epoch": old["epoch"],
            "runs": {r: {"head": heads[r], "count": counts[r]} for r in heads}}
    manifest = {**body, "sig": key.sign(_manifest_bytes(body)).hex()}
    path = env.tmp / "forged.jsonl"
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in receipts)
                    + json.dumps({"manifest": manifest}, sort_keys=True) + "\n")
    return verify(path, env.ledger.public_key, approver_keys=Keys.registry(env.conn))


def on_step(step, **changes):
    def mutate(x):
        if x["kind"] == PAYPAL_KIND and x["details"].get("step") == step:
            x["details"].update(changes)
    return mutate


def errors(env):
    report = env.verify()
    assert not report.ok
    return report.errors


def has(errs, text):
    return any(text in e for e in errs), errs


# 1. amount edited after signing -----------------------------------------------------------


def test_1_a_funding_amount_edited_in_the_signed_approval_is_caught(log, tmp_path):
    lines = [json.loads(x) for x in log.ledger.export_jsonl().splitlines()]
    for x in lines:
        if x.get("kind") == "approval" and "funding" in x.get("details", {}):
            x["details"]["funding"]["amount_minor"] += 100
            x["details"]["funding"]["budget_minor"] += 100
            x["hash"] = Receipt(**{k: v for k, v in x.items() if k not in ("hash", "sig")}
                                ).compute_hash()
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    from studio.approvals import Keys

    report = verify(path, log.ledger.public_key, approver_keys=Keys.registry(log.conn))
    assert any("not the one signed" in e or "bad ledger signature" in e
               for e in report.errors), report.errors


def test_1_an_authorization_above_the_signed_amount_is_caught(log):
    create = approvals(log, "campaign.create")[-1]
    d = dict(call(log, "paypal.authorize").details)
    d.update(amount_minor=d["amount_minor"] + 100, approval_id=create.details["pending_id"])
    log.ledger.append(RUN, agent="treasury", kind=PAYPAL_KIND, provider="paypal", details=d)
    assert has(errors(log), "already authorised a paypal.authorize")[0]


def test_1_a_forged_capture_of_more_than_was_authorized_is_caught(log):
    d = dict(call(log, "paypal.capture").details)
    act = approvals(log, "campaign.activate")[-1]
    # a fresh activation approval cannot be faked (human signature), so the server
    # reuses the real one: caught as reuse and as over-capture
    d.update(amount_minor=d["amount_minor"] * 2, capture_id="3C679366HH908993X",
             approval_id=act.details["pending_id"])
    forge(log, PAYPAL_KIND, d)
    errs = errors(log)
    assert has(errs, "already authorised a paypal.capture")[0], errs


# 2. order id swapped ------------------------------------------------------------------------


def test_2_a_capture_of_another_order_is_caught(log):
    report = resign(log, on_step("paypal.capture", order_id="9ZZ99999ZZ999999Z"))
    assert any("another order than the one signed" in e for e in report.errors), \
        report.errors


def test_the_server_resigning_an_untouched_log_still_verifies(log):
    report = resign(log, lambda x: None)
    assert report.ok, report.errors


# 3. capture without activation --------------------------------------------------------------


def test_3_a_capture_riding_on_the_create_approval_is_caught(log):
    d = dict(call(log, "paypal.capture").details)
    create = approvals(log, "campaign.create")[-1]
    d.update(approval_id=create.details["pending_id"], capture_id="3C679366HH908993Y")
    forge(log, PAYPAL_KIND, d)
    assert has(errors(log), "rides on a 'campaign.create' approval")[0]


def test_3_a_capture_naming_no_approval_is_caught(log):
    d = dict(call(log, "paypal.capture").details)
    d.pop("approval_id")
    d["capture_id"] = "3C679366HH908993Z"
    forge(log, PAYPAL_KIND, d)
    assert has(errors(log), "without approval (P-V6)")[0]


# 4. capture above authorization (with a genuine second activation) ---------------------------


def test_4_capture_above_the_authorization_is_caught(log):
    report = resign(log, on_step("paypal.capture", amount_minor=log.f.captured_minor + 1))
    assert any("captured more than authorized" in e for e in report.errors), report.errors


def test_4_an_authorization_above_the_signed_amount_is_caught(log):
    report = resign(log, on_step("paypal.authorize", amount_minor=log.f.total_minor + 1))
    assert any("authorized more than signed" in e for e in report.errors), report.errors


# 5. refund without approval, or twice ----------------------------------------------------------


def test_5_a_refund_without_approval_is_caught(log):
    forge(log, PAYPAL_KIND, {"step": "paypal.refund", "ok": True, "funding_id": log.f.id,
                             "capture_id": log.f.capture_id, "refund_id": "1JU08902781691499",
                             "amount_minor": 1000, "currency": "PLN",
                             "content_sha256": log.f.content_sha256})
    assert has(errors(log), "paypal.refund without approval (P-V6)")[0]


def test_5_a_refund_twice_under_one_approval_is_caught(log):
    from tests.approve import sign_pending

    pending, body = log.funder.request_refund(log.f.id, 1000)
    v = sign_pending(log.conn, log.ledger, pending, body)
    log.funder.execute_refund(v, body)
    assert log.verify().ok
    d = dict(call(log, "paypal.refund").details)
    d["refund_id"] = "1JU08902781691498"
    forge(log, PAYPAL_KIND, d)
    assert has(errors(log), "already authorised a paypal.refund")[0]


def test_5_a_refund_of_another_amount_than_signed_is_caught(log):
    from tests.approve import sign_pending

    pending, body = log.funder.request_refund(log.f.id, 1000)
    v = sign_pending(log.conn, log.ledger, pending, body)
    log.funder.execute_refund(v, body)
    report = resign(log, on_step("paypal.refund", amount_minor=9000))
    assert any("another amount than the one signed" in e for e in report.errors), \
        report.errors


# 6. forged webhook signature ----------------------------------------------------------------


def capture_event(env):
    return {"id": "WH-1FORGED0000001-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": env.f.capture_id, "status": "COMPLETED",
                         "amount": {"currency_code": "PLN", "value": "94.50"},
                         "custom_id": env.f.content_sha256,
                         "supplementary_data": {"related_ids": {"order_id": env.f.order_id}}}}


def test_6_a_webhook_signed_with_another_key_is_refused(log):
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    headers, body = log.funder.signer.sign(capture_event(log), key=other)
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(log.conn, log.ledger, log.funder.verifier, headers, body)
    assert e.value.code == "signature"


def test_6_a_webhook_whose_body_changed_after_signing_is_refused(log):
    headers, body = log.funder.signer.sign(capture_event(log))
    tampered = body.replace(b"94.50", b"9.45")
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(log.conn, log.ledger, log.funder.verifier, headers, tampered)
    assert e.value.code == "signature"


def test_6_a_webhook_from_another_ca_is_refused(log):
    """The cert URL is pinned to the first certificate seen: a stranger's key fails the
    signature check against it."""
    stranger = WebhookSigner(log.funder.signer.webhook_id)
    headers, body = stranger.sign(capture_event(log))
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(log.conn, log.ledger, webhooks.Verifier(
            log.conn, stranger.webhook_id, fetch=stranger.fetch,
            roots=log.funder.signer.roots), headers, body)
    assert e.value.code == "signature"


def test_6_a_certificate_from_an_untrusted_ca_is_refused(tmp_path):
    from studio import db

    conn = db.connect(tmp_path / "fresh.db")
    db.init(conn)
    trusted, stranger = WebhookSigner("WEBHOOK1"), WebhookSigner("WEBHOOK1")
    v = webhooks.Verifier(conn, "WEBHOOK1", fetch=stranger.fetch, roots=trusted.roots)
    headers, body = stranger.sign({"id": "WH-1STRANGER00001-0001"})
    with pytest.raises(webhooks.WebhookRefused) as e:
        v.verify(headers, body)
    assert e.value.code == "cert_chain"
    assert not conn.execute("SELECT 1 FROM paypal_certs").fetchone()  # never pinned


def test_6_a_forged_webhook_receipt_is_caught(log):
    forge(log, PAYPAL_WEBHOOK_KIND, {"event_type": "PAYMENT.CAPTURE.COMPLETED",
                                     "event_id": "WH-1FORGED0000002-0002",
                                     "order_id": log.f.order_id, "amount_minor": 9450,
                                     "currency": "PLN", "verified": "none",
                                     "custom_id_sha256": log.f.content_sha256})
    assert has(errors(log), "without a verified signature")[0]


# 7. webhook replayed ----------------------------------------------------------------------------


def test_7_a_replayed_webhook_is_applied_once(log):
    headers, body = log.funder.signer.sign(capture_event(log))
    before = len(log.ledger.receipts(RUN))
    first = webhooks.apply(log.conn, log.ledger, log.funder.verifier, headers, body,
                           lookup=log.funder.lookup)
    again = webhooks.apply(log.conn, log.ledger, log.funder.verifier, headers, body,
                           lookup=log.funder.lookup)
    # the capture's attested state was recorded when PayPal first sent it: both no-ops
    assert (first.status, again.status) == ("duplicate", "duplicate")
    assert len(log.ledger.receipts(RUN)) == before and log.verify().ok


def test_7_a_replayed_webhook_receipt_is_caught(log):
    receipt = next(r for r in log.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND)
    forge(log, PAYPAL_WEBHOOK_KIND, dict(receipt.details))
    assert has(errors(log), "replayed")[0]


def test_7_a_stale_transmission_is_refused(log):
    import time

    headers, body = log.funder.signer.sign(capture_event(log), now=time.time() - 5 * 86400)
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(log.conn, log.ledger, log.funder.verifier, headers, body)
    assert e.value.code == "stale"


# 8. webhook for an unknown order -----------------------------------------------------------------


def test_8_a_webhook_for_an_unknown_order_gets_no_receipt(log):
    event = capture_event(log)
    event["id"] = "WH-1UNKNOWN000001-0001"
    event["resource"] = {**event["resource"], "id": "3C000000UNKNOWN0X",
                         "supplementary_data": {"related_ids": {"order_id":
                                                                "8ZZ00000UNKNOWN0X"}}}
    headers, body = log.funder.signer.sign(event)
    before = len(log.ledger.receipts(RUN))
    with pytest.raises(webhooks.WebhookRefused) as e:  # PayPal's API has no such capture
        webhooks.apply(log.conn, log.ledger, log.funder.verifier, headers, body,
                       lookup=log.funder.lookup)
    assert e.value.code == "unknown_resource" and len(log.ledger.receipts(RUN)) == before


def test_8_a_receipt_for_an_unfunded_order_is_caught(log):
    forge(log, PAYPAL_WEBHOOK_KIND, {"event_type": "PAYMENT.CAPTURE.COMPLETED",
                                     "event_id": "WH-1FORGED0000003-0003",
                                     "order_id": "8ZZ00000UNKNOWN0X", "verified": "rsa",
                                     "amount_minor": 100, "currency": "PLN"})
    assert has(errors(log), "did not fund (P-V3)")[0]


# 9. custom_id mismatch ------------------------------------------------------------------------


def test_9_a_webhook_attesting_another_ad_is_caught(log):
    forge(log, PAYPAL_WEBHOOK_KIND, {"event_type": "PAYMENT.CAPTURE.COMPLETED",
                                     "event_id": "WH-1FORGED0000004-0004",
                                     "order_id": log.f.order_id, "verified": "rsa",
                                     "amount_minor": 9450, "currency": "PLN",
                                     "custom_id_sha256": "f" * 64})
    assert has(errors(log), "another ad (custom_id)")[0]


def test_9_paypal_answering_with_another_ad_stops_the_create(tmp_path):
    env = Env(tmp_path)
    from studio.funding import FundingError
    from tests.attack.test_paused_only import payload

    pending = env.pending(payload())
    step = env.funder.money.open(run_id=RUN, item_id=ITEM,
                                 content_sha256=pending.content_sha256, currency="PLN",
                                 daily_budget_minor=3000, days=1,
                                 fee_pct=funding.Decimal(5))
    env.funder.fake.approve(step.funding.order_id)
    env.funder.fake.orders[step.funding.order_id].body["purchase_units"][0][
        "custom_id"] = "e" * 64
    with pytest.raises(FundingError) as e:
        env.funder.money.sync_order(step.funding.id)
    assert e.value.code == "mismatch"



def test_order_approved_before_the_owner_signs_still_verifies(tmp_path):
    """The buyer pays first: PayPal's "order approved" webhook can be recorded before the
    owner's signature that funds the order. That honest log verifies."""
    from decimal import Decimal

    from studio.agents import media_buyer
    from studio.approvals import attach_funding
    from tests.test_paypal_funded import payload, sign_pending

    env = Env(tmp_path)
    p = payload()
    pending = env.pending(p, ITEM)
    step = env.funder.money.open(
        run_id=RUN, item_id=ITEM, content_sha256=pending.content_sha256, currency="PLN",
        daily_budget_minor=p["campaign"]["daily_budget_minor"], days=3, fee_pct=Decimal(5))
    order = step.funding.order_id
    env.funder.fake.approve(order)
    event = {"id": "WH-1ORDERAPPROVED0-0001", "event_type": "CHECKOUT.ORDER.APPROVED",
             "resource": {"id": order, "status": "APPROVED", "purchase_units": [
                 {"custom_id": pending.content_sha256,
                  "amount": {"currency_code": "PLN", "value": "94.50"}}]}}
    headers, body = env.funder.signer.sign(event)
    webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                   lookup=env.funder.lookup)
    hooks = [r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND]
    assert hooks and not any(r.kind == "approval" for r in env.ledger.receipts(RUN))
    env.funder.money.sync_order(step.funding.id)
    f = funding.get(env.conn, step.funding.id)
    fresh, funded = attach_funding(env.conn, pending.id, p, f.block(), ledger=env.ledger)
    v = sign_pending(env.conn, env.ledger, fresh, funded)
    media_buyer.create(env.conn, env.ledger, env.adapter, v, funded, env.tmp,
                       funder=env.funder)
    report = env.verify()
    assert report.ok, report.errors


def test_a_webhook_for_an_order_never_signed_is_still_caught_at_the_end(log):
    forge(log, PAYPAL_WEBHOOK_KIND, {"event_type": "CHECKOUT.ORDER.APPROVED",
                                     "event_id": "WH-1FORGED0000009-0009",
                                     "order_id": "8ZZ00000NEVERSIGNED", "verified": "rsa",
                                     "resource_kind": "order", "status": "APPROVED",
                                     "event_class": "order.approved", "resource_source": "api",
                                     "amount_minor": 100, "currency": "PLN"})
    assert has(errors(log), "did not fund (P-V3)")[0]


def test_only_order_approved_may_come_before_the_signature(log):
    """A capture or authorization webhook for an order not funded by then is caught at once,
    even if a later signature names the order."""
    forge(log, PAYPAL_WEBHOOK_KIND, {"event_type": "PAYMENT.CAPTURE.COMPLETED",
                                     "event_id": "WH-1FORGED0000010-0010",
                                     "order_id": "8ZZ00000UNKNOWN1X", "verified": "rsa",
                                     "resource_kind": "capture", "status": "COMPLETED",
                                     "event_class": "capture.completed", "resource_source": "api",
                                     "capture_id": "9CAP0000000000001",
                                     "amount_minor": 100, "currency": "PLN"})
    assert has(errors(log), "did not fund (P-V3)")[0]


def test_an_approved_order_the_owner_never_signs_still_verifies(tmp_path):
    """The buyer approves in PayPal and the owner walks away: no signature, no money moved,
    an honest log."""
    from decimal import Decimal

    from tests.test_paypal_funded import payload

    env = Env(tmp_path)
    p = payload()
    pending = env.pending(p, ITEM)
    step = env.funder.money.open(
        run_id=RUN, item_id=ITEM, content_sha256=pending.content_sha256, currency="PLN",
        daily_budget_minor=p["campaign"]["daily_budget_minor"], days=3, fee_pct=Decimal(5))
    order = step.funding.order_id
    env.funder.fake.approve(order)
    event = {"id": "WH-1ORDERAPPROVED0-0002", "event_type": "CHECKOUT.ORDER.APPROVED",
             "resource": {"id": order, "status": "APPROVED", "purchase_units": [
                 {"custom_id": pending.content_sha256,
                  "amount": {"currency_code": "PLN", "value": "94.50"}}]}}
    headers, body = env.funder.signer.sign(event)
    webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                   lookup=env.funder.lookup)
    report = env.verify()
    assert report.ok, report.errors
