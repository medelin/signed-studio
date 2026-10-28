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
# Secondary net only (the allowlist below is the control): "fact" between any two
# bracket-like characters (Unicode Ps/Pe/Pi/Pf, plus < >), after NFKC with format
# characters removed and every bracket folded to "[" / "]".
_FACT = re.compile(r"\[\W*f\W*a\W*c\W*t\W*\]", re.IGNORECASE)


def _fold_bracket(ch: str) -> str:
    if ch == "<" or unicodedata.category(ch) in ("Ps", "Pi"):
        return "["
    if ch == ">" or unicodedata.category(ch) in ("Pe", "Pf"):
        return "]"
    return ch


# What a signed copy field may contain (checked after NFKC and on the original text):
# Latin letters incl. Polish diacritics, digits, space/newline, common punctuation, emoji.
_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ0123456789 \n")
_PUNCT = frozenset(".,;:!?'\"-\u2013\u2014()/&%+@#*\u2026")
_SPACES = frozenset("\u00a0\u202f")  # (narrow) no-break space, as in "89\u00a0zł"; NFKC → " "
_ZWJ, _VS16 = "\u200d", "\ufe0f"
SIGNED_FIELDS = ("headline", "primary_text", "description", "cta")


# Emoji = explicit code points / ranges with emoji presentation (Unicode emoji-data),
# not whole blocks: the dingbat blocks also hold ornamental brackets and quotation marks
# (U+2768-2775, U+275B-2760) that must not pass as emoji.
_EMOJI_RANGES = (
    (0x1F300, 0x1F64F), (0x1F680, 0x1F6FF), (0x1F7E0, 0x1F7EB), (0x1F7F0, 0x1F7F0),
    (0x1F900, 0x1F9FF), (0x1FA70, 0x1FAFF),
)
_EMOJI_BMP = frozenset(
    [*range(0x2600, 0x2605), 0x260E, 0x2611, 0x2614, 0x2615, 0x2618, 0x261D, 0x2620,
     0x2622, 0x2623, 0x2626, 0x262A, 0x262E, 0x262F, *range(0x2638, 0x263B), 0x2640,
     0x2642, *range(0x2648, 0x2654), 0x265F, 0x2660, 0x2663, 0x2665, 0x2666, 0x2668,
     0x267B, 0x267E, 0x267F, *range(0x2692, 0x2698), 0x2699, 0x269B, 0x269C, 0x26A0,
     0x26A1, 0x26A7, 0x26AA, 0x26AB, 0x26B0, 0x26B1, 0x26BD, 0x26BE, 0x26C4, 0x26C5,
     0x26C8, 0x26CE, 0x26CF, 0x26D1, 0x26D3, 0x26D4, 0x26E9, 0x26EA, *range(0x26F0, 0x26F6),
     *range(0x26F7, 0x26FB), 0x26FD, 0x2702, 0x2705, *range(0x2708, 0x270E), 0x270F,
     0x2712, 0x2714, 0x2716, 0x271D, 0x2721, 0x2728, 0x2733, 0x2734, 0x2744, 0x2747,
     0x274C, 0x274E, 0x2753, 0x2754, 0x2755, 0x2757, 0x2763, 0x2764, 0x2795, 0x2796,
     0x2797, 0x27A1, 0x27B0, 0x27BF])


def _emoji(ch: str) -> bool:
    cp = ord(ch)
    return cp in _EMOJI_BMP or any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES)


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
    removed and bracket-like characters folded to ``[`` / ``]``."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(_fold_bracket(ch) for ch in folded if unicodedata.category(ch) != "Cf")


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
