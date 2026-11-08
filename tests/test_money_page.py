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
    assert '<script src="/static/vendor/ag-grid-community.min.noStyle.js"' in page.text
    assert '<script src="/static/money.js"' in page.text
    assert "<script>" not in page.text.replace('<script type="application/json"', "")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "unsafe-inline'; img" not in csp
    assert c.get("/static/vendor/ag-grid-community.min.noStyle.js").status_code == 200
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


def test_money_page_loads_the_nostyle_build(world):
    from pathlib import Path

    app, *_ = world
    page = client(app).get("/money").text
    assert "/static/vendor/ag-grid-community.min.noStyle.js" in page
    assert "ag-grid-community.min.js" not in page
    vendor = Path(__file__).resolve().parents[1] / "studio/ui/static/vendor"
    assert not (vendor / "ag-grid-community.min.js").exists()
    bundle = (vendor / "ag-grid-community.min.noStyle.js").read_text()
    # the one remaining createElement("style") is the global-CSS injector, which the
    # noStyle build never feeds (no data-ag-scope theme styles)
    assert bundle.count('createElement("style")') <= 1
    assert "legacy" in bundle  # the legacy theme path exists; its CSS is the vendored file


def test_explain_reads_the_treasurers_real_key():
    from pathlib import Path

    from studio.agents.treasurer import Advice

    ui = Path(__file__).resolve().parents[1] / "studio/ui/static/money.js"
    key = "explanation"
    assert key in Advice(currency="EUR", split=[], explanation="x", source="code").as_dict()
    assert f"r.data.{key}" in ui.read_text()


def test_a_failed_check_is_not_retried_within_the_cooldown(world, monkeypatch):
    app, *_ = world
    calls = []

    def boom(**kw):
        calls.append(1)
        raise RuntimeError("PayPal down")

    monkeypatch.setattr(recon, "reconcile_report", boom)
    c = client(app)
    assert c.get("/api/money?reconcile=1").status_code == 502
    assert c.get("/api/money?reconcile=1").status_code == 502
    assert len(calls) == 1


def test_check_with_paypal_is_refused_in_read_only_mode(tmp_path, monkeypatch):
    app = make_app(tmp_path, PAYPAL_ADAPTER="mock", STUDIO_READ_ONLY="1")
    owner = account(app, "owner")
    calls = []
    monkeypatch.setattr(recon, "reconcile_report", lambda **kw: calls.append(1))
    c = client(app)
    r = c.get("/api/money?reconcile=1")
    assert r.status_code == 503 and r.json() == {"error": "read_only"} and not calls
    assert c.get("/api/money").status_code == 200  # reading the grid stays possible
    assert owner


