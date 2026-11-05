"""Reconciliation of an account's PayPal moves against PayPal's transaction list: matched
by PayPal ids only, ``pending`` while Transaction Search lags, per account."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from studio import funding
from studio.paypal.client import PayPalClient
from studio.paypal.reconcile import RECON_LAG_S, reconcile, reconcile_report, summary
from tests.test_paypal_agent_tools import World, _brief

NOW = 1_800_000_000.0
SHA = "c" * 64
CAP = "3C679366HH908993F"
AUTH = "7AU11111AB222222C"


def listed(tid: str, value: str = "220.50", custom: str = SHA, ref: str = "") -> dict:
    info = {"transaction_id": tid, "custom_field": custom,
            "transaction_amount": {"currency_code": "EUR", "value": value}}
    if ref:
        info["paypal_reference_id"] = ref
    return {"transaction_info": info,
            "payer_info": {"email_address": "buyer@example.com"}}


def captured(conn, run, item, *, sha=SHA, auth=AUTH, cap=CAP, ts=NOW - 5 * 3600,
             order=None):
    q = funding.quote("EUR", 21000, 1, Decimal(5))  # total 220.50
    f = funding.create(conn, run_id=run, item_id=item, content_sha256=sha, q=q, now=ts)
    funding.advance(conn, f.id, "created", expect="created", now=ts,
                    order_id=order or ("O" + auth))
    funding.advance(conn, f.id, "payer_approved", expect="created", now=ts)
    f = funding.advance(conn, f.id, "authorized", expect="payer_approved", now=ts,
                        authorization_id=auth, authorized_minor=q.total_minor)
    funding.record_move(conn, f, "authorization", paypal_id=auth, amount_minor=q.total_minor,
                        now=ts)
    f = funding.advance(conn, f.id, "captured", expect="authorized", now=ts,
                        capture_id=cap, captured_minor=q.total_minor)
    funding.record_move(conn, f, "capture", paypal_id=cap, amount_minor=q.total_minor, now=ts)
    return f


@pytest.fixture()
def w(tmp_path):
    world = World(tmp_path)
    _brief(world.conn, "b_ui", "run_ui")  # the studio's own work: every account sees it
    return world


def run(w, account="acc_1", now=NOW):
    return reconcile(conn=w.conn, client=w.client, ledger=w.ledger, receipt_key=w.key,
                     account_id=account, now=now)


def by_move(lines):
    return {ln.move: ln.status for ln in lines}


def test_capture_is_matched_by_id(w):
    captured(w.conn, "run_ui", "cre_1")
    w.sandbox.transactions = [listed(CAP, ref=AUTH)]
    lines = run(w)
    assert by_move(lines) == {"authorization": "matched", "capture": "matched"}
    report = reconcile_report(conn=w.conn, client=w.client, ledger=w.ledger, receipt_key=w.key,
                              account_id="acc_1", now=NOW)
    out = summary(report)
    assert out["matched"] == 2 and not {"truncated", "pages_read"} & set(out)
    assert report.truncated is False and report.pages_read == 1
    assert out["window_start"] < out["window_end"]
    assert len(w.sandbox.calls("/v1/reporting/transactions")) == 2  # run + report


def test_custom_field_alone_never_matches(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 600)
    w.sandbox.transactions = [listed("9ZZ99999ZZ999999Z")]  # same content hash, other ids
    assert by_move(run(w)) == {"authorization": "held", "capture": "pending"}
    w.sandbox.transactions = [listed("9ZZ99999ZZ999999Z")]
    old = run(w, now=NOW + 5 * 3600)
    assert by_move(old) == {"authorization": "held", "capture": "missing"}


def test_recent_move_is_pending_not_missing(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 600)
    out = summary(reconcile_report(conn=w.conn, client=w.client, ledger=w.ledger, receipt_key=w.key,
                                   account_id="acc_1", now=NOW))
    assert out["pending"] == 1 and out["held"] == 1 and out["missing"] == 0


def authorized_only(conn, run, item, *, ts, sha=SHA, auth=AUTH):
    q = funding.quote("EUR", 21000, 1, Decimal(5))
    f = funding.create(conn, run_id=run, item_id=item, content_sha256=sha, q=q, now=ts)
    funding.advance(conn, f.id, "created", expect="created", now=ts, order_id="O" + auth)
    funding.advance(conn, f.id, "payer_approved", expect="created", now=ts)
    f = funding.advance(conn, f.id, "authorized", expect="payer_approved", now=ts,
                        authorization_id=auth, authorized_minor=q.total_minor)
    funding.record_move(conn, f, "authorization", paypal_id=auth, amount_minor=q.total_minor,
                        now=ts)
    return f


def test_old_absent_capture_is_missing(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 4 * 3600)
    assert 4 * 3600 > RECON_LAG_S
    assert by_move(run(w))["capture"] == "missing"


def test_old_uncaptured_authorization_is_held(w):
    authorized_only(w.conn, "run_ui", "cre_1", ts=NOW - 10 * 3600)
    (line,) = run(w)
    assert line.move == "authorization" and line.status == "held"


def test_captured_authorization_is_matched_via_its_capture(w):
    captured(w.conn, "run_ui", "cre_1")
    w.sandbox.transactions = [listed(CAP)]  # no reference to the authorization at all
    assert by_move(run(w)) == {"authorization": "matched", "capture": "matched"}


def test_authorization_named_by_a_transactions_reference_is_matched(w):
    authorized_only(w.conn, "run_ui", "cre_1", ts=NOW - 10 * 3600)
    w.sandbox.transactions = [listed("5XX55555XX555555X", ref=AUTH)]
    (line,) = run(w)
    assert line.status == "matched"


def paged(w, pages):
    """A sandbox that serves ``pages`` (lists of transactions), PayPal's page numbering."""
    seen = []

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        n = int(request.url.params.get("page", "1"))
        seen.append(n)
        return httpx.Response(200, json={"transaction_details": pages[n - 1], "page": n,
                                         "total_pages": len(pages)})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None)
    return seen


