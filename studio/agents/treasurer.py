"""Treasurer: Muse Spark proposes a budget split and explains a funding's reconciliation in
plain words. Code owns every number.

- **Inputs are code's numbers**: the total to split, the campaigns (opaque item ids), the
  per-campaign cap and, when there is one, the ``funding.Statement`` (captured, spend,
  fee due, refunded, refundable). No PayPal id, credential, vault reference or payer data
  goes into the prompt, and the call has **no tools**: the model cannot reach PayPal or
  any write path. Its answer is a strict ``Treasury`` JSON object.
- **Code recomputes every amount** (``check``): the split must name exactly the given
  campaigns, in whole minor units, each within the cap, adding up to the total; the
  figures the explanation relies on must equal code's statement; and every amount written
  in the explanation must be one of code's amounts. Anything else is refused: the
  model's split is replaced by code's even split and its explanation by code's own
  sentence, and the reasons are returned (``refused``).
- **Nothing moves**: the advice is shown to the owner. A refund still needs its own signed
  ``funding.refund`` approval, a budget its own signature.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from studio.agents import prompts
from studio.funding import Statement, format_amount
from studio.muse.client import MuseClient, MuseError
from studio.muse.structured import strict_schema

AGENT = "treasurer"
MODEL = "muse-spark-1.3"
MAX_OUTPUT_TOKENS = 2_000
MAX_CAMPAIGNS = 10
_AMOUNT = re.compile(r"(?<![\w.,])(\d{1,9}(?:[.,]\d{2}))(?![\w.,]?\d)")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SplitLine(_Closed):
    campaign: str
    amount_minor: int
    why: str


class Figures(_Closed):
    captured_minor: int
    spend_counted_minor: int
    fee_due_minor: int
    refunded_minor: int
    refundable_minor: int


class Treasury(_Closed):
    split: list[SplitLine]
    figures: Figures
    explanation: str


@dataclass
class Advice:
    """What the owner sees: code-checked split and explanation (``source`` says whose)."""

    currency: str
    split: list[dict[str, Any]]
    explanation: str
    source: str  # "model" when the model's answer passed every check, else "code"
    refused: list[str] = field(default_factory=list)
    model_receipt: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"currency": self.currency, "split": self.split,
                "explanation": self.explanation, "source": self.source,
                "refused": self.refused, "model_receipt": self.model_receipt}


def even_split(total_minor: int, campaigns: list[str]) -> list[dict[str, Any]]:
    """Code's own split: equal parts, the remainder to the first campaigns."""
    n = len(campaigns)
    base, rest = divmod(total_minor, n) if n else (0, 0)
    return [{"campaign": c, "amount_minor": base + (1 if i < rest else 0),
             "why": "even split (code)"} for i, c in enumerate(campaigns)]


def figures_of(st: Statement | None) -> dict[str, int]:
    if st is None:
        return {k: 0 for k in Figures.model_fields}
    return {"captured_minor": st.captured_minor,
            "spend_counted_minor": st.spend_counted_minor,
            "fee_due_minor": st.fee_due_minor, "refunded_minor": st.refunded_minor,
            "refundable_minor": st.refundable_minor}


def plain(st: Statement | None, currency: str) -> str:
    """Code's own explanation (used whenever the model's is refused)."""
    if st is None:
        return "Nothing has been captured yet, so there is nothing to reconcile."
    m = {k: format_amount(v, currency) for k, v in figures_of(st).items()}
    return (f"PayPal took {m['captured_minor']} {currency}. Meta's spend that counts is "
            f"{m['spend_counted_minor']} {currency} and the studio fee on it is "
            f"{m['fee_due_minor']} {currency}. {m['refunded_minor']} {currency} was "
            f"refunded already, so {m['refundable_minor']} {currency} can still come back "
            "to you with a refund you sign.")


