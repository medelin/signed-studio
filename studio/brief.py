"""Brief v1 (``brief/v1``): the structured brief the owner confirms before a run.

The producer drafts it from free text or an edited voice transcript; the owner answers or
strikes every open ``[FACT]`` question and confirms. Only then does the run start, and the
copywriter may use only ``confirmed_facts``. The confirmed brief is hashed
(``brief_sha256`` over canonical JSON, the same canonical form as receipts) into a
``brief.confirmed`` receipt, and a ``campaign.create`` approval signs that hash too, so a
fact changed after signing invalidates the approval (``receipts.verify`` checks it).

- ``BriefV1`` is a strict pydantic model; the same model is the producer's strict JSON
  schema, so it carries no length constraints (they are in ``problems``).
- ``ground_draft`` is the code net under the model: a draft fact (or offer, or
  proposition) holding a number the owner never wrote becomes an open question.
- ``ungrounded_numbers`` / ``grounding_net`` (rule P10, ``reviewer``) do the same for ad
  copy against everything the owner confirmed.

The advertiser is global: a person or a company, a display name, countries and a landing
page. No national registry ids.

The objective is one of Meta's six campaign objectives plus a sub-choice
(``objective.sub``); ``promotion`` holds the ids that objective needs (pixel, app, event,
catalog, the owner's privacy-policy page for an instant form). ``studio.meta.objectives``
maps them to Meta's fields in code; a missing id is a field error, never a guess.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from studio.google.adapter import ISO_NUMERIC
from studio.meta import objectives
from studio.receipts import canonical, sha256_hex

SCHEMA = "brief/v1"
FACT = "[FACT]"
MAX_TEXT = 300
MAX_FACT = 200
MAX_ITEMS = 12
MAX_FACTS = 30
MAX_QUESTIONS = 12
MAX_SOURCE = 4000  # the owner's free text or edited transcript a draft starts from

Placement = Literal["feed_1x1", "feed_4x5", "story_9x16"]
Goal = Literal["awareness", "traffic", "engagement", "leads", "app_promotion", "sales"]
GOALS: tuple[str, ...] = objectives.GOALS
# "" = the goal's default sub-choice (``objectives.SUBS``)
Sub = Literal["", "reach", "ad_recall_lift", "link_clicks", "landing_page_views",
              "post_engagement", "messenger", "instagram_direct", "whatsapp",
              "event_responses", "instant_form", "website", "app_installs", "catalog"]
LeadField = Literal["FULL_NAME", "EMAIL", "PHONE", "CITY", "COMPANY_NAME"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Advertiser(_Strict):
    kind: Literal["person", "company"]
    display_name: str
    countries: list[str]  # ISO 3166-1 alpha-2, where the advertiser sells
    landing_url: str


class Reason(_Strict):
    text: str
    facts: list[int]  # indices into ``confirmed_facts``; every reason rests on a fact


class Offer(_Strict):
    product: str
    offer: str
    valid_until: str  # YYYY-MM-DD or ""
    reasons_to_believe: list[Reason]


class Objective(_Strict):
    goal: Goal  # exactly one Meta objective
    kpi: str
    sub: Sub = ""  # Meta's sub-choice for the goal (optimisation / destination)


class Promotion(_Strict):
    """Ids the chosen objective needs, as the owner gives them (``objectives.id_problems``
    says which are required). Never filled in by a model or by default."""

    pixel_id: str = ""
    custom_event_type: str = ""  # "" = LEAD for leads, PURCHASE for sales
    application_id: str = ""
    object_store_url: str = ""
    event_id: str = ""
    product_catalog_id: str = ""
    product_set_id: str = ""
    privacy_policy_url: str = ""  # the owner's own page; required by Meta for an instant form
    lead_fields: list[LeadField] = Field(default_factory=list)  # [] = full name and e-mail


class Insight(_Strict):
    who: str
    belief: str
    barrier: str
    trigger: str


class Audience(_Strict):
    locations: list[str]
    age_min: int
    age_max: int
    languages: list[str]
    insight: Insight
    # Advantage+ audience (default): age range, genders and interests are suggestions
    advantage_audience: bool = True
    genders: list[Literal["male", "female"]] = Field(default_factory=list)  # [] = all
    interests: list[str] = Field(default_factory=list)  # hints, resolved to ids by code
    excluded_interests: list[str] = Field(default_factory=list)
    excluded_countries: list[str] = Field(default_factory=list)  # ISO 3166-1 alpha-2
    # only ids the owner gives; the studio never uploads user data
    custom_audience_ids: list[str] = Field(default_factory=list)
    excluded_custom_audience_ids: list[str] = Field(default_factory=list)
    lookalike_source_id: str = ""  # the owner's own audience to build a lookalike from
    lookalike_percent: int = 1  # 1-10 % of the first country


class ToneBrand(_Strict):
    tone: list[str]
    colours: list[str]
    donts: list[str]


class Mandatories(_Strict):
    disclaimers: list[str]
    banned_claims: list[str]
    dates: list[str]


class Budget(_Strict):
    kind: Literal["daily", "lifetime"]
    amount: float  # 0 = not stated yet
    currency: str
    start: str  # YYYY-MM-DD or ""
    end: str


class Placements(_Strict):
    automatic: bool = True  # Advantage+ placements unless the owner asks otherwise
    deliverables: list[Placement]
    carousel_cards: int = 0  # 0 = no carousel ad; else 2-10 cards rendered by Muse Image
    video: bool = False  # a 9:16 video ad cut from the rendered images
    copy_variants: int = 1  # 1-3 copy variants run against each other (A/B)


class Channels(_Strict):
    """Where else the ad runs. Meta is always the primary channel; Google Search is an
    optional second one with its own daily budget (whole units of the Google account's
    currency): one PAUSED Search campaign made from this same brief."""

    google_search: bool = False
    google_daily_budget: float = 0


class Asset(_Strict):
    kind: Literal["product_photo", "logo", "other"]
    note: str


class FactQuestion(_Strict):
    question: str
    claim: str  # the unconfirmed words from the owner's input ("" if a fact is missing)


class BriefV1(_Strict):
    advertiser: Advertiser
    confirmed_facts: list[str]
    offer: Offer
    objective: Objective
    audience: Audience
    proposition: str
    tone_brand: ToneBrand
    mandatories: Mandatories
    budget: Budget
    placements: Placements
    assets: list[Asset]
    open_questions: list[FactQuestion]
    promotion: Promotion = Field(default_factory=Promotion)
    channels: Channels = Field(default_factory=Channels)


class Resolution(_Strict):
    """The owner's answer to one open question: a fact in their words, or the claim
    struck (it will not be used)."""

    action: Literal["answer", "strike"]
    text: str = ""


# -- hashing ---------------------------------------------------------------------------


def as_json(brief: BriefV1) -> dict[str, Any]:
    """The hashed form: ``{"schema": "brief/v1", ...fields}``."""
    return {"schema": SCHEMA, **brief.model_dump(mode="json")}


def brief_sha256(brief: BriefV1) -> str:
    """SHA-256 over canonical JSON (sorted keys, no whitespace, UTF-8) of ``as_json``."""
    return sha256_hex(canonical(as_json(brief)))


# -- resolving [FACT] questions ---------------------------------------------------------


def resolve(brief: BriefV1, resolutions: list[Resolution]) -> tuple[BriefV1, int, int]:
    """Apply one resolution per open question, in order: an answer becomes a confirmed
    fact (the owner's words), a strike drops the claim. Returns the brief with no open
    questions and the (answered, struck) counts. ``ValueError`` on a count mismatch."""
    if len(resolutions) != len(brief.open_questions):
        raise ValueError("one resolution per open question")
    facts = list(brief.confirmed_facts)
    answered = struck = 0
    for r in resolutions:
        if r.action == "answer":
            facts.append(r.text.strip())
            answered += 1
        else:
            struck += 1
    return brief.model_copy(update={"confirmed_facts": facts, "open_questions": []}), \
        answered, struck


# -- validation (field errors as codes; the UI translates them) -------------------------

_ISO2 = re.compile(r"[A-Z]{2}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_ID = re.compile(r"\d{1,30}")
_SENTENCE_END = re.compile(r"[.!?…](?=\s+\S)")


def _date(value: str) -> dt.date | None:
    if not _DATE.fullmatch(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _https(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    return (parts.scheme == "https" and bool(host) and "@" not in parts.netloc
            and len(url) <= 1000 and not any(c.isspace() or ord(c) < 0x20 for c in url))


def problems(brief: BriefV1, *, confirmed: bool = True, currency: str = "PLN",
             ceiling_daily: float | None = None,
             google_currency: str | None = None) -> dict[str, str]:
    """``{field path: error code}`` for a brief the owner wants to confirm; empty when it
    is complete. ``currency`` is the ad account's (Meta books budgets in the account's
    currency); ``ceiling_daily`` the server's daily-budget ceiling; ``google_currency``
    the Google Ads account's, checked when Google Search is a channel (never converted)."""
    out: dict[str, str] = {}

    def text(path: str, value: str, *, required: bool = True, limit: int = MAX_TEXT) -> None:
        if required and not value.strip():
            out[path] = "required"
        elif len(value) > limit:
            out[path] = "too_long"

    def items(path: str, values: list[str], *, required: bool = False,
              limit: int = MAX_ITEMS, each: int = MAX_TEXT) -> None:
        if required and not [v for v in values if v.strip()]:
            out[path] = "required"
        elif len(values) > limit:
            out[path] = "too_many"
        elif any(len(v) > each for v in values):
            out[path] = "too_long"
        elif any(not v.strip() for v in values):
            out[path] = "empty_item"

    a = brief.advertiser
    text("advertiser.display_name", a.display_name, limit=80)
    if not a.countries:
        out["advertiser.countries"] = "required"
    elif len(a.countries) > MAX_ITEMS or not all(_ISO2.fullmatch(c) for c in a.countries):
        out["advertiser.countries"] = "bad_country"
    if not _https(a.landing_url):
        out["advertiser.landing_url"] = "bad_url"
    items("confirmed_facts", brief.confirmed_facts, limit=MAX_FACTS, each=MAX_FACT)
    if any(FACT.lower() in f.lower() for f in brief.confirmed_facts):
        out["confirmed_facts"] = "placeholder"
    o = brief.offer
    text("offer.product", o.product)
    text("offer.offer", o.offer, required=False)
    if o.valid_until and _date(o.valid_until) is None:
        out["offer.valid_until"] = "bad_date"
    if len(o.reasons_to_believe) > MAX_ITEMS:
        out["offer.reasons_to_believe"] = "too_many"
    for i, r in enumerate(o.reasons_to_believe):
        path = f"offer.reasons_to_believe.{i}"
        if not r.text.strip():
            out[path] = "required"
        elif len(r.text) > MAX_TEXT:
            out[path] = "too_long"
        elif not r.facts:
            out[path] = "no_fact_ref"
        elif any(not 0 <= n < len(brief.confirmed_facts) for n in r.facts):
            out[path] = "bad_fact_ref"
    text("objective.kpi", brief.objective.kpi)
    out.update(objectives.id_problems(brief.objective.goal, brief.objective.sub,
                                      brief.promotion.model_dump()))
    au = brief.audience
    items("audience.locations", au.locations, required=True)
    items("audience.languages", au.languages, required=True)
    if not 13 <= au.age_min <= au.age_max <= 65:
        out["audience.age"] = "age_range"
    for name in ("who", "belief", "barrier", "trigger"):
        text(f"audience.insight.{name}", getattr(au.insight, name), required=False)
    if len(set(au.genders)) != len(au.genders):
        out["audience.genders"] = "duplicate"
    items("audience.interests", au.interests, limit=10, each=80)
    items("audience.excluded_interests", au.excluded_interests, limit=10, each=80)
    if len(au.excluded_countries) > MAX_ITEMS or not all(
            _ISO2.fullmatch(c) for c in au.excluded_countries):
        out["audience.excluded_countries"] = "bad_country"
    elif set(au.excluded_countries) & set(a.countries):
        out["audience.excluded_countries"] = "excluded_sold"
    for name in ("custom_audience_ids", "excluded_custom_audience_ids"):
        ids = getattr(au, name)
        if len(ids) > MAX_ITEMS or not all(_ID.fullmatch(x) for x in ids):
            out[f"audience.{name}"] = "bad_id"
    if au.lookalike_source_id and not _ID.fullmatch(au.lookalike_source_id):
        out["audience.lookalike_source_id"] = "bad_id"
    if not 1 <= au.lookalike_percent <= 10:
        out["audience.lookalike_percent"] = "lookalike_percent"
    text("proposition", brief.proposition)
    if "proposition" not in out and _SENTENCE_END.search(brief.proposition.strip()):
        out["proposition"] = "one_sentence"
    items("tone_brand.tone", brief.tone_brand.tone)
    items("tone_brand.colours", brief.tone_brand.colours)
    items("tone_brand.donts", brief.tone_brand.donts)
    items("mandatories.disclaimers", brief.mandatories.disclaimers)
    items("mandatories.banned_claims", brief.mandatories.banned_claims)
    items("mandatories.dates", brief.mandatories.dates)
    b = brief.budget
    if not (b.amount > 0 and b.amount < 10_000_000):
        out["budget.amount"] = "amount"
    elif b.kind == "daily" and ceiling_daily is not None and b.amount > ceiling_daily:
        out["budget.amount"] = "over_ceiling"
    if not _CURRENCY.fullmatch(b.currency):
        out["budget.currency"] = "bad_currency"
    elif b.currency != currency:
        out["budget.currency"] = "account_currency"
    start, end = _date(b.start) if b.start else None, _date(b.end) if b.end else None
    if b.start and start is None:
        out["budget.start"] = "bad_date"
    if b.end and end is None:
        out["budget.end"] = "bad_date"
    if b.kind == "lifetime" and (start is None or end is None):
        out["budget.end"] = "required"
    if start and end and end < start:
        out["budget.end"] = "date_order"
    ch = brief.channels
    if ch.google_search:
        if not (ch.google_daily_budget > 0 and ch.google_daily_budget < 10_000_000):
            out["channels.google_daily_budget"] = "amount"
        elif ceiling_daily is not None and ch.google_daily_budget > ceiling_daily:
            out["channels.google_daily_budget"] = "over_ceiling"
        if google_currency and b.currency != google_currency:
            out["budget.currency"] = "google_account_currency"
        if any(c not in ISO_NUMERIC for c in a.countries):
            out.setdefault("advertiser.countries", "google_geo")
    if not brief.placements.deliverables:
        out["placements.deliverables"] = "required"
    elif len(set(brief.placements.deliverables)) != len(brief.placements.deliverables):
        out["placements.deliverables"] = "duplicate"
    pl = brief.placements
    if pl.carousel_cards and not 2 <= pl.carousel_cards <= 10:
        out["placements.carousel_cards"] = "carousel_cards"
    if not 1 <= pl.copy_variants <= 3:
        out["placements.copy_variants"] = "copy_variants"
    if brief.objective.goal in objectives.SUBS and (
            brief.objective.sub in ("",) + objectives.SUBS[brief.objective.goal]):
        kind = objectives.row(brief.objective.goal, brief.objective.sub).creative
        if kind in ("event", "catalog") and (pl.carousel_cards or pl.video
                                             or pl.copy_variants > 1):
            out["placements.carousel_cards"] = "format_not_for_objective"
    if len(brief.assets) > MAX_ITEMS:
        out["assets"] = "too_many"
    elif any(len(x.note) > MAX_TEXT for x in brief.assets):
        out["assets"] = "too_long"
    if confirmed and brief.open_questions:
        out["open_questions"] = "open_question"
    elif len(brief.open_questions) > MAX_QUESTIONS:
        out["open_questions"] = "too_many"
    return out


