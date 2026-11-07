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
  ``captured`` (money taken) → ``partially_refunded`` / ``refunded``. A charge of the
  owner's saved PayPal under a signed mandate goes ``created`` → ``authorized`` in one
  step (no buyer is present to approve). ``voided`` ends an
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
from contextlib import AbstractContextManager, contextmanager, nullcontext
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
          "refunded", "voided", "expired", "failed")
TRANSITIONS: dict[str, frozenset[str]] = {
    # created → authorized: a charge of the owner's saved PayPal under a signed mandate
    # (Vault v3), where PayPal holds the money when the order is made; only
    # ``Money.charge_vault`` takes this edge, a buyer's order needs ``payer_approved``
    "created": frozenset({"payer_approved", "authorized", "expired"}),
    # payer_approved → failed: PayPal definitively refused the payment at authorize
    # (the buyer's funding source was declined); a fresh order may then be made
    "payer_approved": frozenset({"authorized", "expired", "failed"}),
    "authorized": frozenset({"captured", "voided"}),
    "captured": frozenset({"partially_refunded", "refunded"}),
    "partially_refunded": frozenset({"partially_refunded", "refunded"}),
    "refunded": frozenset(),
    "voided": frozenset(),
    "expired": frozenset(),
    "failed": frozenset(),
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


def display_amount(minor: Any, currency: Any) -> str:
    """Template filter: minor units → display string through ``format_amount`` (zero-decimal
    aware). A value it cannot format is shown as it is, never as a guess."""
    try:
        return format_amount(minor, str(currency))
    except FundingError:
        return str(minor)


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
-- a PayPal object is recorded once: a replayed answer cannot be counted twice
CREATE UNIQUE INDEX IF NOT EXISTS funding_moves_once ON funding_moves (move, paypal_id)
    WHERE paypal_id != '';
-- one claim per authorising key (an approval id): a signed approval moves money once.
-- A pending refund claim reserves its amount, so concurrent refunds cannot exceed the
-- capture between the check and PayPal's answer. Claims are never deleted: done and
-- refused spend the key; released (the request never left the studio) can be taken
-- again only for exactly the same funding, step and amount.
CREATE TABLE IF NOT EXISTS funding_claims (
    key TEXT PRIMARY KEY,
    funding_id TEXT NOT NULL REFERENCES fundings(id),
    step TEXT NOT NULL,
    amount_minor INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('pending', 'done', 'refused', 'released')),
    paypal_id TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);
"""


# States before authorization: an order made (or about to be) but not yet holding money.
OPEN_STATES = ("created", "payer_approved")
_OPEN_SQL = ", ".join(f"'{s}'" for s in OPEN_STATES)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # One open funding per (item, ad hash). A database from before this index may hold
    # several: keep the newest, end the older ones as ``expired`` (an order never
    # authorized), then create the index, which can no longer fail.
    conn.execute(
        f"UPDATE fundings SET state = 'expired', updated_ts = ? WHERE state IN ({_OPEN_SQL}) "  # noqa: S608
        "AND rowid NOT IN (SELECT MAX(rowid) FROM fundings "
        f"WHERE state IN ({_OPEN_SQL}) GROUP BY item_id, content_sha256)", (time.time(),))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS fundings_one_open ON fundings "  # noqa: S608
                 f"(item_id, content_sha256) WHERE state IN ({_OPEN_SQL})")


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Join the caller's transaction when there is one (so a state change, its money move
    and its receipt commit together), otherwise open one. The connection's lock (an RLock,
    see ``transaction``) is taken first: routes run on worker threads, and without it a
    thread could "join" a transaction another thread has open."""
    lock: AbstractContextManager[Any] = getattr(conn, "lock", None) or nullcontext()
    with lock:
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


def _same_quote(f: Funding, q: Quote) -> bool:
    """Whether the open funding ``f`` is the amount ``q`` asks for (every money field)."""
    return (f.currency == q.currency and f.daily_budget_minor == q.daily_budget_minor
            and f.days == q.days and f.budget_minor == q.budget_minor
            and f.fee_minor == q.fee_minor and f.total_minor == q.total_minor)


