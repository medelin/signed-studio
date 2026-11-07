"""PayPal webhook verification: headers, cert URL allowlist, chain and pin, CRC32 message
and RSA signature, freshness, dedupe, receipts without payer data, the owner-only export
and the offline re-check (``studio verify --paypal-events``)."""

from __future__ import annotations

import json
import time

import pytest

from studio import cli
from studio.paypal import webhooks
from studio.paypal.mock import MOCK_CERT_URL, WebhookSigner
from studio.receipts import PAYPAL_WEBHOOK_KIND
from tests.attack.test_paused_only import RUN
from tests.test_paypal_funded import Env, run_whole_loop


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    run_whole_loop(e)
    return e


@pytest.mark.parametrize("url,ok", [
    ("https://api.sandbox.paypal.com/v1/notifications/certs/CERT-1", True),
    ("https://api.paypal.com/v1/notifications/certs/CERT-1", True),
    ("http://api.paypal.com/v1/notifications/certs/CERT-1", False),
    ("https://api.paypal.com/v1/oauth2/token", False),
    ("https://api.paypal.com/v1/notifications/certsX/1", False),
    ("https://api.paypal.com/v1/notifications/certs", False),
    ("https://api.paypal.com:8443/certs", False),
    ("https://evil.example.com/certs", False),
    ("https://api.paypal.com.evil.example.com/certs", False),
    ("https://user@api.paypal.com/certs", False),
    ("https://169.254.169.254/latest/meta-data", False),
    ("file:///etc/passwd", False)])
def test_the_cert_url_is_an_allowlist(url, ok):
    assert webhooks.cert_url_ok(url) is ok


def test_a_cert_url_outside_the_allowlist_is_never_fetched(env):
    fetched = []
    v = webhooks.Verifier(env.conn, "W1", fetch=lambda u: fetched.append(u) or b"",
                          roots=env.funder.signer.roots)
    headers, body = env.funder.signer.sign({"id": "WH-1AAAAAAAA0001-0001"})
    headers["paypal-cert-url"] = "https://169.254.169.254/x"
    with pytest.raises(webhooks.WebhookRefused) as e:
        v.verify(headers, body)
    assert e.value.code == "cert_url" and not fetched


@pytest.mark.parametrize("drop", ["paypal-transmission-id", "paypal-transmission-time",
                                  "paypal-transmission-sig", "paypal-cert-url",
                                  "paypal-auth-algo"])
def test_every_transmission_header_is_required(env, drop):
    headers, body = env.funder.signer.sign({"id": "WH-1AAAAAAAA0001-0001"})
    headers.pop(drop)
    with pytest.raises(webhooks.WebhookRefused) as e:
        env.funder.verifier.verify(headers, body)
    assert e.value.code == "headers"


def test_another_algorithm_or_no_webhook_id_is_refused(env):
    headers, body = env.funder.signer.sign({"id": "WH-1AAAAAAAA0001-0001"})
    with pytest.raises(webhooks.WebhookRefused) as e:
        env.funder.verifier.verify({**headers, "paypal-auth-algo": "SHA1withRSA"}, body)
    assert e.value.code == "auth_algo"
    v = webhooks.Verifier(env.conn, "", fetch=env.funder.signer.fetch,
                          roots=env.funder.signer.roots)
    with pytest.raises(webhooks.WebhookRefused) as e:
        v.verify(headers, body)
    assert e.value.code == "not_configured"


def test_a_signature_for_another_webhook_id_is_refused(env):
    headers, body = env.funder.signer.sign({"id": "WH-1AAAAAAAA0001-0001"})
    v = webhooks.Verifier(env.conn, "ANOTHERWEBHOOK", fetch=env.funder.signer.fetch,
                          roots=env.funder.signer.roots)
    with pytest.raises(webhooks.WebhookRefused) as e:
        v.verify(headers, body)
    assert e.value.code == "signature"


