"""Ad account currency: budgets are in the account's currency, sent in its minor units.

Meta gives every currency an offset (how the Marketing API counts its subdivisions):
1 for CLP, COP, CRC, HUF, ISK, IDR, JPY, KRW, PYG, TWD and VND, 100 for every other
currency (USD, EUR, GBP, PLN, …). https://developers.facebook.com/docs/marketing-api/currencies
(read 2026-10-28). The account's own currency comes from ``GET /act_{id}?fields=currency``
(``Adapter.account``); a brief in another currency is a field error, never converted.
"""

from __future__ import annotations

import math
import re

OFFSET_ONE = frozenset({"CLP", "COP", "CRC", "HUF", "ISK", "IDR", "JPY", "KRW", "PYG", "TWD",
                        "VND"})
CODE = re.compile(r"[A-Z]{3}")


def offset(currency: str) -> int:
    if not CODE.fullmatch(currency or ""):
        raise ValueError(f"not an ISO 4217 code: {currency!r}")
    return 1 if currency in OFFSET_ONE else 100


def to_minor(amount: float, currency: str) -> int:
    """Whole account-currency units → the API's minor units (rounded up)."""
    return int(math.ceil(round(amount * offset(currency), 6)))


def from_minor(minor: int, currency: str) -> float:
    return minor / offset(currency)
