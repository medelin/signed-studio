"""Copywriter: three ad copy variants for one plan angle (strict JSON ``CopyPack``).

Runs on the Contributor tier when the input has no PII, otherwise on Standard; the choice
and its reason are made in code (``pii.choose_tier``) and recorded in the receipt.
"""

from __future__ import annotations

from studio.agents import prompts
from studio.agents.schemas import Angle, CopyPack
from studio.muse.client import MuseClient, MuseResult
from studio.muse.structured import structured
from studio.pii import choose_tier

AGENT = "copywriter"
FACT = "[FACT]"


def _user_message(angle: Angle, persona: str, brief: str) -> str:
    lines = [f"Angle: {angle.name}", f"Hook: {angle.hook}", f"Persona: {persona}"]
    if brief:
        lines.append(f"Brief: {brief}")
    return "\n".join(lines)


def flag_unfilled_facts(pack: CopyPack) -> CopyPack:
    """Every variant still holding ``[FACT]`` gets a risk line (if the model did not add one)."""
    risks = list(pack.risks)
    for i, v in enumerate(pack.variants, start=1):
        if FACT in f"{v.primary_text} {v.headline} {v.description}":
            line = f"variant {i}: unfilled {FACT} placeholder, needs a real fact before use"
            if line not in risks:
                risks.append(line)
    return pack.model_copy(update={"risks": risks})


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