def daily_budget(brief: BriefV1) -> int:
    """The daily budget in whole account-currency units the campaign is created with: the
    owner's daily amount, or a lifetime amount spread over its days (rounded up)."""
    b = brief.budget
    if b.kind == "daily":
        return max(1, math.ceil(b.amount))
    start, end = _date(b.start), _date(b.end)
    if start is None or end is None:
        raise ValueError("a lifetime budget needs start and end dates")
    days = (end - start).days + 1
    return max(1, math.ceil(b.amount / days))


# -- grounding (numbers the owner never wrote) ------------------------------------------

_DIGITS = re.compile(r"\d+")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def numbers(text: str) -> set[str]:
    """Digit runs of ``text`` after NFKC (so full-width or superscript digits count),
    leading zeros kept: ``"10:00"`` → ``{"10", "00"}``."""
    return set(_DIGITS.findall(_fold(text)))


def ungrounded_numbers(text: str, corpus: str) -> list[str]:
    """The numbers in ``text`` that never appear in ``corpus``, in order of appearance."""
    known = numbers(corpus)
    seen: list[str] = []
    for n in _DIGITS.findall(_fold(text)):
        if n not in known and n not in seen:
            seen.append(n)
    return seen


CONFIRM = {"en": "Please confirm: {claim}", "pl": "Proszę potwierdzić: {claim}"}


