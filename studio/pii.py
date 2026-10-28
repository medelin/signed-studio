"""PII detection and model-tier routing, decided in code before any model call.

``contains_pii`` finds e-mail addresses, Polish and UK phone numbers (spaced, dashed,
bracketed or contiguous, with or without +48/+44), also when another number follows or
precedes them in the same digit run (3-3-3 and 2-3-2-2 groupings on group boundaries), and
NIP/PESEL numbers whose checksum is valid. ``choose_tier`` never selects the Contributor
tier for text with PII, nor for
customer-originated text (comments, leads, transcripts), and explains why in a reason that
names only the kind of PII, never its value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STANDARD_MODEL = "muse-spark-1.3"
CONTRIBUTOR_MODEL = "muse-spark-1.3-contributor"
CUSTOMER_SOURCES = frozenset({"comment", "lead", "transcript"})

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# A run of digits with the separators people type; classified after normalisation.
_RUN = re.compile(r"(?<![\w+])(?:\+|00)?\(?\d[\d \-().]{5,}\d(?!\w)")
_NIP_W = (6, 5, 7, 2, 3, 4, 5, 6, 7)
_PESEL_W = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)


@dataclass(frozen=True)
class Match:
    kind: str  # email | phone | nip | pesel
    start: int
    end: int


def nip_valid(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit():
        return False
    check = sum(int(d) * w for d, w in zip(digits, _NIP_W, strict=False)) % 11
    return check != 10 and check == int(digits[9])


def pesel_valid(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit():
        return False
    check = (10 - sum(int(d) * w for d, w in zip(digits, _PESEL_W, strict=False)) % 10) % 10
    return check == int(digits[10])


def _classify(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    intl = raw.startswith(("+", "00"))
    if intl:
        body = digits[2:] if raw.startswith("00") else digits
        if body.startswith("48") and len(body) == 11:
            return "phone"
        if body.startswith("44") and len(body) in (11, 12):
            return "phone"
        return None
    if len(digits) == 9:
        return "phone"  # PL mobile 3-3-3 or landline (2) 3-2-2
    if len(digits) == 10:
        return "nip" if nip_valid(digits) else None
    if len(digits) == 11:
        if pesel_valid(digits):
            return "pesel"
        if digits[0] == "0" and digits[1] in "1237":
            return "phone"  # UK national format: 01/02/03 landline, 07 mobile
    return None


# Digit groupings of a PL number inside a longer run ("600 123 456 89 PLN").
_PHONE_GROUPS = ((3, 3, 3), (2, 3, 2, 2), (9,))
_GROUP = re.compile(r"\d+")


def _phone_window(raw: str) -> tuple[int, int] | None:
    """Span of a 3-3-3 / 2-3-2-2 / 9-digit grouping that starts and ends on group
    boundaries of ``raw`` (with a directly preceding +48/0048 included), or ``None``."""
    groups = list(_GROUP.finditer(raw))
    lengths = [len(g.group()) for g in groups]
    for i in range(len(groups)):
        for pattern in _PHONE_GROUPS:
            if tuple(lengths[i:i + len(pattern)]) != pattern:
                continue
            start, end = groups[i].start(), groups[i + len(pattern) - 1].end()
            if i > 0 and groups[i - 1].group() in ("48", "0048"):
                prefix = groups[i - 1].start()
                if raw[prefix - 1:prefix] == "+" or groups[i - 1].group() == "0048":
                    start = prefix - (raw[prefix - 1:prefix] == "+")
            return start, end
    return None


def _phones(text: str) -> list[tuple[str, int, int]]:
    """``(kind, start, end)`` for each digit run that classifies, whole or by window."""
    out = []
    for run in _RUN.finditer(text):
        raw = run.group().strip(" -.(")
        offset = run.start() + run.group().index(raw)
        kind = _classify(raw)
        if kind:
            out.append((kind, offset, offset + len(raw)))
            continue
        window = _phone_window(raw)
        if window:
            out.append(("phone", offset + window[0], offset + window[1]))
    return out


def contains_pii(text: str) -> Match | None:
    """First PII match in ``text`` (by position), or ``None``."""
    found: list[Match] = []
    m = _EMAIL.search(text)
    if m:
        found.append(Match("email", m.start(), m.end()))
    phones = _phones(text)
    if phones:
        kind, start, end = phones[0]
        found.append(Match(kind, start, end))
    return min(found, key=lambda x: x.start) if found else None


def choose_tier(text: str, requested: str, *, source: str = "operator") -> tuple[str, str, str]:
    """``(tier, reason, model_id)`` for a call carrying ``text``."""
    if requested not in ("standard", "contributor"):
        raise ValueError(f"unknown tier {requested!r}")
    if source in CUSTOMER_SOURCES:
        return "standard", f"customer text ({source}): contributor never used", STANDARD_MODEL
    if requested == "standard":
        return "standard", "standard tier requested", STANDARD_MODEL
    match = contains_pii(text)
    if match:
        return "standard", f"pii detected ({match.kind}): contributor refused", STANDARD_MODEL
    return "contributor", "no pii detected: public copy on contributor", CONTRIBUTOR_MODEL


def find_email(text: str) -> str:
    m = _EMAIL.search(text)
    return m.group() if m else ""


def find_phone(text: str) -> str:
    for kind, start, end in _phones(text):
        if kind == "phone":
            return text[start:end]
    return ""