def test_an_expired_certificate_is_refused(tmp_path):
    from studio import db

    conn = db.connect(tmp_path / "x.db")
    db.init(conn)
    signer = WebhookSigner("W1", now=time.time() - 500 * 86400)  # valid for 400 days
    v = webhooks.Verifier(conn, "W1", fetch=signer.fetch, roots=signer.roots)
    headers, body = signer.sign({"id": "WH-1AAAAAAAA0001-0001"})
    with pytest.raises(webhooks.WebhookRefused) as e:
        v.verify(headers, body)
    assert e.value.code == "cert_expired"


def test_the_cert_is_pinned_after_first_use(env):
    rows = env.conn.execute("SELECT url, sha256 FROM paypal_certs").fetchall()
    assert rows and rows[0][0] == MOCK_CERT_URL


def test_webhook_receipts_hold_no_payer_data(env):
    for r in env.ledger.receipts(RUN):
        if r.kind == PAYPAL_WEBHOOK_KIND:
            text = json.dumps(r.details)
            assert "email" not in text and "payer" not in text and "name" not in text
            assert r.details["verified"] == "rsa" and len(r.details["cert_sha256"]) == 64


def test_the_route_needs_no_session_and_refuses_forgeries(tmp_path):
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from tests.test_ui_studio import settings

    a = create_app(settings(tmp_path, PAYPAL_ADAPTER="mock", STUDIO_REQUIRE_LOGIN="1"))
    signer = a.state.funder.signer
    with TestClient(a, base_url="https://testserver") as c:
        headers, body = signer.sign({"id": "WH-1ROUTE0000001-0001",
                                     "event_type": "PAYMENT.CAPTURE.COMPLETED",
                                     "resource": {"id": "3C000000NOTOURS0X"}})
        r = c.post("/webhooks/paypal", content=body, headers=headers)
        # PayPal's API does not know that capture: refused, nothing stored
        assert r.status_code == 400 and r.json()["error"] == "unknown_resource"
        assert not a.state.conn.execute("SELECT 1 FROM paypal_events").fetchone()
        a.state.funder.money.client.get = _down
        headers, body = signer.sign({"id": "WH-1ROUTE0000002-0002",
                                     "event_type": "PAYMENT.CAPTURE.COMPLETED",
                                     "resource": {"id": "3C000000NOTOURS0X"}})
        r = c.post("/webhooks/paypal", content=body, headers=headers)
        assert r.status_code == 503 and r.json()["error"] == "lookup_failed"  # PayPal retries
        forged = c.post("/webhooks/paypal", content=body.replace(b"0X", b"0Y"),
                        headers=headers)
        assert forged.status_code == 400 and forged.json()["error"] == "signature"


def test_export_and_offline_recheck(env, tmp_path, capsys):
    out = tmp_path / "export"
    out.mkdir()
    (out / "paypal-events.jsonl").write_text(webhooks.export_events(env.conn))
    assert webhooks.export_certs(env.conn, out / "certs") == 1
    receipts = env.ledger.receipts()
    wid, roots = env.funder.signer.webhook_id, env.funder.signer.roots
    assert webhooks.recheck(out / "paypal-events.jsonl", out / "certs", receipts,
                            webhook_id=wid, roots=roots) == []
    # one byte of a recorded event changed: PayPal's signature no longer verifies
    lines = (out / "paypal-events.jsonl").read_text().splitlines()
    ev = json.loads(lines[0])
    ev["body"] = ev["body"].replace('"COMPLETED"', '"COMPLETEX"').replace(
        '"CREATED"', '"CREATEX"')
    (out / "bad.jsonl").write_text("\n".join([json.dumps(ev), *lines[1:]]) + "\n")
    problems = webhooks.recheck(out / "bad.jsonl", out / "certs", receipts, webhook_id=wid,
                                roots=roots)
    assert any("does not verify" in p for p in problems)
    # the CLI: a valid log and events verify; tampered events fail with exit 2
    from studio.approvals import Keys

    (tmp_path / "r.jsonl").write_text(env.ledger.export_jsonl())
    keys = {a: k.public_bytes_raw().hex() for a, k in Keys.registry(env.conn).items()}
    (tmp_path / "keys.json").write_text(json.dumps(keys))
    from cryptography.hazmat.primitives.serialization import Encoding

    (tmp_path / "roots.pem").write_bytes(b"".join(r.public_bytes(Encoding.PEM) for r in roots))
    base = [str(tmp_path / "r.jsonl"), "--key", env.ledger.public_key.public_bytes_raw().hex(),
            "--approver-keys", str(tmp_path / "keys.json"), "--paypal-certs",
            str(out / "certs"), "--paypal-webhook-id", wid,
            "--paypal-roots", str(tmp_path / "roots.pem")]
    assert cli.main(["verify", *base, "--paypal-events", str(out / "paypal-events.jsonl")]) == 0
    assert cli.main(["verify", *base, "--paypal-events", str(out / "bad.jsonl")]) == 2
    assert "does not verify" in capsys.readouterr().out


