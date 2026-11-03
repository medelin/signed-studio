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
        a.state.funder.lookup = lambda path: (_ for _ in ()).throw(TimeoutError())
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


def test_a_reused_transmission_id_is_refused(env):
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    signer, tid = env.funder.signer, "11111111-2222-3333-4444-555555555555"
    h1, b1 = signer.sign({"id": "WH-1TRANSMIT00001-0001", "event_type": "PAYMENT.CAPTURE."
                          "COMPLETED", "resource": {"id": f.capture_id}}, transmission_id=tid)
    first = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h1, b1,
                           lookup=env.funder.lookup)
    assert first.status == "recorded"
    h2, b2 = signer.sign({"id": "WH-1TRANSMIT00002-0002", "event_type": "PAYMENT.CAPTURE."
                          "COMPLETED", "resource": {"id": f.capture_id}}, transmission_id=tid)
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h2, b2,
                       lookup=env.funder.lookup)
    assert e.value.code == "transmission_replayed"


def test_the_receipt_states_what_paypals_api_says_not_the_body(env):
    """A genuine signature over a body whose amount was changed before signing (CRC32
    binds the body weakly): the receipt records the API's amount."""
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    event = {"id": "WH-1BODYLIE000001-0001", "event_type": "PAYMENT.CAPTURE.COMPLETED",
             "resource": {"id": f.capture_id, "amount": {"currency_code": "PLN",
                                                         "value": "1.00"},
                          "custom_id": "f" * 64}}
    headers, body = env.funder.signer.sign(event)
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                         lookup=env.funder.lookup)
    assert out.status == "recorded"
    d = out.receipt.details
    assert d["amount_minor"] == f.captured_minor and d["custom_id_sha256"] == f.content_sha256
    assert d["resource_source"] == "api"


def test_a_failed_lookup_stores_nothing_and_the_retry_records(env):
    """PayPal's API is down for the first delivery: refused (503 at the route), no row,
    so PayPal's retry of the same event and transmission is checked and recorded."""
    from studio import funding as fmod
    from studio.paypal.client import PayPalError

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
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
        "capture.completed"


def test_the_body_cannot_name_the_event_class(env):
    """A genuine signature over a body that calls the capture something else: the
    receipt's class is what PayPal's API says the resource is."""
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    event = {"id": "WH-1LABEL0000001-0001", "event_type": "PAYMENT.CAPTURE.DENIED",
             "resource": {"id": f.capture_id}}
    headers, body = env.funder.signer.sign(event)
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, headers, body,
                         lookup=env.funder.lookup)
    d = out.receipt.details
    assert d["event_class"] == "capture.completed" and d["status"] == "COMPLETED"
    assert d["body_event_type"] == "PAYMENT.CAPTURE.DENIED"


def test_verify_refuses_a_class_that_does_not_match_the_status(env):
    receipt = next(r for r in env.ledger.receipts(RUN) if r.kind == PAYPAL_WEBHOOK_KIND
                   and r.details["event_class"] == "capture.completed")
    env.ledger.append(RUN, agent="paypal", kind=PAYPAL_WEBHOOK_KIND, provider="paypal",
                      details={**receipt.details, "event_id": "WH-1CLASS0000001-0001",
                               "status": "PENDING"})
    report = env.verify()
    assert any("class does not match" in e for e in report.errors), report.errors


# -- transmission ids cannot be reused with another body (push review) ---------------------


def _capture_event(env, eid, capture_id=None):
    from studio import funding as fmod

    f = fmod.of_item(env.conn, "cmp_paid1")[0]
    return {"id": eid, "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": capture_id or f.capture_id}}


def test_a_refused_transmission_cannot_come_back_with_another_body(env):
    tid = "aaaaaaaa-0000-0000-0000-000000000001"
    h, b = env.funder.signer.sign(_capture_event(env, "WH-1TOMB00000001-0001",
                                                 "3C000000NOTOURS0X"), transmission_id=tid)
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b,
                       lookup=env.funder.lookup)
    assert e.value.code == "unknown_resource"
    h2, b2 = env.funder.signer.sign(_capture_event(env, "WH-1TOMB00000002-0002"),
                                    transmission_id=tid)  # a real capture this time
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h2, b2,
                       lookup=env.funder.lookup)
    assert e.value.code == "transmission_refused"


def _down(path):
    from studio.paypal.client import PayPalError

    raise PayPalError(503, "SERVICE_UNAVAILABLE", transient=True)


def test_a_503_retry_with_the_same_body_is_recorded(env):
    tid = "aaaaaaaa-0000-0000-0000-000000000002"
    h, b = env.funder.signer.sign(_capture_event(env, "WH-1PEND00000001-0001"),
                                  transmission_id=tid)
    with pytest.raises(webhooks.WebhookRefused):
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b, lookup=_down)
    out = webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b,
                         lookup=env.funder.lookup)
    assert out.status == "recorded"
    assert not env.conn.execute("SELECT 1 FROM paypal_transmissions WHERE transmission_id "
                                "= ?", (tid,)).fetchone()


def test_a_503_retry_with_a_changed_body_is_refused(env):
    tid = "aaaaaaaa-0000-0000-0000-000000000003"
    h, b = env.funder.signer.sign(_capture_event(env, "WH-1PEND00000002-0002"),
                                  transmission_id=tid)
    with pytest.raises(webhooks.WebhookRefused):
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h, b, lookup=_down)
    h2, b2 = env.funder.signer.sign(_capture_event(env, "WH-1PEND00000003-0003"),
                                    transmission_id=tid)
    with pytest.raises(webhooks.WebhookRefused) as e:
        webhooks.apply(env.conn, env.ledger, env.funder.verifier, h2, b2,
                       lookup=env.funder.lookup)
    assert e.value.code == "transmission_body_changed"
