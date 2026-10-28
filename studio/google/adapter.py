"""Google Ads adapter interface: the only code that writes to Google Ads.

A second, minimal channel next to Meta, built the same way. Two implementations: ``mock``
(default; tests, sandbox, demo, replay — nothing leaves the process) and ``rest`` (Google
Ads REST API v23 over ``httpx``, no client library, operator machine only,
``GOOGLE_ADS_ADAPTER=rest``).

What is created is one Search campaign: a budget, a campaign, one ad group, keywords, one
Responsive Search Ad and the geo target, all from the signed ``google`` spec of a
``google.create`` payload (``spec``, ``check_spec``). The rules Meta's adapter follows hold
here too:

- Every write takes the approved ``payload`` and the ``Verified`` approval for it and
  refuses (``NotApproved``) unless ``approval.action`` is the write's action and
  ``approval.content_sha256 == sha256(canonical(payload))`` (``meta.adapter.require``). The
  request bodies are built from the signed spec only (``create_operations``), never from
  separate arguments.
- The campaign, ad group and ad are created ``PAUSED``, a literal in the code; a spec
  asking for anything else is refused (``PausedOnly``). Activation is a separate write
  needing a ``google.activate`` approval that names the three objects.
- The spec is hashed (``spec_sha256``) and the approval signs that hash too (``approvals.
  spec_of``); every adapter step records it, and ``studio verify`` rejects a log whose
  step names another one.
- The headline (<= 30 characters) and description (<= 90) limits of a Responsive Search Ad
  are checked in code (``check_spec``); text that does not fit is dropped when the spec is
  built, never truncated.
- Budgets are in the Google account's currency, read from the customer resource (or
  ``GOOGLE_ADS_ACCOUNT_CURRENCY``); a brief in another currency is a field error, never
  converted.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

from studio.approvals import Verified
from studio.meta.adapter import AdapterError, NotApproved, PausedOnly
from studio.receipts import canonical, sha256_hex

__all__ = ["ACTIVATE", "CREATE", "PAUSED", "Adapter", "AdapterError", "NotApproved",
           "PausedOnly", "SpecError", "activation_operations", "build_spec", "check_spec",
           "create_operations", "customer_id_of", "make", "micros", "spec_sha256"]

CREATE: Final = "google.create"
ACTIVATE: Final = "google.activate"
PAUSED: Final = "PAUSED"
ENABLED: Final = "ENABLED"
MOCK_CUSTOMER: Final = "1234567890"
MAX_HEADLINE: Final = 30
MAX_DESCRIPTION: Final = 90
HEADLINES: Final = (3, 15)  # minimum, maximum of a Responsive Search Ad
DESCRIPTIONS: Final = (2, 4)
MAX_KEYWORDS: Final = 8
MAX_KEYWORD: Final = 80
EU_POLITICAL: Final = "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"

# Country criteria are 2000 + the ISO 3166-1 numeric code (geoTargetConstants/2826 = GB).
# A country that is not here is refused (``google_geo``), never guessed.
ISO_NUMERIC: Final = {
    "AR": 32, "AT": 40, "AU": 36, "BE": 56, "BG": 100, "BR": 76, "CA": 124, "CH": 756,
    "CL": 152, "CO": 170, "CY": 196, "CZ": 203, "DE": 276, "DK": 208, "EE": 233, "ES": 724,
    "FI": 246, "FR": 250, "GB": 826, "GR": 300, "HR": 191, "HU": 348, "IE": 372, "IL": 376,
    "IN": 356, "IS": 352, "IT": 380, "JP": 392, "LT": 440, "LU": 442, "LV": 428, "MT": 470,
    "MX": 484, "NL": 528, "NO": 578, "NZ": 554, "PL": 616, "PT": 620, "RO": 642, "SE": 752,
    "SI": 705, "SK": 703, "TR": 792, "UA": 804, "US": 840, "ZA": 710,
}
_CUSTOMER = re.compile(r"\d{10}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_GEO = re.compile(r"\d{4}")
_SYMBOLS = re.compile(r"[!@%^()={};~`<>?\\|,]")  # keyword text Google refuses
_NAME = {"campaign": r"campaigns/\d{1,20}", "ad_group": r"adGroups/\d{1,20}",
         "ad_group_ad": r"adGroupAds/\d{1,20}~\d{1,20}"}
SPEC_KEYS: Final = frozenset({"channel", "customer_id", "currency", "campaign", "geo",
                              "ad_group", "keywords", "ad"})


class SpecError(ValueError):
    """The spec cannot be built or is not valid; ``code`` is a stable error code."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def micros(amount: float) -> int:
    """Whole account-currency units → Google's micros (1 000 000 per unit), rounded up."""
    return math.ceil(round(amount * 1_000_000, 3)) if amount > 0 else 0