# -- tampering with the offline evidence (security review, Day 2) ----------------------------


def exported(env, tmp_path):
    out = tmp_path / "export"
    out.mkdir(exist_ok=True)
    (out / "paypal-events.jsonl").write_text(webhooks.export_events(env.conn))
    webhooks.export_certs(env.conn, out / "certs")
    return out


def test_recheck_refuses_a_self_signed_leaf(env, tmp_path):
    """The server swaps PayPal's cert for its own self-signed 'paypal.com' cert and re-signs
    the events with that key: the chain no longer reaches a trusted root."""
    import base64
    import hashlib
    import zlib

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import Encoding

    out = exported(env, tmp_path)
    fake = WebhookSigner(env.funder.signer.webhook_id)  # its own CA, not the trusted one
    pem = fake.leaf.public_bytes(Encoding.PEM)
    digest = hashlib.sha256(fake.leaf.public_bytes(Encoding.DER)).hexdigest()
    (out / "certs" / f"{digest}.pem").write_bytes(pem)
    lines = []
    for line in (out / "paypal-events.jsonl").read_text().splitlines():
        ev = json.loads(line)
        msg = (f"{ev['transmission_id']}|{ev['transmission_time']}|{fake.webhook_id}|"
               f"{zlib.crc32(ev['body'].encode()) & 0xFFFFFFFF}").encode()
        ev["signature"] = base64.b64encode(
            fake.key.sign(msg, padding.PKCS1v15(), hashes.SHA256())).decode()
        ev["cert_sha256"] = digest
        lines.append(json.dumps(ev))
    (out / "forged.jsonl").write_text("\n".join(lines) + "\n")
    problems = webhooks.recheck(out / "forged.jsonl", out / "certs", env.ledger.receipts(),
                                webhook_id=fake.webhook_id, roots=env.funder.signer.roots)
    assert problems and all("not trusted" in p or "does not match" in p
                            or "no signed PayPal event" in p for p in problems), problems
    assert any("not trusted" in p for p in problems)


def test_recheck_refuses_a_cert_file_whose_hash_is_not_its_name(env, tmp_path):
    out = exported(env, tmp_path)
    (pem_file,) = (out / "certs").glob("*.pem")
    other = WebhookSigner("X").pem()
    pem_file.write_bytes(other)  # same file name (the pinned digest), another certificate
    problems = webhooks.recheck(out / "paypal-events.jsonl", out / "certs",
                                env.ledger.receipts(), webhook_id=env.funder.signer.webhook_id,
                                roots=env.funder.signer.roots)
    assert any("not the pinned certificate" in p for p in problems)


def test_recheck_refuses_a_malformed_cert_digest(env, tmp_path):
    out = exported(env, tmp_path)
    lines = (out / "paypal-events.jsonl").read_text().splitlines()
    ev = json.loads(lines[0])
    ev["cert_sha256"] = "../../etc/passwd"
    (out / "bad.jsonl").write_text(json.dumps(ev) + "\n")
    problems = webhooks.recheck(out / "bad.jsonl", out / "certs", env.ledger.receipts(),
                                webhook_id=env.funder.signer.webhook_id,
                                roots=env.funder.signer.roots)
    assert any("unreadable" in p for p in problems)


def test_recheck_uses_the_verifiers_webhook_id_not_the_exports(env, tmp_path):
    out = exported(env, tmp_path)
    problems = webhooks.recheck(out / "paypal-events.jsonl", out / "certs",
                                env.ledger.receipts(), webhook_id="ANOTHERWEBHOOK",
                                roots=env.funder.signer.roots)
    assert problems and all("does not verify" in p or "no signed PayPal event" in p
                            for p in problems)


