"""Export and import (``studio.exports``): leads as CSV / JSON / vCard and back in, the
monthly statement (CSV profiles, hledger, JSON) tied to signed approvals, masking for
trial and demo accounts in every format and endpoint, the API downloads and CSRF on the
import form."""

import json
import re
import time
from collections import defaultdict
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from studio import db, exports
from studio.receipts import canonical
from tests.forms import csrf
from tests.test_assistants import BASE, account, bearer, login, make_app, token
from tests.test_judge_live import LIVE

SEEDED_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@example\.com")
SEEDED_PHONE = re.compile(r"\+48 ?000 ?000 ?\d{3}")


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    return c


def add_lead(conn, lid, name, email="", phone="", source="lead_form", message="", ts=1.0):
    with db.transaction(conn):
        conn.execute("INSERT INTO leads (id, name, email, phone, source, message, created_ts) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (lid, name, email, phone, source, message, ts))


def people(conn):
    return sorted(conn.execute("SELECT name, email, phone, source, message FROM leads")
                  .fetchall())


def three(conn):
    add_lead(conn, "l1", "Joanna Kowalska", "joanna@example.com", "+48000000101",
             message='Chcę "duży" kubek; dwa, proszę', ts=3.0)
    add_lead(conn, "l2", "Ola Nowak", "", "+48000000102", source="comment",
             message="=HYPERLINK(\"http://x\")", ts=2.0)
    add_lead(conn, "l3", "Piotr Zając", "piotr@example.com", "", message="line one\nline two",
             ts=1.0)


# -- leads: writers ---------------------------------------------------------------------

def test_csv_has_a_bom_crlf_rfc4180_quoting_and_a_formula_guard(conn):
    three(conn)
    text = exports.to_csv(exports.leads_rows(conn, masked=False), exports.LEAD_COLUMNS)
    assert text.startswith("\ufeffid,name,email,phone,source,message,")
    assert text.endswith("\r\n") and "\r\n" in text
    assert '"Chcę ""duży"" kubek; dwa, proszę"' in text  # quote, comma and ; are quoted
    assert "\"'=HYPERLINK(\"\"http://x\"\")\"" in text  # never a live formula
    assert '"line one\nline two"' in text
    assert exports._cell("a;b") == '"a;b"' and exports._cell("plain") == "plain"
    assert exports._cell("-12.5") == "-12.5" and exports._cell("+48 000 000 101") == \
        "+48 000 000 101"
    assert exports._cell("-cmd|' /C calc'!A0") == "'-cmd|' /C calc'!A0"


def test_csv_round_trip_gives_the_same_leads_and_skips_duplicates(conn, tmp_path):
    three(conn)
    data = exports.to_csv(exports.leads_rows(conn, masked=False),
                          exports.LEAD_COLUMNS).encode()
    other = db.connect(tmp_path / "other.db")
    db.init(other)
    r = exports.import_leads(other, data, "csv")
    assert (r.rows, r.added, r.skipped, r.rejected) == (3, 3, 0, [])
    assert people(other) == people(conn)
    again = exports.import_leads(other, data, "csv")
    assert (again.added, again.skipped) == (0, 3)
    assert people(other) == people(conn)


@pytest.mark.parametrize("kind", ["json", "vcf"])
def test_json_and_vcard_round_trip(conn, tmp_path, kind):
    three(conn)
    rows = exports.leads_rows(conn, masked=False)
    body, _, _ = exports.lead_file(rows, kind)
    other = db.connect(tmp_path / "other.db")
    db.init(other)
    r = exports.import_leads(other, body.encode(), kind)
    assert r.added == 3 and not r.rejected
    got = {row[0]: row[1:3] for row in people(other)}
    assert got == {row[0]: row[1:3] for row in people(conn)}


def test_vcard_shape(conn):
    add_lead(conn, "l1", "Anna Wiśniewska-Długonazwiskowa " + "x" * 80, "anna@example.com",
             "+48 000 000 103", message="Hi, there; ok")
    with db.transaction(conn):
        conn.execute("UPDATE leads SET score = 82, temp = 'HOT'")
    vcf = exports.leads_to_vcf(exports.leads_rows(conn, masked=False))
    lines = vcf.split("\r\n")
    assert lines[:2] == ["BEGIN:VCARD", "VERSION:4.0"] and lines[-2:] == ["END:VCARD", ""]
    assert "EMAIL:anna@example.com" in lines
    assert "TEL;VALUE=uri:tel:+48000000103" in lines
    assert all(len(line.encode()) <= 75 for line in lines)
    assert lines[3].startswith(" ")  # the long FN is folded
    note = next(line for line in lines if line.startswith("NOTE:"))
    assert note == r"NOTE:source: lead_form\; score: 82 (HOT)\nHi\, there\; ok"


# -- leads: masking ----------------------------------------------------------------------

def test_masked_rows_carry_no_name_email_or_phone_in_any_format(conn):
    three(conn)
    add_lead(conn, "l4", "Ewa Lis", "ewa@example.com", "+48000000104",
             message="call me at +48 000 000 104 or ewa@example.com")
    rows = exports.leads_rows(conn, masked=True)
    for kind in exports.LEAD_FORMATS:
        body, _, _ = exports.lead_file(rows, kind)
        assert not SEEDED_EMAIL.search(body), kind
        assert not SEEDED_PHONE.search(body), kind
        for name in ("Joanna", "Kowalska", "Nowak", "Zając", "Ewa Lis"):
            assert name not in body, (kind, name)
    assert {r["name"] for r in rows} == {"J. K.", "O. N.", "P. Z.", "E. L."}
    assert {r["email"] for r in rows} == {"[email]", ""}
    assert "EMAIL" not in exports.leads_to_vcf(rows) and "TEL" not in exports.leads_to_vcf(rows)


# -- leads: import rules -------------------------------------------------------------------

def test_import_matches_headers_normalises_and_reports_line_numbers(conn):
    add_lead(conn, "l0", "Old", "dup@example.com", "+48 000 000 199")
    data = ("Imię i nazwisko;E-mail;Telefon;Notatka\r\n"
            "Ala Kot; ALA@Example.com ;+48 (000) 000-111;hej\r\n"
            "Bez Kontaktu;;;nic\r\n"
            "Zły Mail;nie-mail;;x\r\n"
            "Duplikat;Dup@example.com;;x\r\n"
            "Też duplikat;;0048000000199;x\r\n"
            "Dwa w pliku;;+48000000111;x\r\n"
            "Krótki;;12;x\r\n").encode("utf-8-sig")
    r = exports.import_leads(conn, data, "csv")
    assert (r.rows, r.added, r.skipped) == (7, 1, 3)
    assert r.rejected == [(3, "no_contact"), (4, "invalid_email"), (8, "invalid_phone")]
    row = conn.execute("SELECT name, email, phone, source, message FROM leads WHERE name = "
                       "'Ala Kot'").fetchone()
    assert row == ("Ala Kot", "ala@example.com", "+48000000111", "import", "hej")


def test_import_limits(conn, monkeypatch):
    with pytest.raises(exports.ImportRefused) as e:
        exports.import_leads(conn, b"x" * (exports.MAX_IMPORT_BYTES + 1), "csv")
    assert e.value.code == "too_large"
    many = "email\n" + "".join(f"p{i}@example.com\n" for i in range(exports.MAX_IMPORT_ROWS + 1))
    with pytest.raises(exports.ImportRefused) as e:
        exports.import_leads(conn, many.encode(), "csv")
    assert e.value.code == "too_many_rows"
    assert conn.execute("SELECT count(*) FROM leads").fetchone()[0] == 0
    exactly = "email\n" + "".join(f"p{i}@example.com\n" for i in range(exports.MAX_IMPORT_ROWS))
    assert exports.import_leads(conn, exactly.encode(), "csv").added == exports.MAX_IMPORT_ROWS
    for data, kind, code in [(b"name\nx\n", "csv", "no_contact_column"),
                             (b"\xff\xfe", "csv", "not_utf8"), (b"{", "json", "unreadable"),
                             (b"x", "xlsx", "unknown_format")]:
        with pytest.raises(exports.ImportRefused) as e:
            exports.import_leads(conn, data, kind)
        assert e.value.code == code


def test_the_import_receipt_has_counts_only(conn, tmp_path):
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    data = b"name,email,phone\nJoanna Kowalska,joanna@example.com,+48000000101\n"
    r = exports.import_leads(conn, data, "csv", ledger=ledger)
    (receipt,) = [x for x in ledger.receipts() if x.id == r.receipt_id]
    assert receipt.kind == "tool_call" and receipt.details["tool"] == "leads.import"
    assert {k: receipt.details[k] for k in ("added", "skipped", "rejected", "rows")} == \
        {"added": 1, "skipped": 0, "rejected": 0, "rows": 1}
    text = json.dumps(receipt.__dict__)
    assert "Joanna" not in text and "joanna@" not in text and "000000101" not in text


# -- statement --------------------------------------------------------------------------

APPROVER = "ed25519:" + "ab" * 16


def approve(conn, ledger, aid, cid, run, action, payload, decided, executed="done"):
    with db.transaction(conn):
        if executed:
            conn.execute("INSERT INTO executions (approval_id, action, item_id, run_id, "
                         "status, created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (aid, action, cid, run, executed, decided, decided))
        conn.execute("INSERT INTO approvals (id, run_id, action, item_id, content_sha256, "
                     "chain_head, nonce, ts, status, approver, signature, decided_ts) VALUES "
                     "(?, ?, ?, ?, ?, 'h', ?, ?, 'approved', ?, 'sig', ?)",
                     (aid, run, action, cid, "c" * 64, "n_" + aid, int(decided), APPROVER,
                      int(decided)))
        conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                     "VALUES (?, ?, ?, ?)", (aid, run, canonical(payload).decode(), decided))
    return ledger.append(run, agent="human", kind="approval", output_sha256="c" * 64,
                         details={"action": action, "item_id": cid, "approver": APPROVER,
                                  "pending_id": aid}).hash


def snapshot(conn, sid, cid, start, stop, spend, created, by_day=None):
    body = {"date_start": start, "date_stop": stop, "totals": {"spend": spend},
            **({"spend_by_day": by_day} if by_day is not None else {})}
    with db.transaction(conn):
        conn.execute("INSERT INTO insights_snapshots (id, campaign_id, body, created_ts) "
                     "VALUES (?, ?, ?, ?)", (sid, cid, json.dumps(body), created))


def statement_fixture(conn, ledger, month):
    """Two campaigns in the month: c1 (PLN, a budget raise signed on day 3, daily snapshots
    plus an older overlapping 4-day one whose first day only it covers) and c2 (EUR); one
    run with model cost only."""
    _, _, start_ts, _ = exports.month_bounds(month)
    with db.transaction(conn):
        for cid, run, cur, budget in (("c1", "run_a", "PLN", 5000), ("c2", "run_b", "EUR", 2000)):
            conn.execute("INSERT INTO campaigns (id, run_id, name, objective, status, "
                         "daily_budget_minor, adapter, external_id, created_ts, currency) "
                         "VALUES (?, ?, ?, 'OUTCOME_TRAFFIC', 'ACTIVE', ?, 'mock', ?, ?, ?)",
                         (cid, run, f"Mugs {cid}", budget, "120" + cid[1], start_ts, cur))
    h1 = approve(conn, ledger, "ap_1", "c1", "run_a", "campaign.create",
                 {"campaign": {"daily_budget_minor": 4000}}, start_ts)
    approve(conn, ledger, "ap_2", "c1", "run_a", "campaign.update",
            {"change": {"kind": "budget", "to_minor": 5000}}, start_ts + 2 * 86400 + 60)
    approve(conn, ledger, "ap_3", "c2", "run_b", "campaign.create",
            {"campaign": {"daily_budget_minor": 2000}}, start_ts)
    d = [f"{month}-{n:02d}" for n in range(1, 6)]
    # overlaps the newer ones: only its first day (65.63 - 60.63 = 5.00) is left to book
    snapshot(conn, "s0", "c1", d[0], d[3], 65.63, start_ts + 3 * 86400)
    snapshot(conn, "s1", "c1", d[1], d[1], 10.10, start_ts + 4 * 86400)
    snapshot(conn, "s2", "c1", d[2], d[2], 20.20, start_ts + 4 * 86400 + 1)
    snapshot(conn, "s3", "c1", d[3], d[3], 30.33, start_ts + 4 * 86400 + 2)
    snapshot(conn, "s4", "c2", d[4], d[4], 7.07, start_ts + 5 * 86400)
    snapshot(conn, "s5", "c2", "2001-01-01", "2001-01-01", 5.0, start_ts)  # another month
    ledger.append("run_a", agent="producer", kind="model_call", cost_usd=0.012345)
    ledger.append("run_a", agent="copywriter", kind="model_call", cost_usd=0.000655)
    ledger.append("run_x", agent="producer", kind="model_call", cost_usd=0.5)
    return h1


@pytest.fixture
def ledger(conn, tmp_path):
    return db.open_ledger(conn, tmp_path / "ledger.key")


def test_statement_rows_and_exact_totals(conn, ledger):
    month = exports.current_month()
    h1 = statement_fixture(conn, ledger, month)
    every = exports.statement(conn, month)
    # model-only rows are dated by the ledger's clock (today), so they may sit anywhere
    rows = [r for r in every if r["campaign_item_id"]]
    assert [(r["date"][-2:], r["campaign_item_id"], r["spend"]) for r in rows] == [
        ("01", "c1", 5.00), ("02", "c1", 10.10), ("03", "c1", 20.20), ("04", "c1", 30.33),
        ("05", "c2", 7.07)]
    rows = rows[1:]  # the checks below are about the daily snapshots
    first = rows[0]
    assert set(first) == set(exports.STATEMENT_COLUMNS)
    assert first["approval_id"] == "ap_1" and first["approved_daily_budget"] == 40.0
    assert first["approval_receipt_hash"] == h1 and first["approved_by"] == "ed25519:abababab"
    assert first["meta_campaign_id"] == "1201" and first["currency"] == "PLN"
    assert first["model_cost_usd"] == 0.0 and rows[1]["model_cost_usd"] == 0.0
    assert rows[2]["approval_id"] == "ap_2" and rows[2]["approved_daily_budget"] == 50.0
    assert rows[3]["currency"] == "EUR" and rows[3]["approved_daily_budget"] == 20.0
    model_only = [r for r in every if not r["campaign_item_id"]]
    assert [(r["run_id"], r["model_cost_usd"], r["currency"]) for r in model_only] == [
        ("run_x", 0.5, "")]
    rows = exports.statement(conn, month)
    assert rows[0]["model_cost_usd"] == 0.013  # a run's model cost: on its first row
    t = exports.totals(rows)
    assert t["spend"] == {"EUR": 7.07, "PLN": 65.63}
    assert t["model_cost_usd"] == 0.513
    assert exports.statement(conn, month, exclude_runs={"run_a", "run_x"}) == \
        [r for r in rows if r["run_id"] == "run_b"]
    with pytest.raises(ValueError):
        exports.statement(conn, "2026-13")


def test_hledger_transactions_balance_per_commodity(conn, ledger):
    month = exports.current_month()
    statement_fixture(conn, ledger, month)
    rows = exports.statement(conn, month)
    journal = exports.statement_hledger(rows)
    txns = [t for t in journal.split("\n\n") if re.match(r"\d{4}-\d{2}-\d{2} ", t)]
    assert len(txns) == len(rows)
    spent = defaultdict(Decimal)
    for txn in txns:
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for line in txn.splitlines()[1:]:
            m = re.fullmatch(r"    (\S+)    (-?[0-9.]+) ([A-Z]{3})", line)
            if m:
                sums[m.group(3)] += Decimal(m.group(2))
                if m.group(1).startswith("expenses:"):
                    spent[m.group(3)] += Decimal(m.group(2))
        assert sums and all(v == 0 for v in sums.values()), txn
    assert spent == {"PLN": Decimal("65.63"), "EUR": Decimal("7.07"),
                     "USD": Decimal("0.513000")}
    assert "; approval:ap_1, approver:ed25519:abababab" in journal
    assert re.search(r"; receipt:[0-9a-f]{64}", journal)


def test_the_four_csv_profiles(conn, ledger):
    month = exports.current_month()
    statement_fixture(conn, ledger, month)
    rows = exports.statement(conn, month)
    heads = {p: exports.statement_csv(rows, p).split("\r\n")[0] for p in exports.PROFILES}
    assert heads == {"generic": "\ufeff" + ",".join(exports.STATEMENT_COLUMNS),
                     "xero": "\ufeffDate,Amount,Payee,Description,Reference",
                     "quickbooks": "\ufeffDate,Description,Amount",
                     "wfirma": "\ufeffData,Kontrahent,Opis,Kwota,Waluta"}
    xero = exports.statement_csv(rows, "xero").split("\r\n")
    assert xero[1].startswith(f"{month}-01,-5.00,Meta Platforms,") and xero[1].endswith(",ap_1")
    wf = exports.statement_csv(rows, "wfirma").split("\r\n")
    assert wf[1].endswith(",5.00,PLN")
    assert any(line.endswith(",0.50,USD") for line in wf)  # run_x's model cost
    with pytest.raises(ValueError):
        exports.statement_csv(rows, "sage")


def _campaign(conn, cid="c9", run="run_c", currency="PLN", budget=3000):
    with db.transaction(conn):
        conn.execute("INSERT INTO campaigns (id, run_id, name, objective, status, "
                     "daily_budget_minor, adapter, external_id, created_ts, currency) "
                     "VALUES (?, ?, 'Mugs', 'OUTCOME_TRAFFIC', 'ACTIVE', ?, 'mock', '1209', 0, ?)",
                     (cid, run, budget, currency))


def test_overlapping_windows_keep_every_day_in_its_own_month(conn, ledger):
    """Review fix E11: snapshots are pulled daily over the last 7 days, so windows always
    overlap. Daily rows (``spend_by_day``) are booked per day, the newest snapshot first;
    a window without them books only the days no newer snapshot has; each day lands in
    its own month."""
    _campaign(conn)
    snapshot(conn, "w1", "c9", "2026-09-27", "2026-10-03", 70.0, 1, by_day={
        f"2026-09-{d}": 10.0 for d in (27, 28, 29, 30)} | {
        f"2026-10-0{d}": 10.0 for d in (1, 2, 3)})
    snapshot(conn, "w2", "c9", "2026-09-29", "2026-10-05", 77.0, 2, by_day={
        "2026-09-29": 11.0, "2026-09-30": 11.0, "2026-10-01": 11.0, "2026-10-02": 11.0,
        "2026-10-03": 11.0, "2026-10-04": 11.0, "2026-10-05": 11.0})
    # a legacy snapshot (no daily rows), newest, overlapping 10-04 and 10-05 only
    snapshot(conn, "w3", "c9", "2026-10-04", "2026-10-07", 52.0, 3)
    sep = exports.statement(conn, "2026-09")
    assert [(r["date"], r["spend"]) for r in sep] == [
        ("2026-09-27", 10.0), ("2026-09-28", 10.0), ("2026-09-29", 11.0),
        ("2026-09-30", 11.0)]
    octo = {r["date"]: r["spend"] for r in exports.statement(conn, "2026-10")}
    # w3's 52.00 over 4 days: the newer daily values do not exist for its days (w3 is the
    # newest), so it is spread evenly; w2's 10-04/10-05 are not booked twice
    assert octo == {"2026-10-01": 11.0, "2026-10-02": 11.0, "2026-10-03": 11.0,
                    "2026-10-04": 13.0, "2026-10-05": 13.0, "2026-10-06": 13.0,
                    "2026-10-07": 13.0}


def test_only_executed_approvals_set_the_budget(conn, ledger):
    """Review fix E12: a signed budget change that failed (or never ran) did not change
    the budget; the statement shows the one in force."""
    _campaign(conn)
    approve(conn, ledger, "ap_c", "c9", "run_c", "campaign.create",
            {"campaign": {"daily_budget_minor": 3000}}, 1_790_000_000)
    approve(conn, ledger, "ap_f", "c9", "run_c", "campaign.update",
            {"change": {"kind": "budget", "to_minor": 9000}}, 1_790_000_100, executed="failed")
    approve(conn, ledger, "ap_n", "c9", "run_c", "campaign.update",
            {"change": {"kind": "budget", "to_minor": 8000}}, 1_790_000_200, executed="")
    day = exports._day(1_790_100_000)
    snapshot(conn, "x1", "c9", day, day, 12.0, 1_790_100_000)
    (row,) = exports.statement(conn, day[:7])
    assert row["approval_id"] == "ap_c" and row["approved_daily_budget"] == 30.0


# -- web: the pages, downloads, import and CSRF ---------------------------------------------

def seed_people(app):
    """Joanna, besides the synthetic seed; returns the number of leads."""
    conn = app.state.conn
    add_lead(conn, "l1", "Joanna Kowalska", "joanna@example.com", "+48000000101",
             message="mail me: joanna@example.com")
    return count(app)


def count(app):
    return app.state.conn.execute("SELECT count(*) FROM leads").fetchone()[0]


def test_session_export_and_import_with_csrf(tmp_path):
    app = make_app(tmp_path)
    n = seed_people(app)
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/leads/export?format=csv").status_code == 401
        assert c.get("/report/statement?month=2026-10").status_code == 401
        login(c, app)
        page = c.get("/leads").text
        assert 'action="/leads/import"' in page and "/leads/export?format=vcf" in page
        r = c.get("/leads/export?format=csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert r.headers["content-disposition"] == 'attachment; filename="leads.csv"'
        assert "joanna@example.com" in r.text  # the mock adapter: synthetic people
        assert c.get("/leads/export?format=xlsx").status_code == 400
        files = {"file": ("x.csv", b"name,phone\nNew Person,+48000000109\n", "text/csv")}
        r = c.post("/leads/import", data={"format": "csv"}, files=files)
        assert r.status_code == 403 and r.json() == {"error": "csrf"}
        r = c.post("/leads/import", data={"format": "csv", "csrf": "wrong" * 5}, files=files)
        assert r.status_code == 403 and count(app) == n
        r = c.post("/leads/import", data={"format": "csv", "csrf": csrf(c)}, files=files)
        assert r.status_code == 200 and 'id="import-result"' in r.text
        assert "1 added" in r.text and count(app) == n + 1
        bad = {"file": ("x.csv", b"name\nNobody\n", "text/csv")}
        r = c.post("/leads/import", data={"format": "csv", "csrf": csrf(c)}, files=bad)
        assert r.status_code == 400 and 'role="alert"' in r.text
        page = c.get("/report").text
        assert 'id="statement"' in page and "studio verify" in page
        r = c.get("/report/statement?month=2026-10&format=hledger")
        assert r.status_code == 200 and "statement-2026-10.journal" in \
            r.headers["content-disposition"]
        assert c.get("/report/statement?month=2026-13").status_code == 400
        assert c.get("/report/statement?month=2026-10&profile=sage").status_code == 400


def test_a_trial_account_gets_masked_files_and_no_import(tmp_path):
    app = make_app(tmp_path, **LIVE)  # live adapter: real people, masked for trial accounts
    n = seed_people(app)
    account(app, "trial")
    app.state.accounts.set_tier("trial", "trial")
    with TestClient(app, base_url=BASE) as c:
        login(c, app, "trial")
        page = c.get("/leads").text
        assert 'action="/leads/import"' not in page and "/leads/export?format=csv" in page
        for fmt in exports.LEAD_FORMATS:
            body = c.get(f"/leads/export?format={fmt}").text
            assert "joanna" not in body.lower() and "000000101" not in body, fmt
            assert "Kowalska" not in body, fmt
        files = {"file": ("x.csv", b"name,phone\nNew Person,+48000000109\n", "text/csv")}
        r = c.post("/leads/import", data={"format": "csv", "csrf": csrf(c)}, files=files)
        assert r.status_code == 403 and count(app) == n
        _, value = token(app, "trial", scope="read")
        for fmt in exports.LEAD_FORMATS:
            r = c.get(f"/api/v1/leads?format={fmt}", headers=bearer(value))
            assert r.status_code == 200, r.text
            assert "joanna" not in r.text.lower() and "000000101" not in r.text, fmt


# -- API ----------------------------------------------------------------------------------

def test_api_downloads_auth_and_another_accounts_runs(tmp_path):
    app = make_app(tmp_path)
    seed_people(app)
    conn, ledger = app.state.conn, app.state.ledger
    month = exports.current_month()
    statement_fixture(conn, ledger, month)
    owner, value = token(app, "owner", scope="read")
    _, other_value = token(app, "other", scope="read")
    # run_x was drafted through the owner's token: the other account never sees it
    with db.transaction(conn):
        conn.execute("INSERT INTO briefs (id, run_id, text, created_ts) VALUES "
                     "('b_0000000000000001', 'run_x', 't', ?)", (time.time(),))
        conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, created_ts) "
                     "VALUES ('b_0000000000000001', ?, ?, ?)",
                     (owner.account_id, owner.id, time.time()))
    with TestClient(app, base_url=BASE) as c:
        url = f"/api/v1/statement?month={month}&format=json"
        assert c.get(url).status_code == 401
        assert c.get("/api/v1/leads?format=csv").status_code == 401
        r = c.get(url, headers=bearer(value))
        assert r.status_code == 200 and r.headers["x-receipt-id"]
        assert f'statement-{month}.json' in r.headers["content-disposition"]
        assert "run_x" in r.text and r.json()["totals"]["model_cost_usd"] == 0.513
        r = c.get(url, headers=bearer(other_value))
        assert r.status_code == 200 and "run_x" not in r.text
        assert r.json()["totals"]["model_cost_usd"] == 0.013
        r = c.get(f"/api/v1/statement?month={month}&format=csv&profile=xero",
                  headers=bearer(value))
        assert r.text.startswith("\ufeffDate,Amount,Payee,Description,Reference")
        r = c.get(f"/api/v1/statement?month={month}&format=hledger", headers=bearer(value))
        assert r.headers["content-type"].startswith("text/plain") and "; approval:ap_1" in r.text
        assert c.get("/api/v1/statement?month=2026-13", headers=bearer(value)).status_code == 422
        r = c.get("/api/v1/leads?format=vcf", headers=bearer(value))
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/vcard")
        assert "EMAIL:joanna@example.com" in r.text
        r = c.get("/api/v1/leads", headers=bearer(value))  # without format: unchanged
        assert r.status_code == 200 and "leads" in r.json()
        # no token can import: the route is session + CSRF only, and not under /api/v1
        r = c.post("/api/v1/leads", headers=bearer(value), json=[])
        assert r.status_code in (404, 405)
        r = c.post("/leads/import", headers=bearer(value),
                   files={"file": ("x.csv", b"email\na@example.com\n", "text/csv")},
                   data={"format": "csv"})
        assert r.status_code in (401, 403)
        doc = c.get("/api/v1/openapi.json").json()
        assert {"/leads", "/statement"} <= set(doc["paths"])
        params = {p["name"] for p in doc["paths"]["/statement"]["get"]["parameters"]}
        assert {"month", "format", "profile"} <= params
    kinds = {r.details.get("tool") for r in ledger.receipts() if r.kind == "tool_call"}
    assert {"api.statement", "api.export_leads"} <= kinds


def test_i18n_parity_for_the_new_keys():
    from studio.ui import i18n
    from studio.ui.i18n_export import EN_EXPORT, PL_EXPORT
    assert set(EN_EXPORT) == set(PL_EXPORT)
    for key in EN_EXPORT:
        assert i18n.placeholders(EN_EXPORT[key]) == i18n.placeholders(PL_EXPORT[key]), key
        assert i18n.EN[key] == EN_EXPORT[key] and i18n.PL[key] == PL_EXPORT[key]
    codes = {"invalid_email", "invalid_phone", "no_contact", "unreadable", "not_utf8",
             "no_contact_column", "empty", "too_large", "too_many_rows", "unknown_format",
             "import_not_allowed"}
    assert {f"xport.err.{c}" for c in codes} <= set(EN_EXPORT)
    assert {f"xport.profile.{p}" for p in exports.PROFILES} <= set(EN_EXPORT)