def create(conn: sqlite3.Connection, *, run_id: str, item_id: str, content_sha256: str,
           q: Quote, now: float | None = None) -> Funding:
    """A new funding in state ``created`` with no PayPal order yet (``order_id`` is set by
    the step that creates the order, in the same state). At most one funding per
    ``(item_id, content_sha256)`` is open (``fundings_one_open``): when one exists, or a
    racing request inserts it first, that one is returned instead of a second."""
    if not _SHA256.fullmatch(content_sha256 or ""):
        raise FundingError("content_sha256 must be a SHA-256 hex digest", "content")
    if not _ITEM.fullmatch(item_id or ""):
        raise FundingError("item_id must be an opaque id", "item")
    ts = time.time() if now is None else now
    fid = new_id("fund_", 8)
    open_sql = (f"SELECT {', '.join(_COLUMNS)} FROM fundings WHERE item_id = ? "  # noqa: S608
                f"AND content_sha256 = ? AND state IN ({_OPEN_SQL})")
    with atomic(conn):
        existing = conn.execute(open_sql, (item_id, content_sha256)).fetchone()
        if existing is not None:
            found = _row(existing)
            if _same_quote(found, q):
                return found
            # the open funding was quoted differently (a fee or budget setting changed):
            # an order-less row is retired and replaced; one with a PayPal order is never
            # silently reused for another amount
            if found.order_id:
                raise FundingError("the open funding's amount differs from the quote",
                                   "quote_changed")
            conn.execute("UPDATE fundings SET state = 'expired', updated_ts = ? WHERE id = ? "
                         "AND state = 'created' AND order_id IS NULL", (ts, found.id))
        try:
            conn.execute(
                "INSERT INTO fundings (id, run_id, item_id, content_sha256, currency, "
                "daily_budget_minor, days, budget_minor, fee_pct, fee_minor, total_minor, "
                "state, created_ts, updated_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
                (fid, run_id, item_id, content_sha256, q.currency, q.daily_budget_minor,
                 q.days, q.budget_minor, str(q.fee_pct), q.fee_minor, q.total_minor, ts, ts))
        except sqlite3.IntegrityError:  # a racing request on another connection won
            existing = conn.execute(open_sql, (item_id, content_sha256)).fetchone()
            if existing is None:
                raise
            return _row(existing)
    return get(conn, fid)


def check_move(f: Funding, to: str) -> None:
    if to not in TRANSITIONS.get(f.state, frozenset()):
        raise FundingError(f"a funding in state {f.state!r} cannot become {to!r}",
                           "wrong_state")


def advance(conn: sqlite3.Connection, funding_id: str, to: str, *, expect: str,
            now: float | None = None, add: Mapping[str, int] | None = None,
            guard: Mapping[str, Any] | None = None, **fields: Any) -> Funding:
    """Move ``funding_id`` from ``expect`` to ``to`` and set ``fields``, atomically; a
    concurrent move (the state is no longer ``expect``) or an edge not in ``TRANSITIONS``
    raises ``FundingError``. ``to == expect`` updates fields only (``created`` gaining its
    order id), allowed only where ``TRANSITIONS`` has the self-edge or for ``created``.

    ``add`` increments columns relative to their stored value (``refunded_minor =
    refunded_minor + ?``) and ``guard`` names column values the row must still have (a
    version check against what was read before the PayPal call); a guard that no longer
    holds is a ``conflict``. The table's CHECK constraint re-checks captured ≤ authorized ≤
    total and refunded ≤ captured inside the same statement."""
    add, guard = dict(add or {}), dict(guard or {})
    bad = (set(fields) | set(add) | set(guard)) - _UPDATABLE
    if bad:
        raise ValueError(f"not updatable: {sorted(bad)}")
    if to != expect or expect != "created":
        if to not in TRANSITIONS.get(expect, frozenset()):
            raise FundingError(f"a funding in state {expect!r} cannot become {to!r}",
                               "wrong_state")
    ts = time.time() if now is None else now
    sets = "".join(f", {k} = ?" for k in fields) + "".join(f", {k} = {k} + ?" for k in add)
    where = "".join(f" AND {k} IS ?" for k in guard)
    with atomic(conn):
        cur = conn.execute(
            f"UPDATE fundings SET state = ?, updated_ts = ?{sets} "  # noqa: S608
            f"WHERE id = ? AND state = ?{where}",
            (to, ts, *fields.values(), *add.values(), funding_id, expect, *guard.values()))
        if cur.rowcount != 1:
            raise FundingError("the funding changed meanwhile (or does not exist)",
                               "conflict")
    return get(conn, funding_id)


