"""Reconcile an account's PayPal money moves against PayPal's own transaction list.

For each authorization and capture the studio recorded for the account (``funding_moves``,
the account's fundings by the app's ownership rule) one ``list_transactions`` call (the
Agent Toolkit tool, ``studio.paypal.agent_tools``: receipted, payer data stripped, own
money only) says whether PayPal lists it too:

- ``matched``: PayPal lists a transaction whose ``transaction_id`` or
  ``paypal_reference_id`` is the move's PayPal id, with the same amount and currency;
- ``amount_mismatch``: listed, but with another amount or currency;
- ``pending``: not listed yet and younger than ``RECON_LAG_S``: Transaction Search lags
  by up to ~3 hours, so a fresh capture is never "missing";
- ``missing``: not listed, and old enough that it should be.

Matching is by PayPal ids only. A ``custom_field`` (the ad's content hash) is not unique
per account, so it is never a key.

Scope: the window is the oldest recorded move to now, at most 31 days (PayPal's limit for
one call); older moves are not reconciled. One page of up to 500 transactions is read.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from studio import funding
from studio.paypal.agent_tools import (
    account_refs,
    owned_funding_ids,
    run_read_tool,
    tools_chain,
)
from studio.paypal.client import PayPalClient
from studio.receipts import Ledger

RECON_LAG_S = 3 * 3600
MAX_WINDOW_S = 31 * 86400
PAGE_SIZE = 500
MOVES = ("authorization", "capture")
STATUSES = ("matched", "pending", "missing", "amount_mismatch")


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


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _listed(answer: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """PayPal id (``transaction_id`` or ``paypal_reference_id``) → its transaction infos."""
    out: dict[str, list[dict[str, Any]]] = {}
    for t in answer.get("transaction_details") or []:
        info = t.get("transaction_info") if isinstance(t, dict) else None
        if not isinstance(info, dict):
            continue
        for key in ("transaction_id", "paypal_reference_id"):
            if isinstance(info.get(key), str):
                out.setdefault(info[key], []).append(info)
    return out


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
    """One line per authorization / capture of the account's fundings in the window."""
    start = now - MAX_WINDOW_S
    with conn.lock:
        refs = account_refs(conn, account_id)
        wanted = [(fid, m) for fid in owned_funding_ids(conn, account_id)
                  for m in funding.moves(conn, fid)
                  if m["move"] in MOVES and m["paypal_id"] and m["ts"] >= start]
    if not wanted:
        return []
    begin = max(start, min(m["ts"] for _, m in wanted) - 3600)
    answer = run_read_tool(
        client=client, ledger=ledger, conn=conn, chain_id=tools_chain(account_id),
        account_refs=refs, name="list_transactions",
        params={"start_date": _iso(begin), "end_date": _iso(now), "page_size": PAGE_SIZE})
    listed = _listed(answer)
    lines = []
    for fid, m in wanted:
        infos = listed.get(m["paypal_id"])
        if infos:
            # amounts are compared on the transaction that IS the move; a capture that only
            # references an authorization proves it is there, not what was held
            direct = [i for i in infos if i.get("transaction_id") == m["paypal_id"]]
            status = "matched" if not direct or any(_same_amount(m, i) for i in direct) \
                else "amount_mismatch"
        else:
            status = "pending" if now - m["ts"] < RECON_LAG_S else "missing"
        lines.append(ReconLine(fid, m["move"], m["paypal_id"], m["amount_minor"],
                               m["currency"], status))
    return lines


def summary(lines: list[ReconLine]) -> dict[str, Any]:
    """The answer of the ``paypal_transactions`` operation."""
    out: dict[str, Any] = {"lines": [ln.view() for ln in lines]}
    for status in STATUSES:
        out[status] = sum(1 for ln in lines if ln.status == status)
    return out