def test_a_reused_transmission_id_with_another_body_is_judged_on_its_merits(env):
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    signer, tid = env.funder.signer, "11111111-2222-3333-4444-555555555555"
    h1, b1 = signer.sign({"id": "WH-1TRANSMIT00001-0001", "event_type": "PAYMENT.CAPTURE."
                          "COMPLETED", "resource": {"id": f.capture_id}}, transmission_id=tid)
    first = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h1, b1,
                           lookup=env.funder.lookup)
    # the capture's COMPLETED state was already recorded (mock delivery): a no-op
    assert first.status == "duplicate"
    h2, b2 = signer.sign({"id": "WH-1TRANSMIT00002-0002", "event_type": "PAYMENT.CAPTURE."
                          "COMPLETED", "resource": {"id": f.capture_id}}, transmission_id=tid)
    second = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h2, b2,
                            lookup=env.funder.lookup)
    assert second.status == "duplicate"  # judged on its own merits: nothing new attested


def refund_some(env, amount=1000):
    from studio import funding as fmod
    from tests.approve import sign_pending

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    pending, body = env.funder.request_refund(f.id, amount)
    env.funder.execute_refund(sign_pending(env.conn, env.ledger, pending, body), body)
    return fmod.get(env.conn, f.id)


def test_the_receipt_states_what_paypals_api_says_not_the_body(env):
    """A genuine signature over a body whose amount was changed (CRC32 binds the body
    weakly): the receipt records the API's facts for the capture's new state."""
    f = refund_some(env)
    event = {"id": "WH-1BODYLIE000001-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED",
             "resource": {"id": f.capture_id, "amount": {"currency_code": "PLN",
                                                         "value": "1.00"},
                          "custom_id": "f" * 64}}
    headers, body = env.funder.signer.sign(event)
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                         lookup=env.funder.lookup)
    assert out.status == "recorded"
    d = out.receipt.details
    assert d["event_class"] == "capture.partially_refunded"
    assert d["amount_minor"] == f.captured_minor and d["custom_id_sha256"] == f.content_sha256
    assert d["resource_source"] == "api"


def test_a_failed_lookup_stores_nothing_and_the_retry_records(env):
    """PayPal's API is down for the first delivery: refused (503 at the route), no row,
    so PayPal's retry of the same event and transmission is checked and recorded."""
    from studio.paypal.client import PayPalError

    f = refund_some(env)  # the capture now has a state not yet recorded
    event = {"id": "WH-1RETRY0000001-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED",
             "resource": {"id": f.capture_id}}
    headers, body = env.funder.signer.sign(event)

    def down(path):
        raise PayPalError(503, "SERVICE_UNAVAILABLE", transient=True)

    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body, lookup=down)
    assert e.value.code == "lookup_failed"
    assert not env.conn.execute("SELECT 1 FROM paypal_events WHERE event_id = ?",
                                (event["id"],)).fetchone()
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                         lookup=env.funder.lookup)
    assert out.status == "recorded" and out.receipt.details["event_class"] == \
        "capture.partially_refunded"


def test_the_body_cannot_name_the_event_class(env):
    """A genuine signature over a body that calls the capture something else: the
    receipt's class is what PayPal's API says the resource is."""
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    event = {"id": "WH-1LABEL0000001-0001", "event_type": "PAYMENT.CAPTURE.DENIED",
             "resource": {"id": f.capture_id}}
    headers, body = env.funder.signer.sign(event)
    before = len(env.ledger.receipts())
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                         lookup=env.funder.lookup)
    # PayPal's API says COMPLETED, which is already recorded: the label changes nothing
    assert out.status == "duplicate" and len(env.ledger.receipts()) == before


def test_verify_refuses_a_class_that_does_not_match_the_status(env):
    receipt = next(r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND
                   and r.details["event_class"] == "capture.completed")
    env.ledger.append(RUN, agent="paypal", kind=PAYPAL_WEBHOOK_KIND, provider="paypal",
                      details={**receipt.details, "event_id": "WH-1CLASS0000001-0001",
                               "status": "PENDING"})
    report = env.verify()
    assert any("class does not match" in e for e in report.errors), report.errors



