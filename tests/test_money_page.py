"""``/money``: the account's own fundings in an AG Grid (vendored, CSP unchanged); the
reconciliation column fills only on a click (``?reconcile=1``) and is rate-limited."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from studio import funding
from studio.paypal import reconcile as recon
from studio.paypal.reconcile import ReconLine, ReconReport
from studio.ui import i18n
from tests.test_assistants import BASE, account, login, make_app

RUN_OWN, RUN_OTHER = "run_money_own", "run_money_other"
CAP = "3C679366HH908993F"
AUTH = "7AU11111AB222222C"


def _fund(conn, run, item, *, order, capture=None, auth=None, ts=100.0):
    q = funding.quote("EUR", 21000, 1, Decimal(5))
    f = funding.create(conn, run_id=run, item_id=item, content_sha256=item[-1] * 64, q=q,
                       now=ts)
    f = funding.advance(conn, f.id, "created", expect="created", now=ts, order_id=order)
    if auth:
        funding.advance(conn, f.id, "payer_approved", expect="created", now=ts)
        f = funding.advance(conn, f.id, "authorized", expect="payer_approved", now=ts,
                            authorization_id=auth, authorized_minor=q.total_minor)
        funding.record_move(conn, f, "authorization", paypal_id=auth,
                            amount_minor=q.total_minor, receipt_id="rcp_a", now=ts)
    if capture:
        f = funding.advance(conn, f.id, "captured", expect="authorized", now=ts,
                            capture_id=capture, captured_minor=q.total_minor)
        funding.record_move(conn, f, "capture", paypal_id=capture,
                            amount_minor=q.total_minor, receipt_id="rcp_c", now=ts)
    return f


def _brief(conn, bid, run, owner):
    conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts, channel, "
                 "account_id) VALUES (?, ?, 't', 'text', 1, 'whatsapp', ?)",
                 (bid, run, owner))


@pytest.fixture()
def world(tmp_path):
    app = make_app(tmp_path, PAYPAL_ADAPTER="mock")
    owner, other = account(app, "owner"), account(app, "stranger")
    conn = app.state.conn
    _brief(conn, "brf_mo1", RUN_OWN, owner.id)
    _brief(conn, "brf_mo2", RUN_OTHER, other.id)
    own = _fund(conn, RUN_OWN, "cre_a", order="5O190127TN364715T", auth=AUTH, capture=CAP)
    foreign = _fund(conn, RUN_OTHER, "cre_b", order="6O190127TN364715T", auth="8AU2",
                    capture="9CP2")
    return app, own, foreign


def client(app, user="owner"):
    c = TestClient(app, base_url=BASE)
    login(c, app, user)
    return c


def test_money_requires_a_session(world):
    app, *_ = world
    c = TestClient(app, base_url=BASE)
    page = c.get("/money", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login")
    api = c.get("/api/money")
    assert api.status_code == 401 and api.json()["error"] == "login_required"


def test_money_lists_only_own_fundings(world):
    app, own, foreign = world
    c = client(app)
    data = c.get("/api/money").json()
    assert data["currency"] == "EUR"
    assert [r["funding_id"] for r in data["rows"]] == [own.id]
    row = data["rows"][0]
    assert row["via"] == "checkout" and row["state"] == "captured"
    assert row["amount"] == "220.50" and row["captured"] == "220.50"
    assert row["authorized"] == "220.50" and row["refunded"] == "0.00"
    assert row["receipt_ids"] == ["rcp_a", "rcp_c"] and row["recon"] is None
    assert foreign.id not in c.get("/api/money").text
    # the stranger sees theirs and not ours
    other = client(app, "stranger").get("/api/money").json()
    assert [r["funding_id"] for r in other["rows"]] == [foreign.id]


def test_a_mandate_charge_is_marked_via_mandate(world):
    app, own, _ = world
    app.state.conn.execute(
        "INSERT INTO mandate_charges (approval_id, mandate_id, period, amount_minor, "
        "currency, vault_sha256, status, funding_id, created_ts, updated_ts) "
        "VALUES ('ap1', 'man_1', 1, 100, 'EUR', 'v', 'done', ?, 1, 1)", (own.id,))
    row = client(app).get("/api/money").json()["rows"][0]
    assert row["via"] == "mandate"


def test_money_page_empty_and_paypal_off(tmp_path):
    for env in ({}, {"PAYPAL_ADAPTER": "mock"}):
        app = make_app(tmp_path / (env.get("PAYPAL_ADAPTER") or "off"), **env)
        c = client(app)
        page = c.get("/money")
        assert page.status_code == 200 and 'id="money-grid"' in page.text
        assert i18n.text("en", "money.empty") in page.text
        api = c.get("/api/money")
        assert api.status_code == 200 and api.json()["rows"] == []
    off = c.get("/api/money?reconcile=1")  # PayPal on, nothing to check: no 500
    assert off.status_code == 200
    app = make_app(tmp_path / "plain")
    c = client(app)
    assert c.get("/api/money?reconcile=1").status_code == 503


def test_money_page_loads_vendored_grid_under_csp(world):
    app, *_ = world
    c = client(app)
    page = c.get("/money")
    assert '<script src="/static/vendor/ag-grid-community.min.js"' in page.text
    assert '<script src="/static/money.js"' in page.text
    assert "<script>" not in page.text.replace('<script type="application/json"', "")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "unsafe-inline'; img" not in csp
    assert c.get("/static/vendor/ag-grid-community.min.js").status_code == 200
    assert c.get("/static/vendor/AG-GRID-LICENSE.txt").status_code == 200
    nav = page.text.split("</nav>")[0]
    assert 'href="/money"' in nav  # PayPal is on


def test_nav_has_no_money_link_with_paypal_off(tmp_path):
    c = client(make_app(tmp_path))
    assert 'href="/money"' not in c.get("/studio").text


def test_reconcile_runs_once_per_cooldown_and_fills_recon(world, monkeypatch):
    app, own, _ = world
    calls = []

    def fake(**kw):
        calls.append(kw["account_id"])
        return ReconReport(lines=[
            ReconLine(own.id, "authorization", AUTH, 22050, "EUR", "matched"),
            ReconLine(own.id, "capture", CAP, 22050, "EUR", "missing")],
            pages_read=3, truncated=True)

    monkeypatch.setattr(recon, "reconcile_report", fake)
    c = client(app)
    first = c.get("/api/money?reconcile=1").json()
    assert first["rows"][0]["recon"] == "missing"  # the worst line wins
    assert "pages_read" not in str(first) and "truncated" not in str(first)
    second = c.get("/api/money?reconcile=1").json()
    assert second["rows"][0]["recon"] == "missing" and len(calls) == 1  # reused
    assert c.get("/api/money").json()["rows"][0]["recon"] is None  # default: no PayPal


def test_money_strings_exist_in_en_and_pl():
    keys = [k for k in i18n.EN if k.startswith(("money.", "js.money_")) or k == "nav.money"]
    assert len(keys) >= 10
    for k in keys:
        assert i18n.PL[k].strip() and i18n.placeholders(i18n.EN[k]) == i18n.placeholders(i18n.PL[k])