def test_second_page_is_read(w):
    captured(w.conn, "run_ui", "cre_1")
    seen = paged(w, [[listed("9ZZ99999ZZ999999Z")], [listed(CAP)]])
    report = reconcile_report(conn=w.conn, client=w.client, ledger=w.ledger, receipt_key=w.key,
                              account_id="acc_1", now=NOW)
    assert seen == [1, 2] and report.pages_read == 2 and report.truncated is False
    assert by_move(report.lines)["capture"] == "matched"


def test_unread_pages_make_absent_moves_unknown(w, monkeypatch):
    from studio.paypal import reconcile as mod

    captured(w.conn, "run_ui", "cre_1")
    monkeypatch.setattr(mod, "MAX_CALLS_PER_TOOL", 2)
    seen = paged(w, [[listed("9ZZ99999ZZ999999Z")]] * 5)
    report = reconcile_report(conn=w.conn, client=w.client, ledger=w.ledger, receipt_key=w.key,
                              account_id="acc_1", now=NOW)
    assert seen == [1, 2] and report.truncated is True
    assert by_move(report.lines)["capture"] == "unknown"
    out = summary(report)
    assert "truncated" not in out and out["unknown"] == 1 and out["missing"] == 0


def test_moves_older_than_the_window_are_unknown(w):
    captured(w.conn, "run_ui", "cre_1", ts=NOW - 40 * 86400)
    lines = run(w)
    assert {ln.status for ln in lines} == {"unknown"} and len(lines) == 2
    assert w.sandbox.requests == []  # nothing inside the window: no call


def test_amount_mismatch(w):
    captured(w.conn, "run_ui", "cre_1")
    w.sandbox.transactions = [listed(CAP, value="100.00", ref=AUTH)]
    assert by_move(run(w))["capture"] == "amount_mismatch"