def ground_draft(draft: BriefV1, source: str, lang: str = "en") -> BriefV1:
    """The code net under the producer: a draft fact, product, offer, validity date or
    proposition with a number that is not in the owner's ``source`` text becomes an open
    ``[FACT]`` question; reason references move with the surviving facts."""
    ask = CONFIRM.get(lang, CONFIRM["en"])
    questions = list(draft.open_questions)
    keep: list[str] = []
    index: dict[int, int] = {}
    for i, fact in enumerate(draft.confirmed_facts):
        missing = ungrounded_numbers(fact, source)
        if missing:
            questions.append(FactQuestion(question=ask.format(claim=fact), claim=fact))
        else:
            index[i] = len(keep)
            keep.append(fact)
    offer = draft.offer
    updates: dict[str, str] = {}
    for name in ("product", "offer", "valid_until"):
        value = getattr(offer, name)
        if ungrounded_numbers(value, source):
            questions.append(FactQuestion(question=ask.format(claim=value), claim=value))
            updates[name] = ""
    reasons = [Reason(text=r.text, facts=[index[n] for n in r.facts if n in index])
               for r in offer.reasons_to_believe]
    offer = offer.model_copy(update={**updates, "reasons_to_believe": reasons})
    promotion = ground_ids(draft.promotion, source)
    audience = ground_audience(draft.audience, source)
    placements = ground_formats(draft.placements)
    proposition = draft.proposition
    if ungrounded_numbers(proposition, source):
        questions.append(FactQuestion(question=ask.format(claim=proposition),
                                      claim=proposition))
        proposition = ""
    return draft.model_copy(update={"confirmed_facts": keep, "offer": offer,
                                    "proposition": proposition, "promotion": promotion,
                                    "audience": audience, "placements": placements,
                                    "open_questions": questions[:MAX_QUESTIONS]})