# -- dedupe on what PayPal's API attests (CRC32 makes the body collidable) ------------------


def test_a_colliding_body_cannot_block_the_genuine_event(env):
    """An attacker who saw one transmission sends a body with the same CRC32 (here: any
    body, signed by the test key) under that transmission id first. It names something
    that is not ours, is refused and leaves no state; the genuine delivery is recorded."""
    f = refund_some(env)
    signer, tid = env.funder.signer, "bbbbbbbb-0000-0000-0000-000000000001"
    h, b = signer.sign({"id": "WH-1COLLIDE000001-0001", "event_type": "PAYMENT.CAPTURE."
                        "COMPLETED", "resource": {"id": "3C000000NOTOURS0X"}},
                       transmission_id=tid)
    with pytest.raises(webhooks.WebhookRefused):
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b,
                       lookup=env.funder.lookup)
    refund_id = next(iter(env.funder.fake.refunds))
    h2, b2 = signer.sign({"id": "WH-1GENUINE000001-0001", "event_type":
                          "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": refund_id}},
                         transmission_id=tid)
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h2, b2,
                         lookup=env.funder.lookup)
    # the refund's COMPLETED state was recorded by the mock delivery; the capture's new
    # state (partially refunded) is new and recorded
    assert out.status == "duplicate"
    h3, b3 = signer.sign({"id": "WH-1GENUINE000002-0002", "event_type":
                          "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": f.capture_id}},
                         transmission_id=tid)
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h3, b3,
                         lookup=env.funder.lookup)
    assert out.status == "recorded"


def test_a_refused_body_leaves_no_state(env):
    tables = {r[0] for r in env.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "paypal_transmissions" not in tables
    rows = env.conn.execute("SELECT COUNT(*) FROM paypal_events").fetchone()[0]
    for resource in ({"id": "3C000000NOTOURS0X"}, {}):
        h, b = env.funder.signer.sign({"id": "WH-1REFUSED000001-0001", "event_type":
                                       "PAYMENT.CAPTURE.COMPLETED", "resource": resource})
        with pytest.raises(webhooks.WebhookRefused):
            webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b,
                           lookup=env.funder.lookup)
    assert env.conn.execute("SELECT COUNT(*) FROM paypal_events").fetchone()[0] == rows


def test_the_same_resource_state_twice_is_recorded_once(env):
    f = refund_some(env)
    outs = []
    for n in (1, 2):
        h, b = env.funder.signer.sign({"id": f"WH-1TWICE00000{n}-000{n}", "event_type":
                                       "PAYMENT.CAPTURE.COMPLETED",
                                       "resource": {"id": f.capture_id}})
        outs.append(webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b,
                                   lookup=env.funder.lookup).status)
    assert outs == ["recorded", "duplicate"]


def test_a_new_genuine_state_of_the_same_resource_is_recorded(env):
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    classes = lambda: [r.details["event_class"] for r in env.ledger.receipts(RUN)  # noqa: E731
                       if r.kind == PAYPAL_WEBHOOK_KIND]
    assert "capture.completed" in classes()
    refund_some(env)  # the mock delivers the refund event: refund.completed
    h, b = env.funder.signer.sign({"id": "WH-1NEWSTATE00001-0001", "event_type":
                                   "PAYMENT.CAPTURE.COMPLETED",
                                   "resource": {"id": f.capture_id}})
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b,
                         lookup=env.funder.lookup)
    assert out.status == "recorded"
    assert {"capture.completed", "refund.completed", "capture.partially_refunded"} <= set(
        classes())
    assert env.verify().ok


# -- the offline re-check matches deliveries and receipts one to one -------------------------


def _recheck(env, out, events="paypal-events.jsonl"):
    return webhooks.recheck(out / events, out / "certs", env.ledger.receipts(),
                            webhook_id=env.funder.signer.webhook_id,
                            roots=env.funder.signer.roots)