def customer_id_of(value: str) -> str:
    """A customer id as ten digits (dashes allowed on input)."""
    digits = (value or "").replace("-", "").strip()
    if not _CUSTOMER.fullmatch(digits):
        raise ValueError("a Google Ads customer id is ten digits")
    return digits


def spec_sha256(spec: Mapping[str, Any]) -> str:
    """Hash of a Google spec (what the approval signs as ``spec_sha256``)."""
    return sha256_hex(canonical(dict(spec)))


def _fit(texts: list[str], limit: int, cap: int) -> list[str]:
    """Distinct (case-insensitive) texts that fit ``limit``; those that do not are
    dropped, never truncated."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        t = " ".join(str(raw).split())
        if t and len(t) <= limit and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out[:cap]


def _phrase(text: str) -> str:
    """A keyword phrase from free text: the first clause (before a comma, semicolon,
    colon or full stop), symbols Google refuses turned into spaces, at most six words."""
    clause = re.split(r"[,;:.]", str(text))[0]
    return " ".join(_SYMBOLS.sub(" ", clause).split()[:6])


def keywords_of(product: str, display_name: str, locations: list[str]) -> list[str]:
    """Keywords derived by code from the confirmed brief: the product, the product with
    each place the owner named, and the advertiser's name with the product."""
    head = _phrase(product)
    if not head:
        return []
    cands = [head, *(_phrase(f"{head} {loc}") for loc in locations[:3]),
             _phrase(f"{display_name} {head}")]
    return _fit(cands, MAX_KEYWORD, MAX_KEYWORDS)


def build_spec(*, item_id: str, customer_id: str, currency: str, display_name: str,
               product: str, offer: str, proposition: str, countries: list[str],
               locations: list[str], link: str, variants: list[Mapping[str, Any]],
               daily_budget: float, facts: list[str] | None = None) -> dict[str, Any]:
    """The ``google`` block of a ``google.create`` payload, built by code from the
    confirmed brief and the copy variants that were reviewed for Meta. ``SpecError`` when
    the brief cannot make a valid Responsive Search Ad (too little text that fits). The
    text comes only from the reviewed copy variants and what the owner confirmed in the
    brief (name, product, offer, proposition, facts), verbatim."""
    known = [*(facts or [])]
    headlines = _fit([*(str(v.get("headline", "")) for v in variants), _phrase(product),
                      display_name, offer, f"{display_name} · {_phrase(product)}", *known],
                     MAX_HEADLINE, HEADLINES[1])
    headlines = [h for h in headlines if "!" not in h]
    descriptions = _fit([*(str(v.get("description", "")) for v in variants),
                         *(str(v.get("primary_text", "")) for v in variants), proposition,
                         offer, *known], MAX_DESCRIPTION, DESCRIPTIONS[1])
    keywords = keywords_of(product, display_name, locations)
    unknown = [c for c in countries if c not in ISO_NUMERIC]
    if unknown or not countries:
        raise SpecError("a country has no Google geo target here", "google_geo")
    spec = {
        "channel": "google_search", "customer_id": customer_id, "currency": currency,
        "campaign": {
            "name": f"Muse Studio · {display_name} · Search · {item_id[-6:]}"[:200],
            "status": PAUSED, "type": "SEARCH", "bidding": "TARGET_SPEND",
            "daily_budget_micros": micros(daily_budget),
            "contains_eu_political_advertising": EU_POLITICAL},
        "geo": {"countries": list(countries),
                "criteria": [str(2000 + ISO_NUMERIC[c]) for c in countries]},
        "ad_group": {"name": f"{product} · {item_id[-6:]}"[:200], "status": PAUSED,
                     "type": "SEARCH_STANDARD"},
        "keywords": [{"text": k, "match_type": "PHRASE"} for k in keywords],
        "ad": {"status": PAUSED, "final_url": link, "headlines": headlines,
               "descriptions": descriptions},
    }
    return check_spec(spec)


