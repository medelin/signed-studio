"""Campaign objectives: code, never a model, maps the owner's goal to Meta's delivery fields.

Meta has six outcome-driven (ODAX) campaign objectives. Each one, plus the owner's
sub-choice where Meta offers one, maps to exactly one row of ``TABLE``: the campaign
``objective``, the ad set's ``optimization_goal``, ``billing_event``,
``destination_type`` and ``promoted_object``, the creative kind and call to action, the
ids the owner must supply, and the insights action that counts as a result.

``delivery`` turns a row into the ``delivery`` block of a ``campaign.create`` payload
(signed with the rest of the payload, and by hash on its own as ``spec_sha256``);
``check`` is what both adapters run before any Meta call: a delivery block that is not a
row of this table, with the ids filled in, is refused.

Sources (Marketing API v25.0, read 2026-10-28):

- ODAX mapping table (old objective → new objective, destination type, optimization goal,
  promoted object): https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group
  (section "Outcome-Driven Ads Experiences mapping")
- Ad set fields (``optimization_goal``, ``billing_event``, ``destination_type``,
  ``promoted_object``, ``attribution_spec``):
  https://developers.facebook.com/docs/marketing-api/reference/ad-campaign
- Lead ads (``destination_type=ON_AD``, ``LEAD_GENERATION``, ``promoted_object`` = page):
  https://developers.facebook.com/docs/marketing-api/guides/lead-ads/create
- Dynamic creative needs ``OFFSITE_CONVERSIONS`` (sales, leads, traffic, engagement) or an
  app-install ad set:
  https://developers.facebook.com/docs/marketing-api/ad-creative/asset-feed-spec/dynamic-creative/
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

GOALS: tuple[str, ...] = ("awareness", "traffic", "engagement", "leads", "app_promotion",
                          "sales")
LEAD_EVENTS = ("LEAD", "COMPLETE_REGISTRATION", "CONTACT", "SUBMIT_APPLICATION", "SCHEDULE")
SALES_EVENTS = ("PURCHASE", "ADD_TO_CART", "INITIATED_CHECKOUT")
LEAD_FIELDS = ("FULL_NAME", "EMAIL", "PHONE", "CITY", "COMPANY_NAME")
DEFAULT_LEAD_FIELDS = ("FULL_NAME", "EMAIL")
ODAX_SOURCE = "https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group"
LEAD_SOURCE = "https://developers.facebook.com/docs/marketing-api/guides/lead-ads/create"
_NUMERIC_ID = re.compile(r"\d{1,30}")
# Click-through 7 days, view-through 1 day: Meta's default attribution for conversion
# optimisation, set explicitly so the signed payload says what results count
# (``attribution_spec``, https://developers.facebook.com/docs/marketing-api/reference/ad-campaign).
ATTRIBUTION = ({"event_type": "CLICK_THROUGH", "window_days": 7},
               {"event_type": "VIEW_THROUGH", "window_days": 1})
STORE_HOSTS = ("apps.apple.com", "itunes.apple.com", "play.google.com")


@dataclass(frozen=True)
class Row:
    goal: str
    sub: str
    objective: str
    optimization_goal: str
    destination_type: str  # "" = the field is not sent
    promoted: tuple[str, ...]  # promoted_object fields of the ad set
    creative: str  # link | lead_form | message | app | event | catalog
    cta: str  # "" = the copywriter's call to action; else fixed by the destination
    needs: tuple[str, ...]  # Brief v1 ``promotion`` fields the owner must supply
    result: str  # insights field or action type that counts as one result
    conversion: bool = False  # optimises for a pixel or app event: attribution_spec is set
    campaign_promoted: tuple[str, ...] = ()  # promoted_object of the campaign (catalog)
    event: str = ""  # custom_event_type default ("" = none)
    billing_event: str = "IMPRESSIONS"
    note: str = ""


def _row(goal: str, sub: str, objective: str, opt: str, dest: str,
         promoted: tuple[str, ...], creative: str, cta: str, needs: tuple[str, ...],
         result: str, **kw: Any) -> tuple[tuple[str, str], Row]:
    return (goal, sub), Row(goal, sub, objective, opt, dest, promoted, creative, cta, needs,
                            result, **kw)


# (goal, sub-choice) → row. The first sub-choice of a goal is its default.
TABLE: dict[tuple[str, str], Row] = dict([
    _row("awareness", "reach", "OUTCOME_AWARENESS", "REACH", "", ("page_id",), "link", "",
         (), "reach"),
    _row("awareness", "ad_recall_lift", "OUTCOME_AWARENESS", "AD_RECALL_LIFT", "",
         ("page_id",), "link", "", (), "estimated_ad_recallers"),
    _row("traffic", "link_clicks", "OUTCOME_TRAFFIC", "LINK_CLICKS", "WEBSITE", (), "link",
         "", (), "link_click"),
    _row("traffic", "landing_page_views", "OUTCOME_TRAFFIC", "LANDING_PAGE_VIEWS", "WEBSITE",
         (), "link", "", (), "landing_page_view"),
    _row("engagement", "post_engagement", "OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT", "ON_POST",
         (), "link", "", (), "post_engagement"),
    _row("engagement", "messenger", "OUTCOME_ENGAGEMENT", "CONVERSATIONS", "MESSENGER",
         ("page_id",), "message", "MESSAGE_PAGE", (),
         "onsite_conversion.messaging_conversation_started_7d"),
    _row("engagement", "instagram_direct", "OUTCOME_ENGAGEMENT", "CONVERSATIONS",
         "INSTAGRAM_DIRECT", ("page_id",), "message", "INSTAGRAM_MESSAGE", (),
         "onsite_conversion.messaging_conversation_started_7d"),
    _row("engagement", "whatsapp", "OUTCOME_ENGAGEMENT", "CONVERSATIONS", "WHATSAPP",
         ("page_id",), "message", "WHATSAPP_MESSAGE", (),
         "onsite_conversion.messaging_conversation_started_7d",
         note="the Page needs a connected WhatsApp Business number"),
    _row("engagement", "event_responses", "OUTCOME_ENGAGEMENT", "EVENT_RESPONSES", "ON_EVENT",
         (), "event", "", ("event_id",), "rsvp"),
    _row("leads", "instant_form", "OUTCOME_LEADS", "LEAD_GENERATION", "ON_AD", ("page_id",),
         "lead_form", "SIGN_UP", ("privacy_policy_url",), "lead"),
    _row("leads", "website", "OUTCOME_LEADS", "OFFSITE_CONVERSIONS", "WEBSITE",
         ("pixel_id", "custom_event_type"), "link", "", ("pixel_id",),
         "offsite_conversion.fb_pixel_lead", conversion=True, event="LEAD"),
    _row("app_promotion", "app_installs", "OUTCOME_APP_PROMOTION", "APP_INSTALLS", "",
         ("application_id", "object_store_url"), "app", "INSTALL_MOBILE_APP",
         ("application_id", "object_store_url"), "mobile_app_install", conversion=True),
    _row("sales", "website", "OUTCOME_SALES", "OFFSITE_CONVERSIONS", "WEBSITE",
         ("pixel_id", "custom_event_type"), "link", "", ("pixel_id",),
         "offsite_conversion.fb_pixel_purchase", conversion=True, event="PURCHASE"),
    _row("sales", "catalog", "OUTCOME_SALES", "OFFSITE_CONVERSIONS", "WEBSITE",
         ("pixel_id", "custom_event_type", "product_set_id"), "catalog", "",
         ("pixel_id", "product_catalog_id", "product_set_id"),
         "offsite_conversion.fb_pixel_purchase", conversion=True, event="PURCHASE",
         campaign_promoted=("product_catalog_id",)),
])
SUBS: dict[str, tuple[str, ...]] = {
    g: tuple(sub for (goal, sub) in TABLE if goal == g) for g in GOALS}
EVENTS = {"leads": LEAD_EVENTS, "sales": SALES_EVENTS}
# Optimisation goals that support dynamic creative (asset_feed_spec, one ad per ad set).
DYNAMIC_CREATIVE_GOALS = frozenset({"OFFSITE_CONVERSIONS", "APP_INSTALLS"})
OBJECTIVES = frozenset(r.objective for r in TABLE.values())


class DeliveryError(ValueError):
    """A delivery block that is not a valid row of ``TABLE``."""


def row(goal: str, sub: str = "") -> Row:
    """The row for ``goal`` and ``sub`` ("" = the goal's default sub-choice)."""
    if goal not in SUBS:
        raise DeliveryError(f"unknown goal {goal!r}")
    key = (goal, sub or SUBS[goal][0])
    if key not in TABLE:
        raise DeliveryError(f"{sub!r} is not a sub-choice of {goal!r}")
    return TABLE[key]


def _https(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme == "https" and bool(parts.hostname) and "@" not in parts.netloc
            and len(url) <= 1000 and not any(c.isspace() or ord(c) < 0x20 for c in url))


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def id_problems(goal: str, sub: str, promotion: Mapping[str, Any]) -> dict[str, str]:
    """``{"promotion.<field>": code}``: a required id missing (``required_for_objective``)
    or malformed (``bad_id``, ``bad_url``, ``bad_store_url``, ``bad_event``). Ids are never
    guessed: a missing one is an error the owner fixes, not a default."""
    out: dict[str, str] = {}
    if goal not in SUBS:
        return {"objective.goal": "invalid"}
    if sub and sub not in SUBS[goal]:
        return {"objective.sub": "bad_sub"}
    r = row(goal, sub)
    for name in ("pixel_id", "application_id", "event_id", "product_catalog_id",
                 "product_set_id"):
        value = str(promotion.get(name) or "")
        if value and not _NUMERIC_ID.fullmatch(value):
            out[f"promotion.{name}"] = "bad_id"
    store = str(promotion.get("object_store_url") or "")
    if store and not (_https(store) and _host(store) in STORE_HOSTS):
        out["promotion.object_store_url"] = "bad_store_url"
    privacy = str(promotion.get("privacy_policy_url") or "")
    if privacy and not _https(privacy):
        out["promotion.privacy_policy_url"] = "bad_url"
    event = str(promotion.get("custom_event_type") or "")
    if event and event not in EVENTS.get(goal, ()):
        out["promotion.custom_event_type"] = "bad_event"
    for name in r.needs:
        if not str(promotion.get(name) or "").strip():
            out.setdefault(f"promotion.{name}", "required_for_objective")
    fields = promotion.get("lead_fields") or []
    if any(f not in LEAD_FIELDS for f in fields) or len(set(fields)) != len(fields):
        out["promotion.lead_fields"] = "bad_lead_fields"
    return out


def delivery(goal: str = "traffic", sub: str = "", *, page_id: str,
             promotion: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The ``delivery`` block of a ``campaign.create`` payload: every field the adapters
    send for the campaign's objective, ad set and creative kind. ``DeliveryError`` when an
    id the row needs is missing or malformed (nothing is filled in by guess)."""
    p = dict(promotion or {})
    problems = id_problems(goal, sub, p)
    if problems:
        raise DeliveryError("delivery needs: " + ", ".join(sorted(problems)))
    if not page_id:
        raise DeliveryError("delivery needs the Facebook Page id")
    return _build(row(goal, sub), page_id, p)


def _build(r: Row, page_id: str, p: Mapping[str, Any]) -> dict[str, Any]:
    values = {"page_id": page_id, "pixel_id": str(p.get("pixel_id") or ""),
              "custom_event_type": str(p.get("custom_event_type") or r.event),
              "application_id": str(p.get("application_id") or ""),
              "object_store_url": str(p.get("object_store_url") or ""),
              "product_set_id": str(p.get("product_set_id") or ""),
              "product_catalog_id": str(p.get("product_catalog_id") or "")}
    out: dict[str, Any] = {
        "goal": r.goal, "sub": r.sub, "objective": r.objective,
        "optimization_goal": r.optimization_goal, "billing_event": r.billing_event,
        "destination_type": r.destination_type,
        "promoted_object": {k: values[k] for k in r.promoted},
        "creative": r.creative, "cta": r.cta, "result": r.result,
        "dynamic_creative": r.optimization_goal in DYNAMIC_CREATIVE_GOALS,
    }
    if r.campaign_promoted:
        out["campaign_promoted_object"] = {k: values[k] for k in r.campaign_promoted}
    if r.conversion:
        out["attribution_spec"] = [dict(a) for a in ATTRIBUTION]
    if r.creative == "event":
        out["event_id"] = str(p.get("event_id") or "")
    if r.creative == "lead_form":
        out["lead_fields"] = list(p.get("lead_fields") or DEFAULT_LEAD_FIELDS)
    return out


def check(spec: Any, page_id: str | None = None) -> dict[str, Any]:
    """The adapters' gate: ``spec`` must be exactly what ``delivery`` makes for its own
    goal, sub-choice and ids (so a hand-edited objective, optimisation goal, destination
    or promoted object never reaches Meta), every id it carries must be well-formed and
    present, and, when ``page_id`` is given, it must promote that Page."""
    if not isinstance(spec, Mapping):
        raise DeliveryError("the signed payload has no delivery block")
    r = row(str(spec.get("goal", "")), str(spec.get("sub", "")))
    if spec.get("sub") != r.sub:
        raise DeliveryError("the delivery block names no sub-choice")
    promoted = spec.get("promoted_object")
    campaign = spec.get("campaign_promoted_object") or {}
    if not isinstance(promoted, Mapping) or not isinstance(campaign, Mapping):
        raise DeliveryError("promoted_object must be an object")
    ids = {**promoted, **campaign, "event_id": spec.get("event_id", ""),
           "lead_fields": spec.get("lead_fields") or []}
    problems = {k: v for k, v in id_problems(r.goal, r.sub, ids).items()
                if k != "promotion.privacy_policy_url"}  # the form block carries it
    empty = [k for k in (*r.promoted, *r.campaign_promoted)
             if not str(ids.get(k) or "").strip()]
    if problems or empty:
        raise DeliveryError("delivery ids missing or malformed: "
                            + ", ".join(sorted([*problems, *empty])))
    page = str(promoted.get("page_id") or page_id or "")
    if dict(spec) != _build(r, page, ids):
        raise DeliveryError("the delivery block is not a valid objective mapping")
    if page_id is not None and "page_id" in promoted and promoted["page_id"] != page_id:
        raise DeliveryError("the delivery promotes another Page than this studio's")
    return dict(spec)


def label(spec: Mapping[str, Any]) -> str:
    """``OUTCOME_SALES · OFFSITE_CONVERSIONS · WEBSITE`` for screens and campaign rows."""
    parts = [str(spec.get("objective", "")), str(spec.get("optimization_goal", ""))]
    if spec.get("destination_type"):
        parts.append(str(spec["destination_type"]))
    return " · ".join(parts)
