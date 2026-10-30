"""Budget advice: how much a day makes sense for this objective, before the owner signs.

The owner types an amount; the studio says what it can buy. Meta's delivery has to leave
the *learning phase* (about ``LEARNING_EVENTS`` optimisation events in 7 days per ad set);
an ad set that cannot reach that keeps optimising on too little data. So:

- ``minimum``: the ad account's own floor (Meta's ``min_daily_budget``, in minor units,
  rounded up to whole units; at least 1).
- ``learning``: ``LEARNING_EVENTS × cost per result ÷ 7`` with a *rough* cost per result
  for the objective (``COST_PER_RESULT``, in GBP, scaled by ``CURRENCY_SCALE``). It is
  labelled an estimate everywhere it is shown; the real cost comes from the campaign's own
  insights after it runs (``studio.agents.analyst``).
- ``suggested``: ``learning`` kept between ``minimum`` and the studio's ceiling, and
  ``fits_learning`` says whether the ceiling or the owner's amount allows it.

With Meta's own ``delivery_estimate`` (``with_estimate``) the advice also says how many
people a day and how many results the suggested and the owner's amount reach, read off
Meta's curve (linear between its points, flat beyond the last).

Nothing here spends or changes a campaign: the advice is shown with the draft and the owner
keeps the amount they choose.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

LEARNING_EVENTS = 50
# rough cost per optimisation result for a small advertiser, in GBP
COST_PER_RESULT = {
    "awareness": 0.01,     # per person reached (≈ £10 CPM)
    "traffic": 0.40,       # per landing-page view / link click
    "engagement": 0.10,    # per engagement
    "leads": 6.00,         # per lead (instant form or website)
    "app_promotion": 2.50, # per install
    "sales": 20.00,        # per purchase
}
# whole units of the account currency per GBP, rounded (an estimate's scale, not a rate)
CURRENCY_SCALE = {"GBP": 1.0, "EUR": 1.15, "USD": 1.3, "PLN": 5.0, "CHF": 1.1,
                  "SEK": 13.0, "NOK": 13.5, "DKK": 8.6, "CZK": 29.0}


def advise(goal: str, currency: str, *, ceiling: int, amount: float | None = None,
           min_daily_minor: int | None = None) -> dict[str, Any]:
    scale = CURRENCY_SCALE.get(currency.upper(), 1.0)
    cpr = COST_PER_RESULT.get(goal, COST_PER_RESULT["traffic"]) * scale
    minimum = max(1, math.ceil((min_daily_minor or 0) / 100))
    learning = max(minimum, math.ceil(LEARNING_EVENTS * cpr / 7))
    suggested = min(max(learning, minimum), max(ceiling, minimum))
    out = {"goal": goal, "currency": currency.upper(), "minimum": minimum,
           "learning": learning, "suggested": suggested,
           "cost_per_result": round(cpr, 2), "events_per_week": LEARNING_EVENTS,
           "fits_learning": learning <= ceiling, "estimate": True}
    if amount is not None:
        out["amount"] = amount
        out["results_per_week"] = math.floor(amount * 7 / cpr) if cpr else 0
        out["amount_fits_learning"] = amount >= learning
    return out


def _at(curve: list[dict[str, int]], spend_minor: int, key: str) -> int:
    if not curve:
        return 0
    if spend_minor <= curve[0]["spend_minor"]:
        first = curve[0]
        return int(first[key] * spend_minor / first["spend_minor"]) if first["spend_minor"] else 0
    for a, b in itertools.pairwise(curve):
        if a["spend_minor"] <= spend_minor <= b["spend_minor"]:
            span = b["spend_minor"] - a["spend_minor"] or 1
            return int(a[key] + (b[key] - a[key]) * (spend_minor - a["spend_minor"]) / span)
    return curve[-1][key]


def with_estimate(advice: dict[str, Any], estimate: dict[str, Any]) -> dict[str, Any]:
    """``advice`` plus Meta's numbers for the suggested amount (and the owner's)."""
    curve = estimate.get("curve") or []
    if not curve:
        return advice
    out = dict(advice)
    out["meta"] = {
        "audience_lower": estimate.get("audience_lower", 0),
        "audience_upper": estimate.get("audience_upper", 0),
        "suggested_reach": _at(curve, advice["suggested"] * 100, "reach"),
        "suggested_results": _at(curve, advice["suggested"] * 100, "results"),
    }
    if "results_per_week" in advice and advice.get("amount") is not None:
        amount_minor = int(advice["amount"] * 100)
        out["meta"]["amount_reach"] = _at(curve, amount_minor, "reach")
        out["meta"]["amount_results"] = _at(curve, amount_minor, "results")
    return out