def test_a_receipt_sharing_a_body_hash_cannot_hide_behind_the_valid_one(env, tmp_path):
    out = exported(env, tmp_path)
    assert _recheck(env, out) == []
    real = next(r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND)
    env.ledger.append(RUN, agent="paypal", kind=PAYPAL_WEBHOOK_KIND, provider="paypal",
                      details={**real.details, "amount_minor": 1})  # same body, altered
    problems = _recheck(env, out)
    assert any("no signed PayPal event" in p for p in problems), problems


def test_an_extra_receipt_without_an_export_line_fails(env, tmp_path):
    out = exported(env, tmp_path)
    real = next(r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND)
    env.ledger.append(RUN, agent="paypal", kind=PAYPAL_WEBHOOK_KIND, provider="paypal",
                      details={**real.details, "body_sha256": "a" * 64,
                               "transmission_id_sha256": "b" * 64})
    assert any("no signed PayPal event" in p for p in _recheck(env, out))


def test_an_extra_export_line_without_a_receipt_fails(env, tmp_path):
    out = exported(env, tmp_path)
    lines = (out / "paypal-events.jsonl").read_text().splitlines()
    (out / "dup.jsonl").write_text("\n".join([*lines, lines[0]]) + "\n")
    assert any("no paypal.webhook receipt for this delivery" in p
               for p in _recheck(env, out, "dup.jsonl"))


def test_real_paypal_leaf_needs_the_pinned_intermediate():
    """PayPal serves only the leaf (messageverificationcerts.sandbox.paypal.com, issued by
    DigiCert EV RSA CA G2). Without the pinned intermediate every real event was refused
    with cert_chain (found on pay.udun.eu, 2026-11-04)."""
    import datetime as _dt
    from pathlib import Path

    from cryptography import x509

    leaf_pem = (Path(__file__).resolve().parents[1]
                / "fixtures/paypal-certs/messageverificationcerts.sandbox.2026.pem").read_bytes()
    chain = x509.load_pem_x509_certificates(leaf_pem)
    roots = webhooks.load_roots()
    now = _dt.datetime(2026, 11, 4, 13, 28, tzinfo=_dt.UTC).timestamp()
    with pytest.raises(webhooks.WebhookRefused) as exc:
        webhooks.check_chain(chain, roots, now)
    assert exc.value.code == "cert_chain"
    leaf = webhooks.check_chain(chain, roots, now, intermediates=webhooks.load_intermediates())
    assert leaf.subject == chain[0].subject


def test_pinned_intermediate_is_not_a_trust_anchor():
    """The intermediate only completes a chain: it must still chain to a certifi root, and a
    leaf it did not sign is refused."""
    import datetime as _dt

    webhooks.load_roots()
    inter = webhooks.load_intermediates()
    now = _dt.datetime(2026, 11, 4, tzinfo=_dt.UTC).timestamp()
    with pytest.raises(webhooks.WebhookRefused):
        webhooks.check_chain([inter[0]], [], now, intermediates=inter)


def test_webhook_lookup_does_not_retry(env):
    """One read, then the handler's 503 path: PayPal redelivers instead of the studio
    spending the delivery window on its own retries."""
    import httpx

    from studio.paypal.client import PayPalClient

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        seen.append(request.url.path)
        return httpx.Response(503, json={})

    f = refund_some(env)
    env.funder.money.client = PayPalClient(
        "client-id-test", "client-secret-test-value",
        transport=httpx.MockTransport(handler), sleep=lambda s: None)
    event = {"id": "WH-1FAST00000001-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED",
             "resource": {"id": f.capture_id}}
    headers, body = env.funder.signer.sign(event)
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                       lookup=env.funder.lookup)
    assert e.value.code == "lookup_failed" and len(seen) == 1


def _down(path, **kw):
    raise TimeoutError()


def test_a_right_host_wrong_path_cert_url_is_refused_before_any_fetch(env):
    fetched = []
    v = webhooks.Verifier(env.conn, "W1", fetch=lambda u: fetched.append(u) or b"",
                          roots=env.funder.signer.roots)
    headers, body = env.funder.signer.sign({"id": "WH-1AAAAAAAA0002-0001"})
    headers["paypal-cert-url"] = "https://api.sandbox.paypal.com/v1/oauth2/token"
    with pytest.raises(webhooks.WebhookRefused) as e:
        v.verify(headers, body)
    assert e.value.code == "cert_url" and not fetched