def test_reconcile_is_per_account(w):
    _brief(w.conn, "b_api1", "run_api1", api="acc_1")
    _brief(w.conn, "b_api2", "run_api2", api="acc_2")
    captured(w.conn, "run_api1", "cre_1", sha="1" * 64, auth="1AU11111AB222222C",
             cap="1CA11111AB222222C")
    captured(w.conn, "run_api2", "cre_2", sha="2" * 64, auth="2AU11111AB222222C",
             cap="2CA11111AB222222C")
    w.sandbox.transactions = [listed("1CA11111AB222222C", custom="1" * 64),
                              listed("2CA11111AB222222C", custom="2" * 64)]
    one = run(w, "acc_1")
    two = run(w, "acc_2")
    assert {ln.paypal_id for ln in one} == {"1AU11111AB222222C", "1CA11111AB222222C"}
    assert {ln.paypal_id for ln in two} == {"2AU11111AB222222C", "2CA11111AB222222C"}
    assert by_move(one)["capture"] == "matched"


def test_no_fundings_means_no_call(w):
    assert run(w, "acc_none") == []
    assert w.sandbox.requests == []


def test_the_call_is_receipted_in_the_accounts_tools_chain(w):
    captured(w.conn, "run_ui", "cre_1")
    run(w)
    (r,) = w.ledger.receipts("acct:acc_1:paypal-tools")
    assert r.details["tool"] == "list_transactions"


def test_reconcile_receipt_count_is_independent_of_pages(w):
    from studio.receipts import verify

    captured(w.conn, "run_ui", "cre_1")
    chain = "acct:acc_1:paypal-tools"
    counts = []
    for pages in ([[listed(CAP)]], [[listed("9ZZ99999ZZ999999Z")]] * 2 + [[listed(CAP)]]):
        before = len(w.ledger.receipts(chain))
        seen = paged(w, pages)
        report = reconcile_report(conn=w.conn, client=w.client, ledger=w.ledger, receipt_key=w.key,
                                  account_id="acc_1", now=NOW)
        assert len(seen) == len(pages) and by_move(report.lines)["capture"] == "matched"
        new = w.ledger.receipts(chain)[before:]
        counts.append(len(new))
        (r,) = new
        assert r.details["step"] == "paypal.reconcile" and r.details["ok"] is True
        assert r.details["path"] == "/v1/reporting/transactions"
        assert not {"page", "pages", "pages_read", "status"} & set(r.details)
    assert counts == [1, 1]
    path = w.tmp / "receipts.jsonl"
    path.write_text(w.export())
    assert verify(path, w.ledger.public_key).ok


def test_a_failed_reconcile_leaves_one_failed_receipt(w):
    import pytest as _pytest

    from studio.paypal.agent_tools import ToolRefused

    captured(w.conn, "run_ui", "cre_1")

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.params.get("page") == "2":
            return httpx.Response(403, json={"name": "NOT_AUTHORIZED"})
        return httpx.Response(200, json={"transaction_details": [], "page": 1,
                                         "total_pages": 3})

    w.client = PayPalClient("id", "secret", transport=httpx.MockTransport(handler),
                            sleep=lambda s: None)
    with _pytest.raises(ToolRefused):
        run(w)
    (r,) = w.ledger.receipts("acct:acc_1:paypal-tools")
    assert r.details["step"] == "paypal.reconcile" and r.details["ok"] is False


def test_reconcile_receipt_hides_other_accounts_data(w):
    from studio.receipts import verify

    captured(w.conn, "run_ui", "cre_1")
    hashes = []
    for foreign in ([], [listed("8FF11111AB222222C", custom="f" * 64)]):
        w.sandbox.transactions = [listed(CAP, ref=AUTH), *foreign]
        assert by_move(run(w)) == {"authorization": "matched", "capture": "matched"}
        r = w.ledger.receipts("acct:acc_1:paypal-tools")[-1]
        assert r.details["step"] == "paypal.reconcile" and r.details["ok"] is True
        hashes.append(r.details["body_sha256"])
    assert hashes[0] == hashes[1] and len(hashes[0]) == 64
    path = w.tmp / "receipts.jsonl"
    path.write_text(w.export())
    assert verify(path, w.ledger.public_key).ok
