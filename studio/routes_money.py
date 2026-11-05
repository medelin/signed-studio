"""``/money``: the signed-in account's own PayPal fundings in an AG Grid (sandbox).

- ``GET /money``: the page (303 to ``/login`` without a session). It works with PayPal off
  and with no fundings (an empty state).
- ``GET /api/money``: ``{"currency", "rows": [...]}``, one row per funding of the account
  (the app's ownership rule, ``studio.paypal.agent_tools.owned_funding_ids``). Rows carry
  no payer data. ``recon`` is ``null``: the default answer never calls PayPal.
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
SEVERITY = ("missing", "amount_mismatch", "unknown", "pending", "held", "matched")


def worst(statuses: list[str]) -> str | None:
    for st in SEVERITY:
        if st in statuses:
            return st
    return None


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
        "funding_id": f.id, "run_id": f.run_id,
        "created": dt.datetime.fromtimestamp(f.created_ts, dt.UTC).strftime("%Y-%m-%d %H:%M"),
        "state": f.state, "currency": f.currency,
        "amount": funding.format_amount(f.total_minor, f.currency),
        "authorized": funding.format_amount(f.authorized_minor, f.currency),
        "captured": funding.format_amount(f.captured_minor, f.currency),
        "refunded": funding.format_amount(f.refunded_minor, f.currency),
        "via": "mandate" if mandate else "checkout", "receipt_ids": receipts,
        "recon": (recon or {}).get(f.id),
    } for f, mandate, receipts in out]


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
            if getattr(app.state, "funder", None) is None:
                return JSONResponse({"error": "paypal_unavailable"}, 503)
            try:
                recon = checked(account_id)
            except Exception:  # noqa: BLE001 - PayPal or the toolkit failed: say so, no 500
                return JSONResponse({"error": "paypal"}, 502)
        rows = rows_of(conn, account_id, recon)
        return JSONResponse({"currency": rows[0]["currency"] if rows else "", "rows": rows},
                            headers=NO_STORE)