def test_a_failed_cert_url_is_not_fetched_again_within_60_seconds(tmp_path):
    e = Env(tmp_path)
    now = [time.time()]
    fetched = []

    def broken(url):
        fetched.append(url)
        raise webhooks.WebhookRefused("could not fetch PayPal's certificate", "cert_fetch")

    v = webhooks.Verifier(e.conn, "W1", fetch=broken, roots=e.funder.signer.roots,
                          clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(webhooks.WebhookRefused) as exc:
            v.cert(MOCK_CERT_URL)
        assert exc.value.code == "cert_fetch"
    assert fetched == [MOCK_CERT_URL]
    now[0] += 61
    with pytest.raises(webhooks.WebhookRefused):
        v.cert(MOCK_CERT_URL)
    assert fetched == [MOCK_CERT_URL, MOCK_CERT_URL]


def test_the_negative_cache_is_capped_at_64_urls(tmp_path):
    e = Env(tmp_path)

    def broken(url):
        raise webhooks.WebhookRefused("could not fetch PayPal's certificate", "cert_fetch")

    v = webhooks.Verifier(e.conn, "W1", fetch=broken, roots=e.funder.signer.roots)
    for i in range(100):
        with pytest.raises(webhooks.WebhookRefused):
            v.cert(f"https://api.paypal.com/v1/notifications/certs/C{i}")
    assert len(v._failed) <= 64


def test_the_cert_fetch_has_a_five_second_timeout(monkeypatch):
    import httpx

    seen = {}

    class Fake:
        def __init__(self, **kw):
            seen.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return httpx.Response(200, content=b"x")

    monkeypatch.setattr(httpx, "Client", Fake)
    webhooks.http_fetch(MOCK_CERT_URL)
    assert seen["timeout"] == 5.0 and seen["follow_redirects"] is False


def _webhook_app(tmp_path):
    from studio.app import create_app
    from tests.test_ui_studio import settings

    return create_app(settings(tmp_path, PAYPAL_ADAPTER="mock", STUDIO_REQUIRE_LOGIN="1"))


def _signed(app, n):
    event = {"id": f"WH-1CONC{n:09d}-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED",
             "resource": {"id": "3C000000NOTOURS0X"}}
    return app.state.funder.signer.sign(event)


def test_the_fifth_concurrent_webhook_gets_429(tmp_path):
    import asyncio

    import httpx

    a = _webhook_app(tmp_path)
    v = a.state.funder.verifier
    real = v.fetch

    def slow(url):
        time.sleep(1.0)
        return real(url)

    v.fetch = slow

    async def go():
        transport = httpx.ASGITransport(app=a)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            reqs = []
            for i in range(5):
                headers, body = _signed(a, i)
                reqs.append(c.post("/webhooks/paypal", content=body, headers=headers))
            return [r.status_code for r in await asyncio.gather(*reqs)]

    codes = asyncio.run(go())
    assert codes.count(429) == 1 and codes.count(400) == 4, codes


def test_healthz_answers_while_a_webhooks_cert_fetch_sleeps(tmp_path):
    import asyncio

    import httpx

    a = _webhook_app(tmp_path)
    v = a.state.funder.verifier
    real = v.fetch

    def slow(url):
        time.sleep(3.0)
        return real(url)

    v.fetch = slow

    async def go():
        transport = httpx.ASGITransport(app=a)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            headers, body = _signed(a, 99)
            hook = asyncio.ensure_future(c.post("/webhooks/paypal", content=body,
                                                headers=headers))
            await asyncio.sleep(0.3)  # the fetch is now sleeping
            t0 = time.monotonic()
            h = await c.get("/healthz")
            took = time.monotonic() - t0
            still_sleeping = not hook.done()  # healthz was answered during the fetch
            await hook
            return h.status_code, took, still_sleeping

    status, took, still_sleeping = asyncio.run(go())
    assert status == 200 and took < 1.0 and still_sleeping, (took, still_sleeping)