def test_totals_per_currency_and_daily_chart_data(tmp_path):
    from studio.routes_money import daily_of, totals_of

    app = make_app(tmp_path, PAYPAL_ADAPTER="mock")
    owner = account(app, "owner")
    _brief(app.state.conn, "brf_t1", "run_t1", owner.id)
    _brief(app.state.conn, "brf_t2", "run_t2", owner.id)
    day1, day2 = 1_700_000_000.0, 1_700_000_000.0 + 86400 * 2
    _fund(app.state.conn, "run_t1", "cre_c", order="1O1", auth="1AU1", capture="1CP1", ts=day1)
    _fund(app.state.conn, "run_t1", "cre_d", order="2O2", auth="2AU2", ts=day1 + 60)
    _fund(app.state.conn, "run_t2", "cre_e", order="3O3", auth="3AU3", capture="3CP3", ts=day2)
    data = client(app).get("/api/money").json()
    assert data["account"] and data["currency"] == "EUR"
    (tot,) = data["totals"]  # one currency: one row
    assert tot["currency"] == "EUR" and tot["count"] == 3
    assert tot["amount"] == "661.50" and tot["authorized"] == "661.50"
    assert tot["captured"] == "441.00" and tot["refunded"] == "0.00"
    assert tot["amount_n"] == 661.5 and tot["captured_n"] == 441.0
    row = data["rows"][0]
    assert row["amount_n"] == 220.5 and isinstance(row["created_ts"], int)
    # chart data: one point per UTC day, numbers in major units, oldest first
    assert data["daily"] == [
        {"day": "2023-11-14", "currency": "EUR", "held": 441.0, "taken": 220.5, "refunded": 0.0},
        {"day": "2023-11-16", "currency": "EUR", "held": 220.5, "taken": 220.5, "refunded": 0.0}]
    # several currencies: one totals row each, never mixed
    mixed = [
        {"currency": "EUR", "created": "2026-01-01 10:00", "amount": "10.00",
         "authorized": "10.00", "captured": "5.00", "refunded": "1.00"},
        {"currency": "JPY", "created": "2026-01-01 11:00", "amount": "1000",
         "authorized": "1000", "captured": "0", "refunded": "0"},
        {"currency": "EUR", "created": "2026-01-02 10:00", "amount": "0.10",
         "authorized": "0.10", "captured": "0.20", "refunded": "0.00"}]
    totals = {t["currency"]: t for t in totals_of(mixed)}
    assert set(totals) == {"EUR", "JPY"} and totals["EUR"]["amount"] == "10.10"
    assert totals["EUR"]["captured"] == "5.20" and totals["JPY"]["amount"] == "1000"
    assert [(d["day"], d["currency"]) for d in daily_of(mixed)] == [
        ("2026-01-01", "EUR"), ("2026-01-01", "JPY"), ("2026-01-02", "EUR")]
    empty_app = make_app(tmp_path / "e")
    empty = client(empty_app).get("/api/money").json()
    assert empty["totals"] == [] and empty["daily"] == []


def test_money_page_has_the_grid_features_and_csp_is_unchanged(world):
    from pathlib import Path

    app, *_ = world
    c = client(app)
    page = c.get("/money")
    html = page.text
    assert "data-quick-filter" in html and "data-export-csv" in html
    assert 'data-floating-filters="1"' in html and 'aria-describedby="money-keys"' in html
    assert 'id="money-chart"' in html and 'role="img"' in html
    assert '<script src="/static/vendor/ag-charts-community.min.js"' in html
    assert i18n.text("en", "money.csv") in html and i18n.text("en", "money.search") in html
    csp = page.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "style-src 'self';" in csp
    assert "unsafe-eval" not in csp
    js = (Path(__file__).resolve().parents[1] / "studio/ui/static/money.js").read_text()
    for needle in ("agTextColumnFilter", "agNumberColumnFilter", "agDateColumnFilter",
                   "floatingFilter: true", "exportDataAsCsv", "quickFilterText",
                   "pinnedBottomRowData", "localStorage", "AgCharts.create"):
        assert needle in js
    for enterprise in ("rowGroup", "enableRowGroup", "pivot", "agSetColumnFilter",
                       "exportDataAsExcel"):
        assert enterprise not in js
    # the page-load JSON carries what the pinned row and the chart need
    assert {"totals", "daily", "account"} <= set(c.get("/api/money").json())


def test_charts_vendor_file_is_served_with_a_script_type(world):
    app, *_ = world
    c = client(app)
    r = c.get("/static/vendor/ag-charts-community.min.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert b"agCharts" in r.content[:600]
    lic = c.get("/static/vendor/AG-CHARTS-LICENSE.txt")
    assert lic.status_code == 200 and "MIT" in lic.text


def test_money_new_strings_exist_in_en_and_pl():
    for k in ("money.search", "money.csv", "money.reset", "money.chart", "money.keys",
              "js.money_col_campaign", "js.money_total", "js.money_chart_summary",
              "js.money_rows"):
        assert i18n.EN[k].strip() and i18n.PL[k].strip() and i18n.EN[k] != i18n.PL[k]