def facts(*, currency: str, total_minor: int, campaigns: list[str], max_each_minor: int,
          statement: Statement | None, fee_pct: Decimal) -> dict[str, Any]:
    """The prompt's whole content: code's numbers, nothing else."""
    return {"currency": currency, "minor_units_per_unit": 1 if format_amount(
                1, currency) == "1" else 100,
            "split_total_minor": total_minor, "campaigns": campaigns,
            "max_per_campaign_minor": max_each_minor, "fee_pct": str(fee_pct),
            "statement": figures_of(statement) if statement is not None else None,
            "spend_reported_minor": statement.spend_minor if statement is not None else 0}


def check(t: Treasury, *, currency: str, total_minor: int, campaigns: list[str],
          max_each_minor: int, statement: Statement | None) -> list[str]:
    """Every reason the model's answer cannot be shown as it is; empty = it can."""
    reasons: list[str] = []
    named = [line.campaign for line in t.split]
    if sorted(named) != sorted(campaigns):
        reasons.append("split_campaigns")
    amounts = [line.amount_minor for line in t.split]
    if any(a < 0 or a > max_each_minor for a in amounts):
        reasons.append("split_cap")
    if sum(amounts) != total_minor:
        reasons.append("split_total")
    want = figures_of(statement)
    if t.figures.model_dump() != want:
        reasons.append("figures")
    known = {format_amount(v, currency) for v in want.values()}
    known |= {format_amount(a, currency) for a in amounts}
    known |= {format_amount(total_minor, currency)}
    if statement is not None:
        known.add(format_amount(statement.spend_minor, currency))
    written = {m.replace(",", ".") for m in _AMOUNT.findall(t.explanation)}
    if written - known:
        reasons.append("explanation_amounts")
    if not t.explanation.strip() or len(t.explanation) > 1500:
        reasons.append("explanation")
    return reasons


def advise(client: MuseClient, *, run_id: str, currency: str, total_minor: int,
           campaigns: list[str], max_each_minor: int, statement: Statement | None,
           fee_pct: Decimal) -> Advice:
    """One strict-JSON call (no tools), then code's checks. A model error or an answer
    that fails a check gives code's split and explanation with the reasons."""
    campaigns = list(campaigns)[:MAX_CAMPAIGNS]
    fallback = Advice(currency, even_split(total_minor, campaigns), plain(statement, currency),
                      "code")
    content = json.dumps(facts(currency=currency, total_minor=total_minor,
                               campaigns=campaigns, max_each_minor=max_each_minor,
                               statement=statement, fee_pct=fee_pct), sort_keys=True)
    try:
        result = client.respond(
            MODEL,
            [{"role": "system", "content": prompts.system(AGENT)},
             {"role": "user", "content": [{"type": "input_text", "text": content}]}],
            run_id=run_id, agent=AGENT, reasoning={"effort": "low"},
            max_output_tokens=MAX_OUTPUT_TOKENS,
            text_format={"type": "json_schema", "name": "Treasury", "strict": True,
                         "schema": strict_schema(Treasury)},
            tier="standard", tier_reason="treasurer: code-computed amounts only")
    except MuseError as exc:
        fallback.refused = ["model_error:" + (exc.code or "error")]
        return fallback
    try:
        t = Treasury.model_validate_json(result.text)
    except ValueError:
        fallback.refused, fallback.model_receipt = ["invalid_output"], result.receipt_id
        return fallback
    reasons = check(t, currency=currency, total_minor=total_minor, campaigns=campaigns,
                    max_each_minor=max_each_minor, statement=statement)
    split = [line.model_dump() for line in t.split]
    explanation = t.explanation.strip()
    if any(r.startswith("split") for r in reasons):
        split = fallback.split
    if any(r in ("figures", "explanation_amounts", "explanation") for r in reasons):
        explanation = fallback.explanation
    return Advice(currency, split, explanation, "model" if not reasons else "code", reasons,
                  result.receipt_id)
