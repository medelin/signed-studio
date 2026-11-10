"""``/money``: the signed-in account's own PayPal fundings in an AG Grid (sandbox).

- ``GET /money``: the page (303 to ``/login`` without a session). It works with PayPal off
  and with no fundings (an empty state).
- ``GET /api/money``: ``{"currency", "rows": [...]}``, one row per funding of the account
  (the app's ownership rule, ``studio.paypal.agent_tools.owned_funding_ids``). Rows carry
  no payer data. Besides the display strings every row has numeric twins (``amount_n``...,
  major units, for the grid's number filters) and ``created_ts``. ``totals`` is one row per
  currency (sums of amount, held, taken, refunded; the pinned bottom row) and ``daily`` the
  held/taken/refunded sums per UTC day and currency (the chart).
  ``recon`` is ``null``: the default answer never calls PayPal.
  A sandbox instance (``STUDIO_SANDBOX=1``) whose account has no fundings answers with four
  invented rows instead (``examples: true``, each row ``example: true``, ids ``example-*``):
  built in code, never stored, never checked with PayPal; the page labels them and the
  CSV export is off for them.
- ``GET /api/money?reconcile=1``: runs the reconciliation (``studio.paypal.reconcile``,
  Transaction Search) once and fills ``recon`` with the worst status of a row's lines.
  The result is reused for ``RECON_COOLDOWN_S`` per account, so the button cannot hammer
  PayPal. 503 when PayPal is off.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from studio import funding
from studio.paypal import agent_tools, reconcile
from studio.paypal.receipt_key import receipt_key
from studio.receipts import Ledger
from studio.settings import Settings

RECON_COOLDOWN_S = 30.0
NO_STORE = {"Cache-Control": "no-store"}
# the worst status of a funding's lines wins
SUM_KEYS = ("amount", "authorized", "captured", "refunded")
SEVERITY = ("missing", "amount_mismatch", "unknown", "pending", "held", "matched")


def worst(statuses: list[str]) -> str | None:
    for st in SEVERITY:
        if st in statuses:
            return st
    return None


def major(minor: int, currency: str) -> float:
    """Minor units to major units as a number (the grid's number filters and the chart)."""
    return float(Decimal(minor).scaleb(-funding.exponent(currency)))


def totals_of(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per currency: sums of amount, held, taken, refunded (the pinned bottom row).
    Summed from the display strings so the cents are exact; first appearance order."""
    sums: dict[str, dict[str, Decimal]] = {}
    count: dict[str, int] = {}
    for r in rows:
        acc = sums.setdefault(r["currency"], dict.fromkeys(SUM_KEYS, Decimal(0)))
        count[r["currency"]] = count.get(r["currency"], 0) + 1
        for k in SUM_KEYS:
            acc[k] += Decimal(r[k])
    return [{"currency": cur, "count": count[cur],
             **{k: str(v) for k, v in acc.items()},
             **{k + "_n": float(v) for k, v in acc.items()}} for cur, acc in sums.items()]


def daily_of(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Held, taken and refunded per UTC day and currency, oldest day first."""
    days: dict[tuple[str, str], dict[str, Decimal]] = {}
    for r in rows:
        acc = days.setdefault((r["created"][:10], r["currency"]),
                              dict.fromkeys(("authorized", "captured", "refunded"), Decimal(0)))
        for k in acc:
            acc[k] += Decimal(r[k])
    return [{"day": day, "currency": cur, "held": float(a["authorized"]),
             "taken": float(a["captured"]), "refunded": float(a["refunded"])}
            for (day, cur), a in sorted(days.items())]


def rows_of(conn: Any, account_id: str, recon: dict[str, str] | None) -> list[dict[str, Any]]:
    """The account's fundings, newest first. Call without ``conn.lock``."""
    with conn.lock:
        ids = agent_tools.owned_funding_ids(conn, account_id)
        out = []
        for fid in ids:
            f = funding.get(conn, fid)
            mandate = conn.execute("SELECT 1 FROM mandate_charges WHERE funding_id = ? AND "
                                   "status = 'done'", (fid,)).fetchone()
            out.append((f, bool(mandate), [m["receipt_id"] for m in funding.moves(conn, fid)
                                           if m["receipt_id"]]))
    out.sort(key=lambda x: x[0].created_ts, reverse=True)
    return [{
        "funding_id": f.id, "run_id": f.run_id, "created_ts": int(f.created_ts),
        "amount_n": major(f.total_minor, f.currency),
        "authorized_n": major(f.authorized_minor, f.currency),
        "captured_n": major(f.captured_minor, f.currency),
        "refunded_n": major(f.refunded_minor, f.currency),
        "created": dt.datetime.fromtimestamp(f.created_ts, dt.UTC).strftime("%Y-%m-%d %H:%M"),
        "state": f.state, "currency": f.currency,
        "amount": funding.format_amount(f.total_minor, f.currency),
        "authorized": funding.format_amount(f.authorized_minor, f.currency),
        "captured": funding.format_amount(f.captured_minor, f.currency),
        "refunded": funding.format_amount(f.refunded_minor, f.currency),
        "via": "mandate" if mandate else "checkout", "receipt_ids": receipts,
        "recon": (recon or {}).get(f.id),
    } for f, mandate, receipts in out]


def example_rows(now: float) -> list[dict[str, Any]]:
    """Four invented rows for a sandbox account with no fundings yet: held, captured,
    partly refunded and a mandate charge. Built in code on every call; never stored, never
    in the ledger, never sent to PayPal. Newest first, like the real list."""
    cur = "EUR"
    #       id, hours ago, state, via, amount, held, taken, refunded (minor units)
    spec = (("example-mandate", 3, "captured", "mandate", 3150, 3150, 3150, 0),
            ("example-held", 5, "authorized", "checkout", 22050, 22050, 0, 0),
            ("example-captured", 29, "captured", "checkout", 22050, 22050, 22050, 0),
            ("example-refunded", 53, "partially_refunded", "checkout", 11025, 11025, 11025, 4000))
    out = []
    for fid, hours, state, via, amount, held, taken, refunded in spec:
        ts = now - hours * 3600
        out.append({
            "funding_id": fid, "run_id": "example", "created_ts": int(ts), "example": True,
            "amount_n": major(amount, cur), "authorized_n": major(held, cur),
            "captured_n": major(taken, cur), "refunded_n": major(refunded, cur),
            "created": dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d %H:%M"),
            "state": state, "currency": cur,
            "amount": funding.format_amount(amount, cur),
            "authorized": funding.format_amount(held, cur),
            "captured": funding.format_amount(taken, cur),
            "refunded": funding.format_amount(refunded, cur),
            "via": via, "receipt_ids": [], "recon": None})
    return out


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             templates: Jinja2Templates, session_of: Callable[[Request], str | None]) -> None:
    templates.env.globals["paypal_on"] = lambda: getattr(app.state, "funder", None) is not None
    key = receipt_key(settings.data_dir)
    lock = threading.Lock()  # guards the two dicts below only, never a PayPal call
    locks: dict[str, threading.Lock] = {}  # one reconciliation at a time per account
    cache: dict[str, tuple[float, dict[str, str] | None]] = {}  # None: the call failed

    def account_of(sid: str) -> str:
        accounts = getattr(app.state, "accounts", None)
        return (accounts.session_account(sid) if accounts is not None else None) or ""

    def checked(account_id: str) -> dict[str, str]:
        """The reconciliation of this account, reused (a failure too) for the cooldown;
        raises ``RuntimeError`` when the last attempt failed."""
        with lock:
            mine = locks.setdefault(account_id, threading.Lock())
        with mine:
            now = time.time()
            hit = cache.get(account_id)
            if hit is not None and now - hit[0] < RECON_COOLDOWN_S:
                if hit[1] is None:
                    raise RuntimeError("PayPal check failed recently")
                return hit[1]
            try:
                report = reconcile.reconcile_report(
                    conn=conn, client=app.state.funder.money.client, ledger=ledger,
                    receipt_key=key, account_id=account_id, now=now)
            except Exception:
                with lock:
                    cache[account_id] = (now, None)
                raise
            by: dict[str, list[str]] = {}
            for ln in report.lines:
                by.setdefault(ln.funding_id, []).append(ln.status)
            result = {fid: s for fid, v in by.items() if (s := worst(v))}
            with lock:
                cache[account_id] = (now, result)
            return result

    @app.get("/money", response_class=HTMLResponse)
    def money_page(request: Request) -> Response:
        if session_of(request) is None:
            return RedirectResponse("/login?next=/money", 303)
        return templates.TemplateResponse(request, "money.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch, "logged_in": True,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
        })

    @app.get("/api/money")
    def money_api(request: Request, check: int = Query(0, alias="reconcile")) -> Response:
        sid = session_of(request)
        if sid is None:
            return JSONResponse({"error": "login_required"}, 401)
        account_id = account_of(sid)
        recon = None
        if check:
            if settings.read_only:
                return JSONResponse({"error": "read_only"}, 503)
            if getattr(app.state, "funder", None) is None:
                return JSONResponse({"error": "paypal_unavailable"}, 503)
            try:
                recon = checked(account_id)
            except Exception:  # noqa: BLE001 - PayPal or the toolkit failed: say so, no 500
                return JSONResponse({"error": "paypal"}, 502)
        rows = rows_of(conn, account_id, recon)
        # a sandbox judge's fundings are reset every hour: show labelled examples instead of
        # an empty page (not stored, not checked with PayPal, not exported)
        examples = not rows and bool(getattr(getattr(app.state, "sandbox", None), "enabled",
                                             False))
        if examples:
            rows = example_rows(time.time())
        return JSONResponse({"currency": rows[0]["currency"] if rows else "", "rows": rows,
                             "account": account_id, "totals": totals_of(rows),
                             "daily": daily_of(rows), "examples": examples},
                            headers=NO_STORE)
