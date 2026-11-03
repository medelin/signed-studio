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
        ok = c.post("/webhooks/paypal", content=body, headers=headers)
        assert ok.status_code == 200 and ok.json()["status"] == "unmatched"
        again = c.post("/webhooks/paypal", content=body, headers=headers)
        assert again.json()["status"] == "duplicate"
        forged = c.post("/webhooks/paypal", content=body.replace(b"0X", b"0Y"),
                        headers=headers)
        assert forged.status_code == 400 and forged.json()["error"] == "signature"


def test_export_and_offline_recheck(env, tmp_path, capsys):
    out = tmp_path / "export"
    out.mkdir()
    (out / "paypal-events.jsonl").write_text(webhooks.export_events(env.conn))
    assert webhooks.export_certs(env.conn, out / "certs") == 1
    receipts = env.ledger.receipts()
    assert webhooks.recheck(out / "paypal-events.jsonl", out / "certs", receipts) == []
    # one byte of a recorded event changed: PayPal's signature no longer verifies
    lines = (out / "paypal-events.jsonl").read_text().splitlines()
    ev = json.loads(lines[0])
    ev["body"] = ev["body"].replace('"COMPLETED"', '"COMPLETEX"').replace(
        '"CREATED"', '"CREATEX"')
    (out / "bad.jsonl").write_text("\n".join([json.dumps(ev), *lines[1:]]) + "\n")
    problems = webhooks.recheck(out / "bad.jsonl", out / "certs", receipts)
    assert any("does not verify" in p for p in problems)
    # the CLI: a valid log and events verify; tampered events fail with exit 2
    from studio.approvals import Keys

    (tmp_path / "r.jsonl").write_text(env.ledger.export_jsonl())
    keys = {a: k.public_bytes_raw().hex() for a, k in Keys.registry(env.conn).items()}
    (tmp_path / "keys.json").write_text(json.dumps(keys))
    base = [str(tmp_path / "r.jsonl"), "--key", env.ledger.public_key.public_bytes_raw().hex(),
            "--approver-keys", str(tmp_path / "keys.json"), "--paypal-certs",
            str(out / "certs")]
    assert cli.main(["verify", *base, "--paypal-events", str(out / "paypal-events.jsonl")]) == 0
    assert cli.main(["verify", *base, "--paypal-events", str(out / "bad.jsonl")]) == 2
    assert "does not verify" in capsys.readouterr().out
