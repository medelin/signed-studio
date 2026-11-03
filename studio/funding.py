"""The money model: what one campaign's PayPal funding is, which states it moves through,
and the arithmetic, with no network access. ``studio.paypal.money`` drives PayPal and
writes the receipts; this module only decides what is allowed and what the numbers are.

Amounts are integers in the currency's minor unit (pence, cents; whole units for the
zero-decimal currencies ``ZERO_DECIMAL``), never floats. ``format_amount`` gives the string
PayPal expects (``"48.00"``) and ``parse_amount`` reads one back, strictly.

- ``quote``: the daily budget × days is the ad budget; the studio fee is ``fee_pct`` of
  it, rounded half up to the minor unit; the PayPal order total is budget + fee.
- ``funding_block``: the closed set of facts the owner signs about the money (order id,
  intent, amount, currency, payee, the ad's ``content_sha256``). ``funding_sha256`` is its
  canonical hash; every value is an id, a code, a digest or an integer, so the block can
  sit in a public receipt byte-exact.
- States: ``created`` (order made, ``custom_id`` = the ad's content hash) →
  ``payer_approved`` (the buyer approved in PayPal) → ``authorized`` (money held) →
  ``captured`` (money taken) → ``partially_refunded`` / ``refunded``. ``voided`` ends an
  authorization that was never captured; ``expired`` ends an order never authorized.
  ``TRANSITIONS`` is the whole graph: any other move is refused.
- ``reconcile``: what the owner owes and gets back once the ad spend is known. The studio
  fee is charged on what was spent, not on what was held, so unspent budget comes back
  with its share of the fee.
- ``funding_moves`` is the money ledger: one row per PayPal movement (order,
  authorization, capture, void, refund), each with the receipt that records it.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from studio.approvals import transaction
from studio.receipts import canonical, new_id, sha256_hex

SCHEMA_ID = "funding/v1"
INTENT = "AUTHORIZE"
# PayPal's Orders v2 currencies; HUF, JPY and TWD take no decimals in PayPal.
CURRENCIES = frozenset({"AUD", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD",
                        "HUF", "ILS", "JPY", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "SEK",
                        "SGD", "THB", "TWD", "USD"})
ZERO_DECIMAL = frozenset({"HUF", "JPY", "TWD"})
MAX_DAYS = 90
MAX_TOTAL_MINOR = 10_000_000  # 100,000.00: a sandbox demo never needs more

STATES = ("created", "payer_approved", "authorized", "captured", "partially_refunded",
          "refunded", "voided", "expired")
TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"payer_approved", "expired"}),
    "payer_approved": frozenset({"authorized", "expired"}),
    "authorized": frozenset({"captured", "voided"}),
    "captured": frozenset({"partially_refunded", "refunded"}),
    "partially_refunded": frozenset({"partially_refunded", "refunded"}),
    "refunded": frozenset(),
    "voided": frozenset(),
    "expired": frozenset(),
}
FINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)
MOVES = ("order", "payer_approved", "authorization", "reauthorization", "capture", "void",
         "refund", "expired")

BLOCK_KEYS = frozenset({"schema", "item_id", "content_sha256", "order_id", "intent",
                        "currency", "amount_minor", "budget_minor", "fee_minor", "payee"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ITEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_PAYPAL_ID = re.compile(r"[A-Z0-9]{1,36}")
_PAYEE = re.compile(r"[A-Z0-9]{1,20}")  # a PayPal merchant id; "" = the app's own account
_AMOUNT = re.compile(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?")


class FundingError(ValueError):
    """A funding step is not allowed: wrong state, a bad amount or a mismatch with PayPal.
    ``code`` is a short machine-readable reason."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


# -- amounts ---------------------------------------------------------------------------


def exponent(currency: str) -> int:
    if currency not in CURRENCIES:
        raise FundingError(f"currency {currency[:8]!r} is not a PayPal currency", "currency")
    return 0 if currency in ZERO_DECIMAL else 2


def format_amount(minor: int, currency: str) -> str:
    """``4800, "GBP"`` → ``"48.00"``; ``4800, "JPY"`` → ``"4800"``."""
    if not isinstance(minor, int) or isinstance(minor, bool) or minor < 0:
        raise FundingError("an amount must be a non-negative integer of minor units", "amount")
    exp = exponent(currency)
    if exp == 0:
        return str(minor)
    return f"{minor // 100}.{minor % 100:02d}"


