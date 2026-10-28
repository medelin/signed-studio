"""Audiences: Advantage+ audience by default, the owner's hints as suggestions, resolved to
Meta ids by code before the owner signs.

- ``resolve`` looks every interest hint (and every language) up through the targeting
  search API (``GET /search?type=adinterest|adlocale&q=…``, read-only) and keeps the best
  match: the result whose name equals the hint, else the first one; a hint with no result
  is listed as unresolved, never replaced by a guess. The resolved ids are part of the
  signed payload (``audience``) and shown on the approval screen.
- ``targeting`` builds the ad set's targeting spec. With Advantage+ audience
  (``targeting_automation.advantage_audience: 1``, the default) Meta treats age range,
  gender and interests as suggestions; location, minimum age, language and excluded custom
  audiences stay controls, ``age_min`` must be 18–25 and ``age_max`` is fixed at 65, so
  the owner's range goes into ``age_range``. With it off, the brief's values are hard
  targeting.
- Custom audiences and lookalikes come only from ids the owner gives; a lookalike is made
  from the owner's source audience (``subtype=LOOKALIKE``) by the media buyer under the
  campaign approval. No user data is ever uploaded.

Sources (read 2026-10-28):
https://developers.facebook.com/docs/marketing-api/audiences/reference/targeting-expansion/advantage-audience/
(age_min 18–25, age_max fixed at 65, age_range, non-expanded controls);
https://developers.facebook.com/docs/marketing-api/audiences/reference/targeting-search/
(adinterest, adlocale); https://developers.facebook.com/docs/marketing-api/audiences/guides/lookalike-audiences/
(origin_audience_id, lookalike_spec ratio and country).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

GENDERS = {"male": 1, "female": 2}
ADVANTAGE_AGE_MIN = (18, 25)  # the range age_min may take with Advantage+ audience
ADVANTAGE_AGE_MAX = 65
MAX_HINTS = 10
NUMERIC_ID = re.compile(r"\d{1,30}")

# (kind, query) → search results [{"id"|"key", "name"}]; kind: adinterest | adlocale
Search = Callable[[str, str], list[dict[str, Any]]]


@dataclass
class Resolved:
    interests: list[dict[str, Any]] = field(default_factory=list)  # {hint, id, name}
    excluded_interests: list[dict[str, Any]] = field(default_factory=list)
    locales: list[dict[str, Any]] = field(default_factory=list)  # {hint, key, name}
    unresolved: list[str] = field(default_factory=list)
    searches: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {"interests": self.interests, "excluded_interests": self.excluded_interests,
                "locales": self.locales, "unresolved": self.unresolved}


def _best(hint: str, results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    folded = hint.strip().casefold()
    for r in results:
        if str(r.get("name", "")).strip().casefold() == folded:
            return r
    return results[0] if results else None


def resolve(search: Search | None, interests: Sequence[str],
            excluded_interests: Sequence[str] = (), languages: Sequence[str] = ()) -> Resolved:
    """Hints → Meta ids through ``search``; without a search function every hint is
    unresolved (the campaign then targets without it)."""
    out = Resolved()
    for hints, target, kind in ((interests, out.interests, "adinterest"),
                                (excluded_interests, out.excluded_interests, "adinterest"),
                                (languages, out.locales, "adlocale")):
        for hint in list(dict.fromkeys(h.strip() for h in hints if h.strip()))[:MAX_HINTS]:
            results = []
            if search is not None:
                out.searches += 1
                results = [r for r in search(kind, hint) if isinstance(r, Mapping)]
            best = _best(hint, results)
            ident = None if best is None else best.get("key" if kind == "adlocale" else "id")
            if best is None or ident in (None, ""):
                out.unresolved.append(hint)
            elif kind == "adlocale":
                target.append({"hint": hint, "key": int(ident), "name": str(best["name"])[:80]})
            elif NUMERIC_ID.fullmatch(str(ident)):
                target.append({"hint": hint, "id": str(ident), "name": str(best["name"])[:80]})
            else:
                out.unresolved.append(hint)
    return out


def targeting(*, countries: Sequence[str], age_min: int, age_max: int,
              genders: Sequence[str] = (), resolved: Resolved | Mapping[str, Any] | None = None,
              excluded_countries: Sequence[str] = (), custom_audiences: Sequence[str] = (),
              excluded_custom_audiences: Sequence[str] = (),
              advantage: bool = True) -> dict[str, Any]:
    """The ad set's ``targeting`` spec (Marketing API targeting fields)."""
    r = resolved.as_payload() if isinstance(resolved, Resolved) else dict(resolved or {})
    spec: dict[str, Any] = {"geo_locations": {"countries": list(countries) or ["PL"]}}
    if advantage:
        lo, hi = ADVANTAGE_AGE_MIN
        spec.update({"age_min": max(lo, min(hi, age_min)), "age_max": ADVANTAGE_AGE_MAX,
                     "age_range": [max(18, age_min), max(max(18, age_min), age_max)]})
    else:
        spec.update({"age_min": max(18, age_min), "age_max": age_max})
    if genders and set(genders) != set(GENDERS):
        spec["genders"] = sorted(GENDERS[g] for g in set(genders))
    if r.get("locales"):
        spec["locales"] = [int(x["key"]) for x in r["locales"]]
    if r.get("interests"):
        spec["flexible_spec"] = [{"interests": [{"id": x["id"], "name": x["name"]}
                                                for x in r["interests"]]}]
    if r.get("excluded_interests"):
        spec["exclusions"] = {"interests": [{"id": x["id"], "name": x["name"]}
                                            for x in r["excluded_interests"]]}
    if excluded_countries:
        spec["excluded_geo_locations"] = {"countries": list(excluded_countries)}
    if custom_audiences:
        spec["custom_audiences"] = [{"id": i} for i in custom_audiences]
    if excluded_custom_audiences:
        spec["excluded_custom_audiences"] = [{"id": i} for i in excluded_custom_audiences]
    spec["targeting_automation"] = {"advantage_audience": 1 if advantage else 0}
    return spec


def lookalike_spec(origin_id: str, ratio_percent: int, country: str,
                   name: str) -> dict[str, Any]:
    """The signed ``lookalike`` block: a lookalike of the owner's own source audience."""
    if not NUMERIC_ID.fullmatch(origin_id):
        raise ValueError("the lookalike source must be the owner's audience id")
    if not 1 <= ratio_percent <= 10:
        raise ValueError("lookalike size is 1-10 % of the country")
    return {"name": name[:100], "subtype": "LOOKALIKE", "origin_audience_id": origin_id,
            "lookalike_spec": {"ratio": round(ratio_percent / 100, 2), "country": country}}
