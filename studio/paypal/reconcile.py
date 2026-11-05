"""Reconcile an account's PayPal money moves against PayPal's own transaction list.

For each authorization and capture the studio recorded for the account (``funding_moves``,
the account's fundings by the app's ownership rule) one ``list_transactions`` call (the
Agent Toolkit tool, ``studio.paypal.agent_tools``: receipted, payer data stripped, own
money only) says whether PayPal lists it too:

- ``matched``: PayPal lists a transaction whose own ``transaction_id`` is the capture's
  (or refund's) id, with the same amount and currency. An authorization is ``matched``
  when its funding's capture is, or a listed transaction names the authorization id
  (``transaction_id`` / ``paypal_reference_id``);
- ``amount_mismatch``: listed, but with another amount or currency;
- ``pending``: a capture not listed yet and younger than ``RECON_LAG_S``: Transaction
  Search lags by up to ~3 hours, so a fresh capture is never "missing";
- ``held``: an authorization nothing was captured from (still held, voided, expired):
  not an alarm, Transaction Search does not list bare holds;
- ``missing``: only a capture or refund, old enough to be listed, absent from every page
  read of the searched window;
- ``unknown``: the move is older than the searched window, or the search ended with pages
  unread (``truncated``): it cannot be judged, so it is never called ``missing``.

Matching is by PayPal ids only. A ``custom_field`` (the ad's content hash) is not unique
per account, so it is never a key.

Window: the oldest recorded move (an hour earlier) to now, at most 31 days (PayPal's limit
for one search); older moves are lines with ``unknown``. Pages are followed (500
transactions each) within the toolkit's call budget (``MAX_CALLS_PER_TOOL``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

from studio import funding
from studio.paypal.agent_tools import (
    MAX_CALLS_PER_TOOL,
    PAGE_SIZE,
    account_refs,
    owned_funding_ids,
    run_list_page,
    tools_chain,
)
from studio.paypal.client import PayPalClient
from studio.receipts import Ledger

RECON_LAG_S = 3 * 3600
MAX_WINDOW_S = 31 * 86400
MOVES = ("authorization", "capture", "refund")
STATUSES = ("matched", "pending", "held", "missing", "amount_mismatch", "unknown")


@dataclass
class ReconLine:
    funding_id: str
    move: str
    paypal_id: str
    amount_minor: int
    currency: str
    status: str

    def view(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReconReport:
    lines: list[ReconLine] = field(default_factory=list)
    window_start: float = 0.0
    window_end: float = 0.0
    pages_read: int = 0
    truncated: bool = False


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _same_amount(move: dict[str, Any], info: dict[str, Any]) -> bool:
    amount = info.get("transaction_amount")
    if not isinstance(amount, dict):
        return False
    try:
        minor = funding.parse_amount(amount.get("value"), str(amount.get("currency_code")))
    except funding.FundingError:
        return False
    return minor == move["amount_minor"] and amount.get("currency_code") == move["currency"]


def reconcile(*, conn: Any, client: PayPalClient, ledger: Ledger, account_id: str,
              now: float) -> list[ReconLine]:
    """The lines of ``reconcile_report``."""
    return reconcile_report(conn=conn, client=client, ledger=ledger, account_id=account_id,
                            now=now).lines


def reconcile_report(*, conn: Any, client: PayPalClient, ledger: Ledger, account_id: str,
                     now: float) -> ReconReport:
    """One line per authorization / capture / refund of the account's fundings."""
    start = now - MAX_WINDOW_S
    with conn.lock:
        refs = account_refs(conn, account_id)
        wanted = [(fid, m) for fid in owned_funding_ids(conn, account_id)
                  for m in funding.moves(conn, fid)
                  if m["move"] in MOVES and m["paypal_id"]]
    inside = [m["ts"] for _, m in wanted if m["ts"] >= start]
    report = ReconReport(window_start=start, window_end=now)
    own_ids: set[str] = set()  # transaction_id of every listed transaction
    ref_ids: set[str] = set()  # paypal_reference_id of every listed transaction
    infos: dict[str, list[dict[str, Any]]] = {}
    if inside:
        report.window_start = max(start, min(inside) - 3600)
        for page in range(1, MAX_CALLS_PER_TOOL + 1):
            answer, report.truncated = run_list_page(
                client=client, ledger=ledger, conn=conn, chain_id=tools_chain(account_id),
                account_refs=refs,
                params={"start_date": _iso(report.window_start), "end_date": _iso(now),
                        "page_size": PAGE_SIZE, "page": page})
            report.pages_read += 1
            for t in answer.get("transaction_details") or []:
                info = t.get("transaction_info") if isinstance(t, dict) else None
                if not isinstance(info, dict):
                    continue
                if isinstance(info.get("transaction_id"), str):
                    own_ids.add(info["transaction_id"])
                    infos.setdefault(info["transaction_id"], []).append(info)
                if isinstance(info.get("paypal_reference_id"), str):
                    ref_ids.add(info["paypal_reference_id"])
            if not report.truncated:
                break
    captured_ok: set[str] = set()  # fundings whose capture is listed
    status: dict[tuple[str, str, str], str] = {}
    for fid, m in wanted:
        key = (fid, m["move"], m["paypal_id"])
        if m["ts"] < start:
            status[key] = "unknown"
        elif m["move"] == "authorization":
            continue  # after the captures
        elif m["paypal_id"] in own_ids:
            ok = any(_same_amount(m, i) for i in infos[m["paypal_id"]])
            status[key] = "matched" if ok else "amount_mismatch"
            if m["move"] == "capture":
                captured_ok.add(fid)
        elif now - m["ts"] < RECON_LAG_S:
            status[key] = "pending"
        else:
            status[key] = "unknown" if report.truncated else "missing"
    for fid, m in wanted:
        key = (fid, m["move"], m["paypal_id"])
        if m["move"] == "authorization" and key not in status:
            listed = m["paypal_id"] in own_ids or m["paypal_id"] in ref_ids
            status[key] = "matched" if listed or fid in captured_ok else "held"
    report.lines = [ReconLine(fid, m["move"], m["paypal_id"], m["amount_minor"],
                              m["currency"], status[(fid, m["move"], m["paypal_id"])])
                    for fid, m in wanted]
    return report


def summary(report: ReconReport) -> dict[str, Any]:
    """The answer of the ``paypal_transactions`` operation."""
    out: dict[str, Any] = {
        "lines": [ln.view() for ln in report.lines],
        # no page count or truncation flag: they would size the shared merchant's list;
        # incompleteness shows per line as ``unknown``
        "window_start": _iso(report.window_start), "window_end": _iso(report.window_end)}
    for st in STATUSES:
        out[st] = sum(1 for ln in report.lines if ln.status == st)
    return out