def parse_amount(value: Any, currency: str) -> int:
    """PayPal's amount string → minor units. Refuses more decimals than the currency has
    (so ``"48.005"`` is an error, never a rounding)."""
    exp = exponent(currency)
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise FundingError("not a PayPal amount", "amount")
    whole, _, frac = value.partition(".")
    if len(frac) > exp:
        raise FundingError(f"{currency} takes {exp} decimal places", "amount")
    return int(whole) * 10 ** exp + (int(frac.ljust(exp, "0")) if exp else 0)


def percent_of(minor: int, pct: Decimal) -> int:
    """``pct`` % of ``minor``, rounded half up to the minor unit."""
    return int((Decimal(minor) * pct / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Quote:
    """The numbers of one funding, computed by code (never taken from a model's text)."""

    currency: str
    daily_budget_minor: int
    days: int
    budget_minor: int
    fee_pct: Decimal
    fee_minor: int

    @property
    def total_minor(self) -> int:
        return self.budget_minor + self.fee_minor

    def amount(self, which: str = "total") -> str:
        minor = {"total": self.total_minor, "budget": self.budget_minor,
                 "fee": self.fee_minor}[which]
        return format_amount(minor, self.currency)


def quote(currency: str, daily_budget_minor: int, days: int, fee_pct: Decimal,
          *, max_daily_minor: int | None = None) -> Quote:
    """Budget = daily × days; fee = ``fee_pct`` % of the budget; ``FundingError`` for a
    currency PayPal lacks, a non-positive budget, too many days, a daily budget above
    ``max_daily_minor`` or a total above ``MAX_TOTAL_MINOR``."""
    exponent(currency)
    for name, value in (("daily budget", daily_budget_minor), ("days", days)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise FundingError(f"the {name} must be a positive whole number", "amount")
    if days > MAX_DAYS:
        raise FundingError(f"a funding covers at most {MAX_DAYS} days", "days")
    if max_daily_minor is not None and daily_budget_minor > max_daily_minor:
        raise FundingError("the daily budget is above the studio's ceiling", "ceiling")
    if not Decimal(0) <= fee_pct <= Decimal(30):
        raise FundingError("the fee must be 0-30 %", "fee")
    budget = daily_budget_minor * days
    q = Quote(currency, daily_budget_minor, days, budget, fee_pct, percent_of(budget, fee_pct))
    if q.total_minor > MAX_TOTAL_MINOR:
        raise FundingError("the total is above the sandbox demo ceiling", "ceiling")
    return q


# -- the signed block --------------------------------------------------------------------


def funding_block(*, item_id: str, content_sha256: str, order_id: str, q: Quote,
                  payee: str = "") -> dict[str, Any]:
    """The facts about the money that the owner's approval signs (``funding_sha256``)."""
    block = {"schema": SCHEMA_ID, "item_id": item_id, "content_sha256": content_sha256,
             "order_id": order_id, "intent": INTENT, "currency": q.currency,
             "amount_minor": q.total_minor, "budget_minor": q.budget_minor,
             "fee_minor": q.fee_minor, "payee": payee}
    problem = funding_block_problem(block)
    if problem is not None:
        raise FundingError(f"funding block field {problem!r} is malformed", "block")
    return block


def funding_block_problem(block: Any) -> str | None:
    """Why ``block`` is not a well-formed funding block, or ``None``. The schema is closed."""
    if not isinstance(block, dict) or set(block) != BLOCK_KEYS:
        return "fields"
    checks: dict[str, bool] = {
        "schema": block["schema"] == SCHEMA_ID,
        "item_id": isinstance(block["item_id"], str) and bool(_ITEM.fullmatch(block["item_id"])),
        "content_sha256": isinstance(block["content_sha256"], str)
        and bool(_SHA256.fullmatch(block["content_sha256"])),
        "order_id": isinstance(block["order_id"], str)
        and bool(_PAYPAL_ID.fullmatch(block["order_id"])),
        "intent": block["intent"] == INTENT,
        "currency": block["currency"] in CURRENCIES,
        "payee": isinstance(block["payee"], str)
        and (block["payee"] == "" or bool(_PAYEE.fullmatch(block["payee"]))),
    }
    for name, ok in checks.items():
        if not ok:
            return name
    for name in ("amount_minor", "budget_minor", "fee_minor"):
        v = block[name]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            return name
    if block["amount_minor"] != block["budget_minor"] + block["fee_minor"] or not block[
            "amount_minor"]:
        return "amount_minor"
    return None


def funding_sha256(block: Mapping[str, Any]) -> str:
    return sha256_hex(canonical(dict(block)))


# -- reconciliation ----------------------------------------------------------------------


@dataclass(frozen=True)
class Statement:
    """What happened to one funding's money, in minor units of ``currency``."""

    currency: str
    captured_minor: int
    spend_minor: int  # Meta's reported spend, as reported
    spend_counted_minor: int  # the spend the owner pays for: at most the budget
    fee_due_minor: int  # the studio fee on the counted spend
    refunded_minor: int
    refundable_minor: int
    overspend_minor: int  # spend beyond the funded budget (the studio's loss, flagged)


def reconcile(*, currency: str, budget_minor: int, fee_minor: int, captured_minor: int,
              spend_minor: int, refunded_minor: int, fee_pct: Decimal) -> Statement:
    """Unspent budget comes back with its share of the fee; the fee due never exceeds the
    fee that was paid; nothing refundable goes below zero."""
    for name, v in (("budget", budget_minor), ("fee", fee_minor),
                    ("captured", captured_minor), ("spend", spend_minor),
                    ("refunded", refunded_minor)):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise FundingError(f"{name} must be a non-negative integer", "amount")
    counted = min(spend_minor, budget_minor)
    fee_due = min(fee_minor, percent_of(counted, fee_pct))
    refundable = max(0, captured_minor - counted - fee_due - refunded_minor)
    return Statement(currency, captured_minor, spend_minor, counted, fee_due, refunded_minor,
                     refundable, max(0, spend_minor - budget_minor))


# -- storage -----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS fundings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    currency TEXT NOT NULL,
    daily_budget_minor INTEGER NOT NULL,
    days INTEGER NOT NULL,
    budget_minor INTEGER NOT NULL,
    fee_pct TEXT NOT NULL,
    fee_minor INTEGER NOT NULL,
    total_minor INTEGER NOT NULL,
    state TEXT NOT NULL,
    order_id TEXT UNIQUE,
    authorization_id TEXT UNIQUE,
    capture_id TEXT UNIQUE,
    authorized_minor INTEGER NOT NULL DEFAULT 0,
    captured_minor INTEGER NOT NULL DEFAULT 0,
    refunded_minor INTEGER NOT NULL DEFAULT 0,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    CHECK (captured_minor <= authorized_minor AND authorized_minor <= total_minor
           AND refunded_minor <= captured_minor)
);
CREATE TABLE IF NOT EXISTS funding_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    funding_id TEXT NOT NULL REFERENCES fundings(id),
    move TEXT NOT NULL,
    paypal_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    amount_minor INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL,
    approval_id TEXT NOT NULL DEFAULT '',
    receipt_id TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS funding_moves_by_funding ON funding_moves (funding_id, id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Join the caller's transaction when there is one (so a state change, its money move
    and its receipt commit together), otherwise open one."""
    if conn.in_transaction:
        yield conn
    else:
        with transaction(conn):
            yield conn


@dataclass(frozen=True)
class Funding:
    id: str
    run_id: str
    item_id: str
    content_sha256: str
    currency: str
    daily_budget_minor: int
    days: int
    budget_minor: int
    fee_pct: Decimal
    fee_minor: int
    total_minor: int
    state: str
    order_id: str | None
    authorization_id: str | None
    capture_id: str | None
    authorized_minor: int
    captured_minor: int
    refunded_minor: int
    created_ts: float
    updated_ts: float

    @property
    def quote(self) -> Quote:
        return Quote(self.currency, self.daily_budget_minor, self.days, self.budget_minor,
                     self.fee_pct, self.fee_minor)

    def block(self, payee: str = "") -> dict[str, Any]:
        if not self.order_id:
            raise FundingError("the funding has no PayPal order yet", "no_order")
        return funding_block(item_id=self.item_id, content_sha256=self.content_sha256,
                             order_id=self.order_id, q=self.quote, payee=payee)


_COLUMNS = ("id", "run_id", "item_id", "content_sha256", "currency", "daily_budget_minor",
            "days", "budget_minor", "fee_pct", "fee_minor", "total_minor", "state",
            "order_id", "authorization_id", "capture_id", "authorized_minor",
            "captured_minor", "refunded_minor", "created_ts", "updated_ts")
_UPDATABLE = frozenset({"order_id", "authorization_id", "capture_id", "authorized_minor",
                        "captured_minor", "refunded_minor"})


def _row(row: Any) -> Funding:
    data = dict(zip(_COLUMNS, row, strict=True))
    data["fee_pct"] = Decimal(data["fee_pct"])
    return Funding(**data)


def get(conn: sqlite3.Connection, funding_id: str) -> Funding:
    row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM fundings WHERE id = ?",  # noqa: S608
                       (funding_id,)).fetchone()
    if row is None:
        raise FundingError("no such funding", "not_found")
    return _row(row)


def by_order(conn: sqlite3.Connection, order_id: str) -> Funding | None:
    row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM fundings "  # noqa: S608
                       "WHERE order_id = ?", (order_id,)).fetchone()
    return None if row is None else _row(row)


def of_item(conn: sqlite3.Connection, item_id: str) -> list[Funding]:
    rows = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM fundings "  # noqa: S608
                        "WHERE item_id = ? ORDER BY created_ts", (item_id,)).fetchall()
    return [_row(r) for r in rows]


def create(conn: sqlite3.Connection, *, run_id: str, item_id: str, content_sha256: str,
           q: Quote, now: float | None = None) -> Funding:
    """A new funding in state ``created`` with no PayPal order yet (``order_id`` is set by
    the step that creates the order, in the same state)."""
    if not _SHA256.fullmatch(content_sha256 or ""):
        raise FundingError("content_sha256 must be a SHA-256 hex digest", "content")
    if not _ITEM.fullmatch(item_id or ""):
        raise FundingError("item_id must be an opaque id", "item")
    ts = time.time() if now is None else now
    fid = new_id("fund_", 8)
    with atomic(conn):
        conn.execute(
            "INSERT INTO fundings (id, run_id, item_id, content_sha256, currency, "
            "daily_budget_minor, days, budget_minor, fee_pct, fee_minor, total_minor, state, "
            "created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
            (fid, run_id, item_id, content_sha256, q.currency, q.daily_budget_minor, q.days,
             q.budget_minor, str(q.fee_pct), q.fee_minor, q.total_minor, ts, ts))
    return get(conn, fid)


def check_move(f: Funding, to: str) -> None:
    if to not in TRANSITIONS.get(f.state, frozenset()):
        raise FundingError(f"a funding in state {f.state!r} cannot become {to!r}",
                           "wrong_state")


def advance(conn: sqlite3.Connection, funding_id: str, to: str, *, expect: str,
            now: float | None = None, **fields: Any) -> Funding:
    """Move ``funding_id`` from ``expect`` to ``to`` and set ``fields``, atomically; a
    concurrent move (the state is no longer ``expect``) or an edge not in ``TRANSITIONS``
    raises ``FundingError``. ``to == expect`` updates fields only (``created`` gaining its
    order id), allowed only where ``TRANSITIONS`` has the self-edge or for ``created``."""
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"not updatable: {sorted(bad)}")
    if to != expect or expect != "created":
        if to not in TRANSITIONS.get(expect, frozenset()):
            raise FundingError(f"a funding in state {expect!r} cannot become {to!r}",
                               "wrong_state")
    ts = time.time() if now is None else now
    sets = "".join(f", {k} = ?" for k in fields)
    with atomic(conn):
        cur = conn.execute(
            f"UPDATE fundings SET state = ?, updated_ts = ?{sets} "  # noqa: S608
            "WHERE id = ? AND state = ?",
            (to, ts, *fields.values(), funding_id, expect))
        if cur.rowcount != 1:
            raise FundingError("the funding changed meanwhile (or does not exist)",
                               "conflict")
    return get(conn, funding_id)


def record_move(conn: sqlite3.Connection, f: Funding, move: str, *, paypal_id: str = "",
                status: str = "", amount_minor: int = 0, approval_id: str = "",
                receipt_id: str = "", now: float | None = None) -> None:
    if move not in MOVES:
        raise ValueError(f"unknown money move {move!r}")
    with atomic(conn):
        conn.execute(
            "INSERT INTO funding_moves (funding_id, move, paypal_id, status, amount_minor, "
            "currency, approval_id, receipt_id, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f.id, move, paypal_id, status, amount_minor, f.currency, approval_id,
             receipt_id, time.time() if now is None else now))


def moves(conn: sqlite3.Connection, funding_id: str) -> list[dict[str, Any]]:
    cols = ("move", "paypal_id", "status", "amount_minor", "currency", "approval_id",
            "receipt_id", "ts")
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM funding_moves "  # noqa: S608
                        "WHERE funding_id = ? ORDER BY id", (funding_id,)).fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]