ID_FIELDS = ("pixel_id", "application_id", "object_store_url", "event_id",
             "product_catalog_id", "product_set_id", "privacy_policy_url")


def ground_ids(promotion: Promotion, source: str) -> Promotion:
    """An id or URL in the draft that the owner never wrote is cleared: the field then
    shows as required for the objective, so the owner types it in (never a guess)."""
    folded = _fold(source)
    cleared = {name: "" for name in ID_FIELDS
               if getattr(promotion, name) and getattr(promotion, name) not in folded}
    return promotion.model_copy(update=cleared) if cleared else promotion


def ground_formats(placements: Placements) -> Placements:
    """Format options outside their ranges fall back to the defaults (no carousel, one
    copy variant): a choice the owner did not make is not made for them."""
    update: dict[str, Any] = {}
    if placements.carousel_cards and not 2 <= placements.carousel_cards <= 10:
        update["carousel_cards"] = 0
    if not 1 <= placements.copy_variants <= 3:
        update["copy_variants"] = 1
    return placements.model_copy(update=update) if update else placements


def ground_audience(audience: Audience, source: str) -> Audience:
    """Audience ids the owner never wrote are dropped (custom audiences, the lookalike
    source): they cannot be guessed."""
    folded = _fold(source)
    keep = {name: [x for x in getattr(audience, name) if x in folded]
            for name in ("custom_audience_ids", "excluded_custom_audience_ids")}
    if not 1 <= audience.lookalike_percent <= 10:
        keep["lookalike_percent"] = 1  # type: ignore[assignment]
    if audience.lookalike_source_id and audience.lookalike_source_id not in folded:
        return audience.model_copy(update={**keep, "lookalike_source_id": ""})
    return audience.model_copy(update=keep)


