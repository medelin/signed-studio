"""The plan prices shown to a buyer (``/pricing``, the account's billing section, the
sign-in page).

- **Plans** (``PLANS``): ``monthly`` (``STRIPE_PRICE_ID``), ``3m`` / ``6m`` / ``12m``
  (``STRIPE_PRICE_3M`` / ``_6M`` / ``_12M``: billed upfront every 3, 6 or 12 months) and
  ``once`` (``STRIPE_PRICE_ONCE``: one payment for ``ONCE_DAYS`` days, no renewal). A plan
  whose price id is unset is not offered.
- **From Stripe**: ``GET /v1/prices/{id}`` once per plan, kept for ``TTL_S`` (12 h);
  ``unit_amount``, ``currency``, ``recurring`` and ``currency_options``. A price is shown
  only when it matches its plan (``interval=month`` with the plan's ``interval_count``, or
  ``one_time`` for ``once``): anything else would make the page's wording untrue, so it is
  treated as unknown.
- **Fallback**: billing off, Stripe unreachable (retried after ``RETRY_S``) or an unusable
  price → for the monthly plan ``STUDIO_PRICE_DISPLAY`` (the amount, e.g. ``£9``), else no
  amount at all.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from studio.billing import ONCE_DAYS, PLAN_MONTHS, PLANS, BillingError, StripeApi

__all__ = ["ONCE_DAYS", "PLANS", "PLAN_MONTHS", "Price", "PriceCache", "money", "parse"]

log = logging.getLogger(__name__)

TTL_S = 12 * 3600
RETRY_S = 600
SYMBOLS = {"gbp": "£", "usd": "$", "eur": "€", "pln": "zł"}
ZERO_DECIMAL = frozenset({"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg",
                          "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"})
# "£9 / month", "£9 per month", "9 zł / mies." → the amount alone (the pages add "a month")
_PER_MONTH = re.compile(r"\s*(?:/|per|a|na)\s*(?:month|mo|miesiąc|mies\.?|m-c)\s*$", re.I)


@dataclass(frozen=True)
class Price:
    amount: int  # minor units, for the whole period
    currency: str  # ISO 4217, lower case
    months: int  # the period: 1, 3, 6, 12 (``once``: 1, ONCE_DAYS days)
    recurring: bool = True
    options: dict[str, int] = field(default_factory=dict)  # other currencies, minor units


def money(amount: int, currency: str, lang: str) -> str:
    """``900, "gbp"`` → ``£9`` (EN) / ``9 £`` (PL); unknown currencies by their code."""
    cur = currency.lower()
    value = amount if cur in ZERO_DECIMAL else amount / 100
    number = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    if lang == "pl":
        number = number.replace(",", " ").replace(".", ",")
        return f"{number} {SYMBOLS.get(cur, cur.upper())}"
    symbol = SYMBOLS.get(cur)
    if symbol and cur != "pln":
        return f"{symbol}{number}"
    return f"{number} {symbol or cur.upper()}"


def parse(data: Mapping[str, Any], plan: str = "monthly") -> Price | None:
    """The price when it is what ``plan`` promises, else None."""
    amount = data.get("unit_amount")
    currency = str(data.get("currency") or "")
    if not isinstance(amount, int) or amount <= 0 or not re.fullmatch(r"[a-z]{3}", currency):
        return None
    recurring = data.get("recurring") or {}
    if plan == "once":
        if recurring or data.get("type", "one_time") != "one_time":
            return None
    elif (recurring.get("interval") != "month"
          or recurring.get("interval_count", 1) != PLAN_MONTHS.get(plan)):
        return None
    options: dict[str, int] = {}
    for cur, opt in (data.get("currency_options") or {}).items():
        unit = (opt or {}).get("unit_amount")
        if cur != currency and re.fullmatch(r"[a-z]{3}", str(cur)) and isinstance(unit, int) \
                and unit > 0:
            options[str(cur)] = unit
    return Price(amount, currency, PLAN_MONTHS[plan], plan != "once", options)


def fallback_amount(display: str) -> str:
    return _PER_MONTH.sub("", display).strip()


class PriceCache:
    """One Stripe read per plan and ``TTL_S``; thread-safe (pages render in the pool)."""

    def __init__(self, stripe_of: Callable[[], StripeApi | None], fallback: str = "", *,
                 clock: Callable[[], float] = time.time) -> None:
        self._stripe_of = stripe_of
        self._fallback = fallback_amount(fallback)
        self._clock = clock
        self._lock = threading.Lock()
        self._prices: dict[str, Price | None] = {}
        self._until: dict[str, float] = {}
        self._fetching: set[str] = set()

    def plans(self) -> list[str]:
        """The plans on offer (a price id is configured), in display order."""
        api = self._stripe_of()
        return [p for p in PLANS if api is not None and api.config.plans.get(p)]

    def get(self, plan: str = "monthly", *, fetch: bool = True) -> Price | None:
        """The plan's price; ``fetch=False`` never calls Stripe (for async handlers).

        Readers never wait on the network: a fresh cached value is returned without the
        lock, and the Stripe call runs outside it; only one caller per plan fetches
        (``_fetching``), the others get the cached value meanwhile."""
        now = self._clock()
        if not fetch or now < self._until.get(plan, 0.0):
            return self._prices.get(plan)
        with self._lock:
            if plan in self._fetching:
                return self._prices.get(plan)
            self._fetching.add(plan)
        try:
            api = self._stripe_of()
            price_id = api.config.plans.get(plan, "") if api is not None else ""
            if api is None or not price_id:
                return None
            try:
                price = parse(api.price(price_id), plan)
                if price is None:
                    log.warning("pricing: the Stripe price of plan %s does not match it", plan)
                until = now + TTL_S
            except BillingError as exc:
                log.warning("pricing: %s", exc.code)
                price, until = None, now + RETRY_S
            with self._lock:
                self._prices[plan], self._until[plan] = price, until
            return price
        finally:
            with self._lock:
                self._fetching.discard(plan)

    def warm(self) -> None:
        for plan in self.plans():
            self.get(plan)

    def amount(self, lang: str, plan: str = "monthly", *, fetch: bool = True) -> str:
        """``£9``, the fallback (monthly only), or "" when no amount may be shown."""
        price = self.get(plan, fetch=fetch)
        if price:
            return money(price.amount, price.currency, lang)
        return self._fallback if plan == "monthly" else ""

    def per_month(self, lang: str, plan: str, *, fetch: bool = True) -> str:
        price = self.get(plan, fetch=fetch)
        if not price or price.months <= 1:
            return ""
        return money(round(price.amount / price.months), price.currency, lang)

    def free_months(self, plan: str, *, fetch: bool = True) -> int:
        """Whole months saved against paying monthly, from the two Stripe amounts (same
        currency); 0 when unknown or none."""
        price, monthly = self.get(plan, fetch=fetch), self.get("monthly", fetch=fetch)
        if not price or not monthly or price.currency != monthly.currency or \
                price.months <= 1 or not price.recurring:
            return 0
        saved = monthly.amount * price.months - price.amount
        return max(0, round(saved / monthly.amount))

    def others(self, lang: str, plan: str = "monthly", *, fetch: bool = True) -> list[str]:
        """The same price in the other currencies Stripe offers at checkout."""
        price = self.get(plan, fetch=fetch)
        return [money(a, c, lang) for c, a in sorted(price.options.items())] if price else []
