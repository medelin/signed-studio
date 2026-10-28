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
- The access token goes only in the ``Authorization`` header, never in a URL.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from studio.approvals import Verified, payload_sha256

PAUSED: Final = "PAUSED"
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


def require(approval: Any, action: str, payload: Mapping[str, Any]) -> Verified:
    """The approval must be a ``Verified`` for this action and exactly this payload."""
    if not isinstance(approval, Verified):
        raise NotApproved(f"{action} needs a verified human approval")
    if approval.action != action:
        raise NotApproved(f"approval is for {approval.action!r}, not {action!r}")
    if approval.content_sha256 != payload_sha256(dict(payload)):
        raise NotApproved("payload differs from the content the human signed")
    if payload.get("action") != action:
        raise NotApproved("payload names another action")
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

    def create_adset_paused(self, campaign_id: str, payload: Mapping[str, Any],
                            approval: Verified) -> str: ...

    def create_creative(self, image_hash: str, payload: Mapping[str, Any],
                        approval: Verified) -> str: ...

    def create_ad_paused(self, adset_id: str, creative_id: str, payload: Mapping[str, Any],
                         approval: Verified) -> str: ...

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


def campaign_name(payload: Mapping[str, Any]) -> str:
    return f"Muse Studio · {payload['campaign'].get('angle', 'campaign')}"[:200]


def adset_targeting(payload: Mapping[str, Any]) -> dict[str, Any]:
    t = payload.get("targeting") or {}
    return {"geo_locations": {"countries": list(t.get("countries") or ["PL"])},
            "age_min": int(t.get("age_min", 25)), "age_max": int(t.get("age_max", 55))}


def make(settings: Any, **kwargs: Any) -> Adapter:
    """The configured adapter: ``graph`` only with ``META_ADAPTER=graph``."""
    if settings.meta_adapter == "graph":
        from studio.meta.graph import GraphAdapter
        return GraphAdapter.from_settings(settings, **kwargs)
    from studio.meta.mock import MockAdapter
    return MockAdapter(**kwargs)