def grounding_corpus(brief: BriefV1) -> str:
    """Everything the owner confirmed that an ad may state: the facts, the offer and its
    reasons, the proposition, the disclaimers and dates, and the display name."""
    o, m = brief.offer, brief.mandatories
    parts = [brief.advertiser.display_name, *brief.confirmed_facts, o.product, o.offer,
             o.valid_until, *(r.text for r in o.reasons_to_believe), brief.proposition,
             *m.disclaimers, *m.dates]
    return "\n".join(parts)


def banned_hits(text: str, brief: BriefV1) -> list[str]:
    """Banned claims from the brief's mandatories that appear in ``text`` (case-folded)."""
    folded = _fold(text).casefold()
    return [b for b in brief.mandatories.banned_claims
            if b.strip() and _fold(b).casefold().strip() in folded]


# -- prompt rendering --------------------------------------------------------------------


def render(brief: BriefV1) -> str:
    """The confirmed brief as plain text for the producer's and copywriter's prompts."""
    a, o, au, b = brief.advertiser, brief.offer, brief.audience, brief.budget
    lines = [
        f"Brief v1 (confirmed by the owner, {SCHEMA})",
        f"Advertiser: {a.kind}, {a.display_name}, countries {', '.join(a.countries)}, "
        f"landing page {a.landing_url}",
        "Confirmed facts (the only facts an ad may state):",
        *[f"  {i + 1}. {f}" for i, f in enumerate(brief.confirmed_facts)],
        f"Product: {o.product}",
        f"Offer: {o.offer or '-'}" + (f" (valid until {o.valid_until})" if o.valid_until
                                      else ""),
        "Reasons to believe: " + ("; ".join(
            f"{r.text} (facts {', '.join(str(n + 1) for n in r.facts)})"
            for r in o.reasons_to_believe) or "-"),
        f"Objective: {brief.objective.goal}"
        + (f" ({brief.objective.sub})" if brief.objective.sub else "")
        + f"; KPI: {brief.objective.kpi}",
        f"Audience: {', '.join(au.locations)}; ages {au.age_min}-{au.age_max}; "
        f"languages {', '.join(au.languages)}",
        f"Insight: who {au.insight.who or '-'}; believes {au.insight.belief or '-'}; "
        f"barrier {au.insight.barrier or '-'}; trigger {au.insight.trigger or '-'}",
        f"Proposition: {brief.proposition}",
        f"Tone: {', '.join(brief.tone_brand.tone) or '-'}; colours "
        f"{', '.join(brief.tone_brand.colours) or '-'}; do not: "
        f"{'; '.join(brief.tone_brand.donts) or '-'}",
        f"Mandatory disclaimers: {'; '.join(brief.mandatories.disclaimers) or '-'}",
        f"Banned claims: {'; '.join(brief.mandatories.banned_claims) or '-'}",
        f"Dates: {'; '.join(brief.mandatories.dates) or '-'}",
        f"Budget: {b.kind} {b.amount:g} {b.currency}"
        + (f", {b.start or '?'} to {b.end or '?'}" if b.start or b.end else ""),
        f"Placements: {'automatic' if brief.placements.automatic else 'manual'}; "
        f"deliverables {', '.join(brief.placements.deliverables)}"
        + (f"; carousel of {brief.placements.carousel_cards} cards"
           if brief.placements.carousel_cards else "")
        + ("; a 9:16 video" if brief.placements.video else "")
        + (f"; {brief.placements.copy_variants} copy variants tested"
           if brief.placements.copy_variants > 1 else ""),
        "Assets: " + ("; ".join(f"{x.kind} ({x.note})" for x in brief.assets) or "-"),
    ]

    return "\n".join(lines)


def counts(brief: BriefV1, answered: int, struck: int) -> dict[str, int]:
    return {"facts": len(brief.confirmed_facts), "open_questions": len(brief.open_questions),
            "answered": answered, "struck": struck}