def check_spec(spec: Any, customer_id: str | None = None) -> dict[str, Any]:
    """The adapters' gate on the signed ``google`` block: exactly the keys ``build_spec``
    makes, every status ``PAUSED``, Responsive Search Ad limits (3-15 headlines of at most
    30 characters, 2-4 descriptions of at most 90), well-formed keywords, geo criteria
    that match the countries, an https final URL and a positive budget; when
    ``customer_id`` is given, the spec must target that account. ``SpecError`` otherwise."""
    if not isinstance(spec, Mapping) or set(spec) != SPEC_KEYS:
        raise SpecError("the signed payload has no valid google spec", "google_spec")
    c, g, ag, ad = (spec.get(k) for k in ("campaign", "geo", "ad_group", "ad"))
    if not all(isinstance(x, Mapping) for x in (c, g, ag, ad)) or not isinstance(
            spec.get("keywords"), list):
        raise SpecError("the google spec is malformed", "google_spec")
    assert isinstance(c, Mapping) and isinstance(g, Mapping)
    assert isinstance(ag, Mapping) and isinstance(ad, Mapping)
    if spec["channel"] != "google_search":
        raise SpecError("not a Search campaign", "google_spec")
    for obj in (c, ag, ad):
        if obj.get("status") != PAUSED:
            raise PausedOnly(f"objects are created PAUSED only, not {obj.get('status')!r}")
    if not isinstance(spec["customer_id"], str) or not _CUSTOMER.fullmatch(spec["customer_id"]):
        raise SpecError("bad customer id", "google_spec")
    if customer_id is not None and spec["customer_id"] != customer_id:
        raise SpecError("the spec targets another Google Ads account", "google_account")
    if not isinstance(spec["currency"], str) or not _CURRENCY.fullmatch(spec["currency"]):
        raise SpecError("bad currency", "google_spec")
    if (set(c) != {"name", "status", "type", "bidding", "daily_budget_micros",
                   "contains_eu_political_advertising"}
            or c["type"] != "SEARCH" or c["bidding"] != "TARGET_SPEND"
            or c["contains_eu_political_advertising"] != EU_POLITICAL
            or not isinstance(c["name"], str) or not c["name"].strip()
            or isinstance(c["daily_budget_micros"], bool)
            or not isinstance(c["daily_budget_micros"], int) or c["daily_budget_micros"] <= 0):
        raise SpecError("the campaign block is not valid", "google_spec")
    if set(ag) != {"name", "status", "type"} or ag["type"] != "SEARCH_STANDARD" \
            or not isinstance(ag["name"], str) or not ag["name"].strip():
        raise SpecError("the ad group block is not valid", "google_spec")
    countries, criteria = g.get("countries"), g.get("criteria")
    if (set(g) != {"countries", "criteria"} or not isinstance(countries, list)
            or not countries or not all(isinstance(x, str) and x in ISO_NUMERIC
                                        for x in countries)
            or criteria != [str(2000 + ISO_NUMERIC[x]) for x in countries]
            or not all(_GEO.fullmatch(x) for x in criteria)):
        raise SpecError("the geo target does not match its countries", "google_geo")
    kws = spec["keywords"]
    if not 1 <= len(kws) <= MAX_KEYWORDS:
        raise SpecError("a Search ad group needs 1-8 keywords", "google_keywords")
    for k in kws:
        text = k.get("text") if isinstance(k, Mapping) else None
        if (not isinstance(k, Mapping) or set(k) != {"text", "match_type"}
                or k["match_type"] not in ("PHRASE", "EXACT") or not isinstance(text, str)
                or not text.strip() or len(text) > MAX_KEYWORD or len(text.split()) > 10
                or _SYMBOLS.search(text)):
            raise SpecError("a keyword is not valid", "google_keywords")
    if set(ad) != {"status", "final_url", "headlines", "descriptions"}:
        raise SpecError("the ad block is not valid", "google_spec")
    url = ad["final_url"]
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2000 \
            or any(ch.isspace() for ch in url):
        raise SpecError("the final URL must be https", "google_url")
    for name, limit, (lo, hi) in (("headlines", MAX_HEADLINE, HEADLINES),
                                  ("descriptions", MAX_DESCRIPTION, DESCRIPTIONS)):
        texts = ad[name]
        if not isinstance(texts, list) or not lo <= len(texts) <= hi:
            raise SpecError(f"a Responsive Search Ad needs {lo}-{hi} {name}", f"google_{name}")
        if not all(isinstance(t, str) and t.strip() and len(t) <= limit for t in texts):
            raise SpecError(f"a {name[:-1]} is empty or longer than {limit} characters",
                            f"google_{name}")
        if len({t.casefold() for t in texts}) != len(texts):
            raise SpecError(f"duplicate {name}", f"google_{name}")
    if any("!" in h for h in ad["headlines"]):
        raise SpecError("a headline may not hold an exclamation mark", "google_headlines")
    return dict(spec)


