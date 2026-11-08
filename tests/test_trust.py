"""The public proof: /trust, /verify/<hash>, the printable receipt and the eval summary."""

import json
import re
import secrets
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import trust as trust_module
from studio.app import CSP, create_app
from studio.receipts import public_key_raw
from studio.settings import Settings
from tests.test_login_gate import HTML, PASSWORD, form_login

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    return create_app(Settings.load({
        "STUDIO_DATA_DIR": str(tmp_path / "v"), "JUDGE_PASSWORD": PASSWORD,
        "SESSION_SECRET": secrets.token_urlsafe(32), "STUDIO_REQUIRE_LOGIN": "1"}))


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


def make_receipt(app):
    return app.state.ledger.append("run-t", agent="producer", kind="model_call")


def test_trust_page_is_public_with_key_and_counts(app, client):
    make_receipt(app)
    r = client.get("/trust", headers=HTML)
    assert r.status_code == 200
    key = public_key_raw(app.state.ledger.public_key).hex()
    assert key in r.text
    head = app.state.ledger.receipts()[-1].hash
    assert head in r.text
    assert re.search(r"chain OK, 1 receipts, \d+ signatures, P-V1…P-V6 OK", r.text)
    summary = json.loads((ROOT / "docs" / "eval-summary.json").read_text())
    assert f"{summary['total_tests']} tests" in r.text
    assert f"{summary['attack_tests']} of them attack tests" in r.text
    assert 'data-copy="ledger-key"' in r.text
    assert "https://github.com/medelin/signed-studio" in r.text


def test_trust_page_in_polish(client):
    r = client.get("/trust?lang=pl")
    assert "Dowód, bez konta" in r.text
    assert "łańcuch OK" in r.text


def test_self_check_is_cached_and_stats_expire(app, monkeypatch):
    calls = []
    real = trust_module.verify
    monkeypatch.setattr(trust_module, "verify",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    clock = [1000.0]
    t = trust_module.Trust(app.state.ledger, lambda: {}, clock=lambda: clock[0])
    first = t.self_check()
    assert t.self_check() is first and len(calls) == 1
    clock[0] += trust_module.VERIFY_TTL_S - 1
    t.self_check()
    assert len(calls) == 1
    clock[0] += 2
    assert t.self_check() is not first and len(calls) == 2
    assert t.stats()["count"] == 0
    make_receipt(app)
    assert t.stats()["count"] == 0  # cached for 60 s
    clock[0] += trust_module.STATS_TTL_S + 1
    assert t.stats()["count"] == 1


def test_two_requests_verify_once(app, client, monkeypatch):
    calls = []
    real = trust_module.verify
    monkeypatch.setattr(trust_module, "verify",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    client.get("/trust")
    client.get("/trust")
    assert len(calls) == 1


def test_a_broken_ledger_is_reported(app, client, monkeypatch):
    make_receipt(app)
    real = trust_module.verify

    def broken(*a, **k):
        report = real(*a, **k)
        report.ok = False
        report.errors.append("abc: captured more than authorized (P-V2)")
        return report

    monkeypatch.setattr(trust_module, "verify", broken)
    r = client.get("/trust")
    assert "chain INVALID" in r.text and "P-V1…P-V6 FAIL" in r.text


def test_verify_known_and_unknown_hash(app, client):
    receipt = make_receipt(app)
    r = client.get(f"/verify/{receipt.hash}")  # no session, JSON
    assert r.status_code == 200
    body = r.json()
    assert body["found"] and body["signed"] and body["step"] == "producer/model_call"
    assert set(body) == {"hash", "found", "signed", "step", "time"}  # no contents
    page = client.get(f"/verify/{receipt.hash.upper()}", headers=HTML)
    assert page.status_code == 200 and "signed by the ledger key" in page.text
    assert client.get("/verify/" + "0" * 64).status_code == 404
    unknown = client.get("/verify/" + "0" * 64, headers=HTML)
    assert unknown.status_code == 404 and "No receipt with this hash" in unknown.text
    assert client.get("/verify/not-a-hash").status_code == 400


def test_verify_is_rate_limited_but_not_the_login(app, client):
    codes = [client.get("/verify/" + "1" * 64).status_code for _ in range(70)]
    assert codes[0] == 404 and codes[-1] == 429
    assert client.get("/login", headers=HTML).status_code == 200
    assert app.state.limiter.reserve("203.0.113.9")


def test_print_view_needs_a_session(app, client):
    receipt = make_receipt(app)
    url = f"/receipts/{receipt.id}/print"
    r = client.get(url, headers=HTML)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert form_login(client).status_code == 303
    r = client.get(url, headers=HTML)
    assert r.status_code == 200
    for part in (receipt.hash, receipt.sig, receipt.prev, receipt.id, "<svg"):
        assert part in r.text
    assert f"https://testserver/verify/{receipt.hash}" in r.text
    assert client.get("/receipts/nope/print", headers=HTML).status_code == 404


def test_csp_is_unchanged_and_pages_are_clean(app, client):
    receipt = make_receipt(app)
    form_login(client)
    for path in ("/trust", f"/receipts/{receipt.id}/print", f"/verify/{receipt.hash}"):
        r = client.get(path, headers=HTML)
        assert r.headers["Content-Security-Policy"] == CSP
        assert ' style="' not in r.text and "<style" not in r.text
        assert not re.search(r"\son[a-z]+=", r.text)


def test_eval_summary_shape_and_counts():
    path = ROOT / "docs" / "eval-summary.json"
    data = json.loads(path.read_text())
    assert set(data) == {"total_tests", "attack_tests", "attack_files", "generated"}
    assert data["total_tests"] > data["attack_tests"] > 0
    attack_files = len(list((ROOT / "tests" / "attack").glob("test_*.py")))
    assert data["attack_files"] == attack_files
    assert trust_module.eval_summary(path) == data
    assert trust_module.eval_summary(ROOT / "nope.json") is None


def test_eval_summary_script_counts_node_ids():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import eval_summary
    finally:
        sys.path.pop(0)
    ids = ["tests/attack/test_a.py::t1", "tests/attack/test_a.py::t2[x]", "tests/test_b.py::t",
           "", "3 tests collected in 0.1s"]
    got = eval_summary.count(ids)
    assert (got["total_tests"], got["attack_tests"], got["attack_files"]) == (3, 2, 1)
