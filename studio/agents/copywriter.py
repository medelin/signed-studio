"""Copywriter: three ad copy variants for one plan angle (strict JSON ``CopyPack``).

Runs on the Contributor tier when the input has no PII, otherwise on Standard; the choice
and its reason are made in code (``pii.choose_tier``) and recorded in the receipt.
"""

from __future__ import annotations

import re
import unicodedata

from studio.agents import prompts
from studio.agents.schemas import Angle, CopyPack, CopyVariant
from studio.muse.client import MuseClient, MuseResult
from studio.muse.structured import structured
from studio.pii import choose_tier

AGENT = "copywriter"
FACT = "[FACT]"
# Look-alikes NFKC leaves alone (Cyrillic, Greek, Latin variants) folded to F, A, C, T.
_CONFUSABLES = str.maketrans({
    "\u0410": "A", "\u0430": "a", "\u0391": "A", "\u03b1": "a",  # Cyrillic/Greek A
    "\u0421": "C", "\u0441": "c", "\u03f9": "C", "\u03f2": "c", "\u216d": "C",  # C
    "\u0422": "T", "\u0442": "t", "\u03a4": "T", "\u03c4": "t",  # Cyrillic/Greek T
    "\u03dc": "F", "\u03dd": "f", "\u0192": "f", "\u0191": "F", "\u1d6e": "f",  # F
    "\u1d00": "a", "\u1d04": "c", "\u1d1b": "t", "\ua730": "f",  # small capitals
})
_FACT = re.compile(r"[\[({<\u27e6\u3010\u3014]\W*f\W*a\W*c\W*t\W*[\])}>\u27e7\u3011\u3015]",
                   re.IGNORECASE)


def normalise(text: str) -> str:
    """NFKC, Unicode format characters (category Cf: zero-width, soft hyphen, bidi) removed,
    confusable letters folded to Latin."""
    folded = unicodedata.normalize("NFKC", text.translate(_CONFUSABLES))
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return folded.translate(_CONFUSABLES)


def count_unfilled_facts(v: CopyVariant) -> int:
    return sum(len(_FACT.findall(normalise(t))) for t in (v.primary_text, v.headline,
                                                           v.description))


def has_unfilled_fact(v: CopyVariant) -> bool:
    """True if any signed field still holds a ``[FACT]`` placeholder, look-alikes included."""
    return count_unfilled_facts(v) > 0


def _user_message(angle: Angle, persona: str, brief: str) -> str:
    lines = [f"Angle: {angle.name}", f"Hook: {angle.hook}", f"Persona: {persona}"]
    if brief:
        lines.append(f"Brief: {brief}")
    return "\n".join(lines)


def flag_unfilled_facts(pack: CopyPack) -> CopyPack:
    """Every variant still holding ``[FACT]`` gets a risk line (if the model did not add one)."""
    risks = list(pack.risks)
    for i, v in enumerate(pack.variants, start=1):
        if has_unfilled_fact(v):
            line = f"variant {i}: unfilled {FACT} placeholder, needs a real fact before use"
            if line not in risks:
                risks.append(line)
    found = sum(count_unfilled_facts(v) for v in pack.variants)
    return pack.model_copy(update={"risks": risks,
                                   "unfilled_facts": max(pack.unfilled_facts, found)})


def write(
    client: MuseClient,
    angle: Angle,
    persona: str,
    *,
    run_id: str,
    brief: str = "",
    max_tokens: int = 4000,
) -> tuple[CopyPack, MuseResult]:
    user = _user_message(angle, persona, brief)
    tier, reason, model = choose_tier(user, "contributor")
    pack, result = structured(
        client, model,
        [{"role": "system", "content": prompts.system(AGENT)},
         {"role": "user", "content": user}],
        CopyPack, tier=tier, tier_reason=reason, run_id=run_id, agent=AGENT,
        max_tokens=max_tokens, reasoning_effort="minimal",
    )
    return flag_unfilled_facts(pack), result
