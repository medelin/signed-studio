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
# Secondary net only (the allowlist below is the control): bracketed "fact" after NFKC and
# with format characters removed.
_FACT = re.compile(r"[\[({<\u27e6\u3010\u3014]\W*f\W*a\W*c\W*t\W*[\])}>\u27e7\u3011\u3015]",
                   re.IGNORECASE)

# What a signed copy field may contain (checked after NFKC and on the original text):
# Latin letters incl. Polish diacritics, digits, space/newline, common punctuation, emoji.
_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ0123456789 \n")
_PUNCT = frozenset(".,;:!?'\"-\u2013\u2014()/&%+@#*\u2026")
_SPACES = frozenset("\u00a0\u202f")  # (narrow) no-break space, as in "89\u00a0zł"; NFKC → " "
_ZWJ, _VS16 = "\u200d", "\ufe0f"
SIGNED_FIELDS = ("headline", "primary_text", "description", "cta")


def _emoji(ch: str) -> bool:
    return 0x1F300 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF


def _unsupported(text: str, *, extra: frozenset[str] = frozenset()) -> str | None:
    """First character outside the allowlist, or ``None``. ZWJ and VS16 pass only inside
    an emoji sequence (VS16 right after an emoji; ZWJ between two emoji)."""
    for i, ch in enumerate(text):
        if ch in _LETTERS or ch in _PUNCT or ch in extra or _emoji(ch):
            continue
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == _VS16 and prev and _emoji(prev):
            continue
        if ch == _ZWJ and prev and (_emoji(prev) or prev == _VS16) and nxt and _emoji(nxt):
            continue
        return ch
    return None


def unsupported_character(v: CopyVariant) -> str | None:
    """``"unsupported character U+XXXX in <field>"`` for the first signed field holding a
    character outside the allowlist (in its NFKC form or as written), else ``None``."""
    for name in SIGNED_FIELDS:
        text = str(getattr(v, name))
        extra = frozenset("_") if name == "cta" else frozenset()
        bad = (_unsupported(unicodedata.normalize("NFKC", text), extra=extra)
               or _unsupported(text, extra=extra | _SPACES))
        if bad is not None:
            return f"unsupported character U+{ord(bad):04X} in {name}"
    return None


def normalise(text: str) -> str:
    """NFKC with Unicode format characters (category Cf: zero-width, soft hyphen, bidi)
    removed."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")


def count_unfilled_facts(v: CopyVariant) -> int:
    return sum(len(_FACT.findall(normalise(t))) for t in (v.primary_text, v.headline,
                                                           v.description))


def has_unfilled_fact(v: CopyVariant) -> bool:
    """Secondary net: a ``[FACT]`` placeholder survives NFKC and Cf removal."""
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
