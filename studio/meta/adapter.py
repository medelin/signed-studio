"""Meta adapter interface: the only code that writes to Facebook/Instagram.

Two implementations: ``mock`` (default; tests, sandbox, judges — nothing leaves the
process) and ``graph`` (Marketing API + Pages API over ``httpx``, operator machine only,
``META_ADAPTER=graph``).

Rules every implementation follows, enforced by ``require`` and ``check_paused`` here:

- Every write takes the approved ``payload`` and the ``Verified`` approval for it, and
  refuses (``NotApproved``) unless ``approval.action`` is the write's action and
  ``approval.content_sha256 == sha256(canonical(payload))``. The values sent come from
  that payload, never from separate arguments, so nothing unsigned can be slipped in.
- Campaign, ad set and ad are created with ``status="PAUSED"``, a literal in the code;
  there is no status parameter, and a payload asking for anything else is refused
  (``PausedOnly``). Activation is a separate write needing a ``campaign.activate``
  approval whose payload names all three objects; it sets ``ACTIVE`` on the ad, then the
  ad set, then the campaign (``ACTIVATION_ORDER``), one call each.
- The access token goes in the POST form body or the ``Authorization`` header, never in
  a URL (``graph.py``).
- The campaign objective, the ad set's optimisation goal, billing event, destination and
  promoted object come only from the payload's signed ``delivery`` block, and only after
  ``objectives.check`` accepted it as a row of the objective table (``delivery_of``);
  ``adset_fields`` and ``creative_spec`` build the same bodies for both adapters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from studio.approvals import Verified, payload_sha256
from studio.meta import objectives
from studio.receipts import canonical, sha256_hex

PAUSED: Final = "PAUSED"
MOCK_PAGE: Final = "mock-page"  # the mock adapter's Page (no Meta Page exists in the sandbox)
CREATE, ACTIVATE, REPLY, HIDE = ("campaign.create", "campaign.activate", "reply.post",
                                 "comment.hide")


# Activation order: the parent last, so nothing can deliver before its children are ready.
ACTIVATION_ORDER: Final = ("ad", "adset", "campaign")
ACTIVATION_KEYS: Final = {"ad": "ad_external_id", "adset": "adset_external_id",
                          "campaign": "external_id"}


class AdapterError(Exception):
    code = "adapter_error"

    def __init__(self, message: str, *, code: str = "", transient: bool = False,
                 graph_code: int = 0) -> None:
        super().__init__(message)
        if code:
            self.code = code
        self.transient = transient
        self.graph_code = graph_code


class NotApproved(AdapterError):
    code = "not_approved"


class PausedOnly(AdapterError):
    code = "paused_only"


def require(approval: Any, action: str, payload: Mapping[str, Any],
            conn: Any = None) -> Verified:
    """The approval must be a ``Verified`` for this action and exactly this payload. With
    ``conn`` (the app's database; adapters made by ``make`` have it), its execution must
    also be claimed and still ``running`` (``executor.authorize`` ran, ``finish`` did not):
    an adapter write cannot happen outside an executor."""
    if not isinstance(approval, Verified):
        raise NotApproved(f"{action} needs a verified human approval")
    if approval.action != action:
        raise NotApproved(f"approval is for {approval.action!r}, not {action!r}")
    if approval.content_sha256 != payload_sha256(dict(payload)):
        raise NotApproved("payload differs from the content the human signed")
    if payload.get("action") != action:
        raise NotApproved("payload names another action")
    if conn is not None:
        with conn.lock:
            row = conn.execute("SELECT status, action FROM executions WHERE approval_id = ?",
                               (approval.pending_id,)).fetchone()
        if row is None or tuple(row) != ("running", action):
            raise NotApproved("no running execution claimed for this approval")
    return approval


def check_paused(payload: Mapping[str, Any]) -> None:
    """Any ``status`` anywhere in a creation payload must be ``PAUSED``."""

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for k, v in value.items():
                if k in ("status", "effective_status", "configured_status") and v != PAUSED:
                    raise PausedOnly(f"objects are created PAUSED only, not {v!r}")
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(payload)


@dataclass(frozen=True)
class Preview:
    """``iframe``: a Meta preview URL (validated host/path); ``inline``: rendered by us."""

    kind: str
    src: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteComment:
    external_id: str
    message: str
    author: str
    created_ts: float
    object_id: str


class Adapter(Protocol):
    name: str

    def upload_image(self, png: bytes, payload: Mapping[str, Any],
                     approval: Verified) -> str: ...

    def create_campaign_paused(self, payload: Mapping[str, Any],
                               approval: Verified) -> str: ...

    def create_lookalike(self, payload: Mapping[str, Any], approval: Verified) -> str:
        """``POST /act_{id}/customaudiences`` (``subtype=LOOKALIKE``) from the owner's
        source audience in the signed payload."""

    def create_adset_paused(self, campaign_id: str, payload: Mapping[str, Any],
                            approval: Verified, *, lookalike_id: str = "") -> str: ...

    def create_creative(self, image_hash: str, payload: Mapping[str, Any],
                        approval: Verified, *, form_id: str = "") -> str: ...

    def create_ad_paused(self, adset_id: str, creative_id: str, payload: Mapping[str, Any],
                         approval: Verified) -> str: ...

    def create_lead_form(self, payload: Mapping[str, Any], approval: Verified) -> str:
        """``POST /{page_id}/leadgen_forms`` with the signed ``lead_form`` block."""

    def list_leads(self, form_id: str, since: float = 0.0) -> list[Any]:
        """``GET /{form_id}/leads`` (``leadads.RemoteLead`` items)."""

    def get_lead(self, lead_id: str) -> Any:
        """``GET /{lead_id}`` (a ``leadads.RemoteLead``), for the leadgen webhook."""

    def preview(self, ad_id: str) -> Preview: ...

    def activate(self, level: str, payload: Mapping[str, Any], approval: Verified) -> None:
        """Set ``ACTIVE`` on one object (``ad``, ``adset`` or ``campaign``) the signed
        ``campaign.activate`` payload names."""

    def list_comments(self, object_id: str, since: float = 0.0) -> list[RemoteComment]: ...

    def reply(self, payload: Mapping[str, Any], approval: Verified) -> str: ...

    def hide(self, payload: Mapping[str, Any], approval: Verified) -> None: ...

    def insights(self, campaign_id: str) -> dict[str, Any]: ...


def activation_target(level: str, payload: Mapping[str, Any]) -> str:
    """The external id of ``level`` in a signed activation payload; refused when absent."""
    if level not in ACTIVATION_KEYS:
        raise NotApproved(f"unknown activation level {level!r}")
    value = payload.get(ACTIVATION_KEYS[level])
    if not isinstance(value, str) or not value:
        raise NotApproved(f"the signed activation names no {level}")
    return value


def delivery_of(payload: Mapping[str, Any], page_id: str | None = None) -> dict[str, Any]:
    """The signed ``delivery`` block, accepted only as a valid objective mapping for this
    Page (``objectives.check``); ``NotApproved`` otherwise, before any call."""
    try:
        return objectives.check(payload.get("delivery"), page_id)
    except objectives.DeliveryError as exc:
        raise NotApproved(f"delivery refused: {exc}") from None


def spec_sha256(spec: Mapping[str, Any]) -> str:
    """Hash of a delivery block (what the approval signs as ``spec_sha256``)."""
    return sha256_hex(canonical(dict(spec)))


def campaign_fields(payload: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    """Campaign body fields from the signed delivery (``status`` is added by the adapter)."""
    out: dict[str, Any] = {"name": campaign_name(payload), "objective": spec["objective"],
                           "special_ad_categories": []}
    if spec.get("campaign_promoted_object"):
        out["promoted_object"] = dict(spec["campaign_promoted_object"])
    return out


def adset_fields(payload: Mapping[str, Any], spec: Mapping[str, Any],
                 lookalike_id: str = "") -> dict[str, Any]:
    """Ad set body fields from the signed delivery: optimisation goal, billing event,
    destination type, promoted object and attribution (Marketing API ad set reference,
    https://developers.facebook.com/docs/marketing-api/reference/ad-campaign)."""
    out: dict[str, Any] = {
        "daily_budget": int(payload["campaign"]["daily_budget_minor"]),
        "billing_event": spec["billing_event"], "optimization_goal": spec["optimization_goal"],
        "targeting": adset_targeting(payload, lookalike_id)}
    if spec.get("destination_type"):
        out["destination_type"] = spec["destination_type"]
    if spec.get("promoted_object"):
        out["promoted_object"] = dict(spec["promoted_object"])
    if spec.get("attribution_spec"):
        out["attribution_spec"] = [dict(a) for a in spec["attribution_spec"]]
    return out


# Call-to-action values per creative kind (link data call_to_action value:
# https://developers.facebook.com/docs/marketing-api/reference/ad-creative-link-data-call-to-action-value/
# — ``app_destination`` for messaging ads, ``application`` + ``link`` for app installs,
# ``lead_gen_form_id`` for instant forms).
APP_DESTINATIONS: Final = {"MESSAGE_PAGE": "MESSENGER", "INSTAGRAM_MESSAGE": "INSTAGRAM_DIRECT",
                           "WHATSAPP_MESSAGE": "WHATSAPP"}
LEAD_FORM_LINK: Final = "https://fb.me/"  # what Meta requires as the link of a lead ad


def creative_spec(payload: Mapping[str, Any], spec: Mapping[str, Any], page_id: str,
                  image_hash: str, *, form_id: str = "") -> dict[str, Any]:
    """The creative body (``object_story_spec`` and, for a catalog, ``product_set_id``)
    for the signed delivery's creative kind."""
    ad, link = payload["ad"], str(payload.get("link", ""))
    kind = spec["creative"]
    data: dict[str, Any] = {"image_hash": image_hash, "link": link,
                            "message": ad["primary_text"], "name": ad["headline"],
                            "description": ad["description"]}
    cta: dict[str, Any] | None = {"type": ad["cta"], "value": {"link": link}}
    if kind == "message":
        cta = {"type": spec["cta"], "value": {"app_destination": APP_DESTINATIONS[spec["cta"]]}}
    elif kind == "app":
        store = spec["promoted_object"]["object_store_url"]
        data["link"] = store
        cta = {"type": spec["cta"], "value": {
            "application": spec["promoted_object"]["application_id"], "link": store}}
    elif kind == "lead_form":
        if not form_id:
            raise NotApproved("an instant-form ad needs the form created first")
        data["link"] = LEAD_FORM_LINK
        cta = {"type": spec["cta"], "value": {"lead_gen_form_id": form_id}}
    elif kind == "event":
        # an event ad links to the Facebook event; Meta shows its own response button
        data["link"] = f"https://www.facebook.com/events/{spec['event_id']}/"
        cta = None
    if cta is not None:
        data["call_to_action"] = cta
    out: dict[str, Any] = {"name": campaign_name(payload) + " · creative"}
    if kind == "catalog":
        # Advantage+ catalog ads: a template creative over the promoted product set
        # (https://developers.facebook.com/docs/marketing-api/advantage-catalog-ads/)
        data.pop("image_hash")
        out["object_story_spec"] = {"page_id": page_id, "template_data": data}
        out["product_set_id"] = spec["promoted_object"]["product_set_id"]
    else:
        out["object_story_spec"] = {"page_id": page_id, "link_data": data}
    return out


def lead_form_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The signed instant-form block; refused unless it names the owner's privacy page."""
    form = payload.get("lead_form")
    if not isinstance(form, Mapping):
        raise NotApproved("the signed payload has no instant form")
    privacy = form.get("privacy_policy")
    if not isinstance(privacy, Mapping) or not str(privacy.get("url", "")).startswith(
            "https://"):
        raise NotApproved("an instant form needs the owner's privacy-policy URL")
    questions = form.get("questions")
    if not isinstance(questions, list) or not questions or not all(
            isinstance(q, Mapping) and q.get("type") in objectives.LEAD_FIELDS
            for q in questions):
        raise NotApproved("instant-form questions must come from the brief's list")
    return {k: form[k] for k in ("name", "questions", "privacy_policy",
                                 "follow_up_action_url") if k in form}


def campaign_name(payload: Mapping[str, Any]) -> str:
    return f"Muse Studio · {payload['campaign'].get('angle', 'campaign')}"[:200]


def adset_targeting(payload: Mapping[str, Any], lookalike_id: str = "") -> dict[str, Any]:
    """The signed targeting spec (``audience.targeting``, built by code from the brief);
    a payload with only countries and ages gets Advantage+ audience with those as its
    controls and suggestions. A lookalike made under this approval joins the custom
    audiences."""
    from studio.meta import audience

    t = dict(payload.get("targeting") or {})
    if "geo_locations" not in t:
        t = audience.targeting(countries=list(t.get("countries") or ["PL"]),
                               age_min=int(t.get("age_min", 25)),
                               age_max=int(t.get("age_max", 55)))
    if lookalike_id:
        t["custom_audiences"] = [*t.get("custom_audiences", []), {"id": lookalike_id}]
    return t


def lookalike_of(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The signed lookalike block: built from the owner's own source audience id only."""
    spec = payload.get("lookalike")
    if not isinstance(spec, Mapping) or spec.get("subtype") != "LOOKALIKE":
        raise NotApproved("the signed payload has no lookalike")
    origin = str(spec.get("origin_audience_id", ""))
    if not origin.isdigit() or len(origin) > 30:
        raise NotApproved("a lookalike needs the owner's source audience id")
    return {k: spec[k] for k in ("name", "subtype", "origin_audience_id", "lookalike_spec")}


def make(settings: Any, **kwargs: Any) -> Adapter:
    """The configured adapter: ``graph`` only with ``META_ADAPTER=graph``. Pass
    ``conn=`` to bind writes to claimed executions (see ``require``)."""
    if settings.meta_adapter == "graph":
        from studio.meta.graph import GraphAdapter
        if getattr(settings, "replay", False) and "transport" not in kwargs:
            from studio.replay import ReplayTransport
            kwargs["transport"] = ReplayTransport.from_set(settings.replay_set)
        return GraphAdapter.from_settings(settings, **kwargs)
    from studio.meta.mock import MockAdapter
    return MockAdapter(**kwargs)
