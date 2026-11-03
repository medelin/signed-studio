"""PayPal (sandbox only): configuration and the adapter choice.

- ``PAYPAL_ADAPTER``: unset = PayPal off; ``mock`` = the in-process fake PayPal
  (``studio.paypal.mock``, no network, no credentials); ``rest`` = the PayPal REST API
  through ``studio.paypal.client`` with ``PAYPAL_CLIENT_ID`` and ``PAYPAL_CLIENT_SECRET``.
- ``PAYPAL_ENV`` must be ``sandbox`` (or unset). Any other value stops the app at start:
  the base URL is the constant ``SANDBOX_BASE`` and there is no live option to choose.
- ``STUDIO_FEE_PCT``: the studio fee, a percentage of the ad budget added to the PayPal
  order as its own item (default ``DEFAULT_FEE_PCT`` = 5 %, allowed 0-30).
- ``PAYPAL_WEBHOOK_ID``: the id PayPal gave the registered webhook (needed to verify its
  signatures; optional until webhooks are on).

Secrets stay out of ``repr`` so they never reach a log or a traceback.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
ADAPTERS = frozenset({"", "mock", "rest"})
DEFAULT_FEE_PCT = Decimal(5)
MAX_FEE_PCT = Decimal(30)
_FEE = re.compile(r"\d{1,2}(\.\d{1,2})?")
_WEBHOOK_ID = re.compile(r"[A-Z0-9]{1,64}")


class SandboxOnly(ValueError):
    """A live PayPal environment was configured: this build talks to the sandbox only."""


@dataclass(frozen=True)
class PayPalConfig:
    adapter: str = ""
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    webhook_id: str = ""
    fee_pct: Decimal = DEFAULT_FEE_PCT

    @property
    def enabled(self) -> bool:
        return bool(self.adapter)

    @property
    def fee_pct_text(self) -> str:
        """``5`` or ``2.5``: the percentage as people read it."""
        return format(self.fee_pct.normalize(), "f")

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> PayPalConfig:
        """Raise ``SandboxOnly`` for a non-sandbox ``PAYPAL_ENV`` and ``ValueError`` for any
        other bad value; both name the variable."""
        env = e.get("PAYPAL_ENV", "").strip().lower()
        if env not in ("", "sandbox"):
            raise SandboxOnly(f"PAYPAL_ENV={env!r}: this build talks to the PayPal sandbox "
                              f"only ({SANDBOX_BASE}); there is no live mode")
        adapter = e.get("PAYPAL_ADAPTER", "").strip().lower()
        if adapter not in ADAPTERS:
            raise ValueError("PAYPAL_ADAPTER must be mock or rest (or unset: PayPal off)")
        client_id = e.get("PAYPAL_CLIENT_ID", "").strip()
        secret = e.get("PAYPAL_CLIENT_SECRET", "").strip()
        if adapter == "rest" and not (client_id and secret):
            raise ValueError("PAYPAL_ADAPTER=rest needs PAYPAL_CLIENT_ID and "
                             "PAYPAL_CLIENT_SECRET (a sandbox REST app's credentials)")
        webhook_id = e.get("PAYPAL_WEBHOOK_ID", "").strip()
        if webhook_id and not _WEBHOOK_ID.fullmatch(webhook_id):
            raise ValueError("PAYPAL_WEBHOOK_ID must be the webhook id PayPal returned "
                             "(capital letters and digits)")
        return cls(adapter=adapter, client_id=client_id, client_secret=secret,
                   webhook_id=webhook_id, fee_pct=fee_pct_of(e.get("STUDIO_FEE_PCT", "")))


def fee_pct_of(raw: str) -> Decimal:
    """``STUDIO_FEE_PCT`` as a ``Decimal`` (default 5); ``ValueError`` outside 0-30 or with
    more than two decimal places."""
    raw = raw.strip()
    if not raw:
        return DEFAULT_FEE_PCT
    try:
        pct = Decimal(raw) if _FEE.fullmatch(raw) else None
    except InvalidOperation:
        pct = None
    if pct is None or not Decimal(0) <= pct <= MAX_FEE_PCT:
        raise ValueError("STUDIO_FEE_PCT must be a percentage from 0 to 30, like 5 or 2.5")
    return pct
