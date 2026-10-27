"""Mock Meta adapter: same contract as ``graph``, nothing leaves the process.

Used by tests, the judge sandbox and the demo (``META_ADAPTER=mock``, the default). It
keeps the objects it "created" in memory, asserts ``PAUSED`` on each, and records every
call in ``calls`` so tests can prove that a refused write made no adapter call.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from studio.approvals import Verified
from studio.meta.adapter import (
    ACTIVATE,
    CREATE,
    HIDE,
    PAUSED,
    REPLY,
    AdapterError,
    NotApproved,
    Preview,
    RemoteComment,
    adset_targeting,
    campaign_name,
    check_paused,
    require,
)

INSIGHTS = (Path(__file__).resolve().parents[2] / "fixtures" / "meta"
            / "insights.SYNTHETIC.json")


def _id(prefix: str) -> str:
    return f"mock-{prefix}-{secrets.token_hex(5)}"


class MockAdapter:
    name = "mock"

    def __init__(self, comments: list[RemoteComment] | None = None) -> None:
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []  # (method, object id)
        self.replies: dict[str, list[str]] = {}
        self.hidden: set[str] = set()
        self._comments = list(comments or [])

    def _create(self, method: str, prefix: str, payload: Mapping[str, Any],
                approval: Verified, obj: dict[str, Any]) -> str:
        require(approval, CREATE, payload)
        check_paused(payload)
        if "status" in obj and obj["status"] != PAUSED:
            raise AssertionError("mock adapter: creation must be PAUSED")
        oid = _id(prefix)
        self.objects[oid] = obj
        self.calls.append((method, oid))
        return oid

    def upload_image(self, png: bytes, payload: Mapping[str, Any],
                     approval: Verified) -> str:
        require(approval, CREATE, payload)
        digest = hashlib.sha256(png).hexdigest()
        if digest not in {i.get("sha256") for i in payload.get("images", [])}:
            raise NotApproved("image is not one of the approved images")
        image_hash = "mockhash" + digest[:24]
        self.objects[image_hash] = {"type": "image", "sha256": digest}
        self.calls.append(("upload_image", image_hash))
        return image_hash

    def create_campaign_paused(self, payload: Mapping[str, Any], approval: Verified) -> str:
        return self._create("create_campaign_paused", "cmp", payload, approval, {
            "type": "campaign", "name": campaign_name(payload),
            "objective": payload["campaign"]["objective"], "status": PAUSED,
            "special_ad_categories": []})

    def create_adset_paused(self, campaign_id: str, payload: Mapping[str, Any],
                            approval: Verified) -> str:
        if self.objects.get(campaign_id, {}).get("type") != "campaign":
            raise AdapterError("unknown campaign", code="not_found")
        return self._create("create_adset_paused", "adset", payload, approval, {
            "type": "adset", "campaign_id": campaign_id, "status": PAUSED,
            "daily_budget": payload["campaign"]["daily_budget_minor"],
            "targeting": adset_targeting(payload), "billing_event": "IMPRESSIONS",
            "optimization_goal": "LINK_CLICKS"})

    def create_creative(self, image_hash: str, payload: Mapping[str, Any],
                        approval: Verified) -> str:
        ad = payload["ad"]
        return self._create("create_creative", "creative", payload, approval, {
            "type": "creative", "image_hash": image_hash, "message": ad["primary_text"],
            "name": ad["headline"], "description": ad["description"], "cta": ad["cta"],
            "link": payload.get("link", "")})

    def create_ad_paused(self, adset_id: str, creative_id: str, payload: Mapping[str, Any],
                         approval: Verified) -> str:
        return self._create("create_ad_paused", "ad", payload, approval, {
            "type": "ad", "adset_id": adset_id, "creative_id": creative_id, "status": PAUSED})

    def preview(self, ad_id: str) -> Preview:
        ad = self.objects.get(ad_id)
        if not ad or ad.get("type") != "ad":
            raise AdapterError("unknown ad", code="not_found")
        creative = self.objects[ad["creative_id"]]
        self.calls.append(("preview", ad_id))
        return Preview("inline", fields={k: creative[k] for k in
                                         ("message", "name", "description", "cta", "link")})

    def activate(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, ACTIVATE, payload)
        campaign_id = str(payload.get("external_id", ""))
        obj = self.objects.get(campaign_id)
        if not obj or obj.get("type") != "campaign":
            raise AdapterError("unknown campaign", code="not_found")
        obj["status"] = "ACTIVE"
        self.calls.append(("activate", campaign_id))

    def list_comments(self, object_id: str, since: float = 0.0) -> list[RemoteComment]:
        self.calls.append(("list_comments", object_id))
        return [c for c in self._comments if c.object_id == object_id and c.created_ts > since]

    def reply(self, payload: Mapping[str, Any], approval: Verified) -> str:
        require(approval, REPLY, payload)
        target = str(payload.get("external_id") or payload["comment_id"])
        reply_id = _id("reply")
        self.replies.setdefault(target, []).append(str(payload["message"]))
        self.calls.append(("reply", target))
        return reply_id

    def hide(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, HIDE, payload)
        target = str(payload.get("external_id") or payload["comment_id"])
        self.hidden.add(target)
        self.calls.append(("hide", target))

    def insights(self, campaign_id: str = "") -> dict[str, Any]:
        self.calls.append(("insights", campaign_id))
        return json.loads(INSIGHTS.read_text())  # type: ignore[no-any-return]
