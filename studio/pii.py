"""PII detection and model-tier routing, decided in code before any model call.

``contains_pii`` finds e-mail addresses, Polish and UK phone numbers (spaced, dashed,
bracketed, slashed or contiguous, with or without +48/+44 or 0048/0044), and NIP/PESEL
numbers whose checksum is valid. Every digit run is split into groups and every contiguous
sub-sequence of groups is classified, so a number next to a price or a year is still found.

``choose_tier`` fails closed: it never selects the Contributor tier for text with PII, for
text holding 9+ digits (any script) within any 20 letters-and-digits characters ("long
digit run", whatever it is), nor for
customer-originated text (comments, leads, transcripts), and explains why in a reason that
names only the kind of PII, never its value.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

STANDARD_MODEL = "muse-spark-1.3"
CONTRIBUTOR_MODEL = "muse-spark-1.3-contributor"
CUSTOMER_SOURCES = frozenset({"comment", "lead", "transcript"})

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# A run of digits with the separators people type; classified after normalisation.
_RUN = re.compile(r"(?<![\w+])(?:\+|00)?\(?\d[\d \-()./]{5,}\d(?!\w)")
# Fail-closed rule: LONG_RUN_DIGITS digits (any script) within any LONG_RUN_WINDOW
# characters, whatever lies between them.
LONG_RUN_DIGITS = 9
LONG_RUN_WINDOW = 20
_WORD = re.compile(r"\w+")
_MAX_CANDIDATE_DIGITS = 14  # longest form classified: 00 + 44 + 10 digits
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


_GROUP = re.compile(r"\d+")


def _classify_groups(raw: str, groups: list[re.Match[str]], i: int, j: int) -> str | None:
    """Classify groups ``i..j`` of ``raw``: as written (with a directly preceding ``+``),
    and, when the first group starts with 48/44 and is longer than the prefix, as
    ``+`` prefix + number."""
    plus = "+" if raw[groups[i].start() - 1:groups[i].start()] == "+" else ""
    candidate = plus + " ".join(g.group() for g in groups[i:j + 1])
    kind = _classify(candidate)
    if kind is None and not plus and groups[i].group()[:2] in ("48", "44"):
        kind = _classify("+" + candidate)
    return kind


def _phones(text: str) -> list[tuple[str, int, int]]:
    """``(kind, start, end)`` for every contiguous sub-sequence of digit groups, in every
    digit run, that classifies as a phone, NIP or PESEL (ordered by start)."""
    out = []
    for run in _RUN.finditer(text):
        raw = run.group()
        groups = list(_GROUP.finditer(raw))
        for i in range(len(groups)):
            digits = 0
            for j in range(i, len(groups)):
                digits += len(groups[j].group())
                if digits > _MAX_CANDIDATE_DIGITS:
                    break
                kind = _classify_groups(raw, groups, i, j)
                if kind:
                    start = groups[i].start()
                    if raw[start - 1:start] == "+":
                        start -= 1
                    out.append((kind, run.start() + start, run.start() + groups[j].end()))
    return sorted(out, key=lambda m: (m[1], m[2]))


def fold(text: str) -> str:
    """Position-preserving ASCII view for matching: every character that is a digit after
    NFKC (fullwidth, Arabic-Indic, superscript...) becomes its ASCII digit, every dash
    (Pd, U+2212) becomes ``-`` and every space separator (Zs) becomes `` ``."""
    out = []
    for ch in text:
        if ch.isascii():
            out.append(ch)
            continue
        n = unicodedata.normalize("NFKC", ch)
        d = unicodedata.digit(n, None) if len(n) == 1 else None
        if d is None and len(n) == 1:
            d = unicodedata.decimal(n, None)
        if d is not None:
            out.append(str(d))
        elif ch == "\u2212" or unicodedata.category(ch) == "Pd":
            out.append("-")
        elif unicodedata.category(ch) == "Zs":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _windows(positions: list[int]) -> list[tuple[int, int]]:
    """Merged ``(first, last)`` index pairs where LONG_RUN_DIGITS positions fit in the
    window (positions are indexes into the matching view)."""
    spans: list[tuple[int, int]] = []
    k = LONG_RUN_DIGITS - 1
    for i in range(len(positions) - k):
        if positions[i + k] - positions[i] < LONG_RUN_WINDOW:
            first, last = positions[i], positions[i + k]
            if spans and first <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(last, spans[-1][1]))
            else:
                spans.append((first, last))
    return spans


def long_digit_spans(text: str, *, letters_break: bool = False) -> list[tuple[int, int]]:
    """``(start, end)`` spans of ``text`` holding 9+ digits within 20 characters.

    The window is measured on a matching view that keeps only letters and digits (Unicode
    L* and N* after NFKC and digit mapping): spaces, punctuation, symbols, format
    characters (ZWSP, U+2060, soft hyphen) and combining marks cannot stretch it.
    With ``letters_break`` (redaction), a word that contains a letter (a hex digest, an
    opaque id) is a boundary instead: its digits do not count and the window restarts."""
    folded = fold(text)
    view = [i for i, ch in enumerate(folded) if unicodedata.category(ch)[0] in "LN"]
    in_letter_word: set[int] = set()
    if letters_break:
        for m in _WORD.finditer(folded):
            if any(ch.isalpha() for ch in m.group()):
                in_letter_word.update(range(m.start(), m.end()))
    pairs: list[tuple[int, int]] = []
    positions: list[int] = []
    for v, i in enumerate(view):
        if i in in_letter_word:
            pairs += _windows(positions)
            positions = []
        elif folded[i].isdigit():
            positions.append(v)
    pairs += _windows(positions)
    return [(view[first], view[last] + 1) for first, last in pairs]


def long_digit_run(text: str) -> bool:
    """True if ``text`` holds 9+ digits within any 20 characters (fail-closed routing)."""
    return bool(long_digit_spans(text))


def redact_digit_runs(text: str, mark: str = "[phone]") -> str:
    """Every long digit span (``letters_break``) replaced by ``mark``."""
    for start, end in reversed(long_digit_spans(text, letters_break=True)):
        text = text[:start] + mark + text[end:]
    return text


def contains_pii(text: str) -> Match | None:
    """First PII match in ``text`` (by position), or ``None``."""
    found: list[Match] = []
    m = _EMAIL.search(text)
    if m:
        found.append(Match("email", m.start(), m.end()))
    phones = _phones(fold(text))
    if phones:
        kind, start, end = phones[0]
        found.append(Match(kind, start, end))
    return min(found, key=lambda x: x.start) if found else None


def redact(text: str) -> str:
    """``text`` with every e-mail (``[email]``), phone/NIP/PESEL (``[phone]``, ``[nip]``,
    ``[pesel]``) and long digit run (``[digits]``) replaced: what a model may see of
    customer text. Detection runs on the original first (``contains_pii``)."""
    spans = [(m.start(), m.end(), "email") for m in _EMAIL.finditer(text)]
    spans += [(start, end, kind) for kind, start, end in _phones(fold(text))]
    spans.sort()
    merged: list[list[Any]] = []
    for start, end, kind in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end, kind])
    for start, end, kind in reversed(merged):
        text = text[:start] + f"[{kind}]" + text[end:]
    return redact_digit_runs(text, "[digits]")


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
    if long_digit_run(text):
        return "standard", "long digit run", STANDARD_MODEL
    return "contributor", "no pii detected: public copy on contributor", CONTRIBUTOR_MODEL


def find_email(text: str) -> str:
    m = _EMAIL.search(text)
    return m.group() if m else ""


def find_phone(text: str) -> str:
    for kind, start, end in _phones(fold(text)):
        if kind == "phone":
            return text[start:end]
    return ""