def spec_of(payload: Mapping[str, Any], customer_id: str | None = None) -> dict[str, Any]:
    """The signed ``google`` block, accepted only as a valid spec; ``NotApproved`` before
    any call otherwise."""
    try:
        return check_spec(payload.get("google"), customer_id)
    except PausedOnly:
        raise
    except SpecError as exc:
        raise NotApproved(f"google spec refused: {exc}") from None


def create_operations(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The ``mutateOperations`` of ``googleAds:mutate`` that create the campaign, in one
    atomic request with temporary ids (-1 budget, -2 campaign, -3 ad group), from the
    checked spec only. Campaign, ad group and ad carry ``PAUSED`` from the literal."""
    cid = spec["customer_id"]
    budget, campaign, group = (f"customers/{cid}/campaignBudgets/-1",
                               f"customers/{cid}/campaigns/-2", f"customers/{cid}/adGroups/-3")
    c, ag, ad = spec["campaign"], spec["ad_group"], spec["ad"]
    ops: list[dict[str, Any]] = [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget, "name": c["name"] + " · budget",
            "amountMicros": str(c["daily_budget_micros"]), "deliveryMethod": "STANDARD",
            "explicitlyShared": False}}},
        {"campaignOperation": {"create": {
            "resourceName": campaign, "name": c["name"], "status": PAUSED,
            "advertisingChannelType": "SEARCH", "campaignBudget": budget, "targetSpend": {},
            "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                "targetContentNetwork": False,
                                "targetPartnerSearchNetwork": False},
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE",
                                     "negativeGeoTargetType": "PRESENCE"},
            "containsEuPoliticalAdvertising": c["contains_eu_political_advertising"]}}},
    ]
    ops += [{"campaignCriterionOperation": {"create": {
        "campaign": campaign, "location": {"geoTargetConstant": f"geoTargetConstants/{x}"}}}}
        for x in spec["geo"]["criteria"]]
    ops.append({"adGroupOperation": {"create": {
        "resourceName": group, "name": ag["name"], "campaign": campaign, "status": PAUSED,
        "type": "SEARCH_STANDARD"}}})
    ops += [{"adGroupCriterionOperation": {"create": {
        "adGroup": group, "status": ENABLED,
        "keyword": {"text": k["text"], "matchType": k["match_type"]}}}}
        for k in spec["keywords"]]
    ops.append({"adGroupAdOperation": {"create": {
        "adGroup": group, "status": PAUSED, "ad": {
            "finalUrls": [ad["final_url"]],
            "responsiveSearchAd": {"headlines": [{"text": t} for t in ad["headlines"]],
                                   "descriptions": [{"text": t} for t in ad["descriptions"]]}}}}})
    return ops


def names_of(responses: list[Mapping[str, Any]]) -> dict[str, str]:
    """Resource names from ``mutateOperationResponses`` (campaign, budget, ad group, ad)."""
    keys = {"campaignBudgetResult": "budget", "campaignResult": "campaign",
            "adGroupResult": "ad_group", "adGroupAdResult": "ad_group_ad"}
    out: dict[str, str] = {}
    for r in responses:
        for key, name in keys.items():
            res = r.get(key)
            if isinstance(res, Mapping) and isinstance(res.get("resourceName"), str):
                out[name] = res["resourceName"]
    if set(out) != set(keys.values()):
        raise AdapterError("Google Ads returned no resource names", code="bad_response")
    return out


def activation_names(payload: Mapping[str, Any], customer_id: str) -> dict[str, str]:
    """The three resource names a signed ``google.activate`` names; each must belong to
    ``customer_id`` and be well-formed, else ``NotApproved``."""
    if payload.get("customer_id") != customer_id:
        raise NotApproved("the activation names another Google Ads account")
    out: dict[str, str] = {}
    for key, pattern in _NAME.items():
        value = payload.get(key)
        if not isinstance(value, str) or not re.fullmatch(
                rf"customers/{customer_id}/{pattern}", value):
            raise NotApproved(f"the signed activation names no valid {key}")
        out[key] = value
    return out


def activation_operations(names: Mapping[str, str]) -> list[dict[str, Any]]:
    """Enable the ad, then the ad group, then the campaign (the parent last), in one
    request: ``status`` is the only field in the update mask."""
    return [
        {"adGroupAdOperation": {"update": {"resourceName": names["ad_group_ad"],
                                           "status": ENABLED}, "updateMask": "status"}},
        {"adGroupOperation": {"update": {"resourceName": names["ad_group"],
                                         "status": ENABLED}, "updateMask": "status"}},
        {"campaignOperation": {"update": {"resourceName": names["campaign"],
                                          "status": ENABLED}, "updateMask": "status"}},
    ]


GAQL: Final = ("SELECT segments.date, metrics.impressions, metrics.clicks, "
               "metrics.cost_micros, metrics.conversions, metrics.ctr FROM campaign "
               "WHERE campaign.resource_name = '{name}' AND segments.date DURING LAST_7_DAYS "
               "ORDER BY segments.date")


def normalize_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """GAQL result rows → per-day numbers (cost in whole currency units)."""
    out: list[dict[str, Any]] = []
    for r in results:
        m = r.get("metrics") if isinstance(r.get("metrics"), Mapping) else {}
        s = r.get("segments") if isinstance(r.get("segments"), Mapping) else {}
        assert isinstance(m, Mapping) and isinstance(s, Mapping)

        def num(v: Any) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        out.append({"date": str(s.get("date", "")), "impressions": int(num(m.get("impressions"))),
                    "clicks": int(num(m.get("clicks"))),
                    "cost": round(num(m.get("costMicros")) / 1_000_000, 2),
                    "conversions": round(num(m.get("conversions")), 2),
                    "ctr": round(num(m.get("ctr")) * 100, 2)})
    return out


class Adapter(Protocol):
    name: str
    customer_id: str

    def account_info(self) -> dict[str, str]:
        """The customer's ``currency`` and ``time_zone`` (budgets are in that currency)."""

    def create_search_campaign(self, payload: Mapping[str, Any], approval: Verified, *,
                               validate_only: bool = False) -> dict[str, str]:
        """``googleAds:mutate`` with the signed spec; ``validate_only`` is the dry run
        (nothing is created). Returns the created resource names."""

    def activate(self, payload: Mapping[str, Any], approval: Verified) -> None:
        """Enable ad, ad group and campaign named by the signed ``google.activate``."""

    def insights(self, campaign: str) -> list[dict[str, Any]]:
        """Daily numbers of the last 7 days (``googleAds:search``)."""


def make(settings: Any, **kwargs: Any) -> Adapter:
    """The configured adapter: ``rest`` only with ``GOOGLE_ADS_ADAPTER=rest``. Pass
    ``conn=`` to bind writes to claimed executions (see ``meta.adapter.require``)."""
    if settings.google_adapter == "rest":
        from studio.google.rest import RestAdapter
        return RestAdapter.from_settings(settings, **kwargs)
    from studio.google.mock import MockAdapter
    currency = settings.google_account_currency or getattr(settings, "account_currency", "GBP")
    return MockAdapter(currency=currency, **kwargs)