def claim(conn: sqlite3.Connection, key: str, *, funding_id: str, step: str,
          amount_minor: int = 0, now: float | None = None) -> bool:
    """Reserve the money step ``step`` of ``funding_id`` for the authorising ``key`` (an
    approval id) before PayPal is called. ``True`` for a new claim (or a released one taken
    again with the same parameters); ``False`` for a retry of the same pending step (same
    funding, step and amount: PayPal gets the same request id and answers with its first
    result). ``FundingError`` ``approval_used`` when the key is done or refused (a fresh
    signed approval is needed) or was claimed with other parameters, and ``refund_amount``
    when a refund would exceed the capture minus what is refunded and what pending refunds
    reserve."""
    ts = time.time() if now is None else now
    with atomic(conn):
        row = conn.execute("SELECT funding_id, step, amount_minor, status FROM "
                           "funding_claims WHERE key = ?", (key,)).fetchone()
        if row is not None:
            if row[3] in ("done", "refused") or tuple(row[:3]) != (funding_id, step,
                                                                   amount_minor):
                raise FundingError("this approval has already authorised a money step",
                                   "approval_used")
            if row[3] == "pending":
                return False
        if step == "refund":
            f = get(conn, funding_id)
            reserved = conn.execute(
                "SELECT COALESCE(SUM(amount_minor), 0) FROM funding_claims WHERE "
                "funding_id = ? AND step = 'refund' AND status = 'pending'",
                (funding_id,)).fetchone()[0]
            if not 0 < amount_minor <= f.captured_minor - f.refunded_minor - reserved:
                raise FundingError("the refund is more than what is left of the capture "
                                   "(counting refunds in flight)", "refund_amount")
        if row is not None:  # released, same parameters: take it again
            conn.execute("UPDATE funding_claims SET status = 'pending', updated_ts = ? "
                         "WHERE key = ? AND status = 'released'", (ts, key))
            return True
        conn.execute("INSERT INTO funding_claims (key, funding_id, step, amount_minor, "
                     "status, created_ts, updated_ts) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                     (key, funding_id, step, amount_minor, ts, ts))
    return True


def settle_claim(conn: sqlite3.Connection, key: str, paypal_id: str,
                 now: float | None = None) -> None:
    """Mark the claim done (inside the step's commit transaction)."""
    with atomic(conn):
        cur = conn.execute("UPDATE funding_claims SET status = 'done', paypal_id = ?, "
                           "updated_ts = ? WHERE key = ? AND status = 'pending'",
                           (paypal_id, time.time() if now is None else now, key))
        if cur.rowcount != 1:
            raise FundingError("the money step's claim is gone or already settled",
                               "conflict")


def end_claim(conn: sqlite3.Connection, key: str, status: str,
              now: float | None = None) -> None:
    """Close a pending claim without money moving: ``refused`` (PayPal answered with a
    definite refusal; the key stays spent) or ``released`` (the request never left the
    studio; the same parameters may claim it again). The row is never deleted."""
    if status not in ("refused", "released"):
        raise ValueError(status)
    with atomic(conn):
        conn.execute("UPDATE funding_claims SET status = ?, updated_ts = ? WHERE key = ? "
                     "AND status = 'pending'", (status, time.time() if now is None else now,
                                                key))


def claims(conn: sqlite3.Connection, funding_id: str) -> list[dict[str, Any]]:
    cols = ("key", "step", "amount_minor", "status", "paypal_id")
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM funding_claims "  # noqa: S608
                        "WHERE funding_id = ? ORDER BY created_ts", (funding_id,)).fetchall()
    return [dict(zip(cols, r, strict=True)) for r in rows]


def record_move(conn: sqlite3.Connection, f: Funding, move: str, *, paypal_id: str = "",
                status: str = "", amount_minor: int = 0, approval_id: str = "",
                receipt_id: str = "", now: float | None = None) -> None:
    if move not in MOVES:
        raise ValueError(f"unknown money move {move!r}")
    with atomic(conn):
        if paypal_id and conn.execute("SELECT 1 FROM funding_moves WHERE move = ? AND "
                                      "paypal_id = ?", (move, paypal_id)).fetchone():
            raise FundingError(f"PayPal {move} {paypal_id} is already recorded (a replayed "
                               "answer)", "replayed")
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
