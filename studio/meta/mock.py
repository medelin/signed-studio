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
from datetime import datetime
from pathlib import Path
from typing import Any

from studio.approvals import Verified
from studio.meta import pixel
from studio.meta.adapter import (
    ACTIVATE,
    CREATE,
    HIDE,
    MOCK_PAGE,
    PAUSED,
    REPLY,
    UPDATE,
    AdapterError,
    AdJob,
    NotApproved,
    Preview,
    RemoteComment,
    activation_target,
    ad_variants,
    adset_fields,
    campaign_fields,
    check_paused,
    creative_spec,
    delivery_of,
    lead_form_of,
    lookalike_of,
    require,
    update_of,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "meta"
INSIGHTS = FIXTURES / "insights.SYNTHETIC.json"
LEADS = FIXTURES / "leads.SYNTHETIC.json"
INSIGHTS_FULL = FIXTURES / "insights_full.SYNTHETIC.json"


def _id(prefix: str) -> str:
    return f"mock-{prefix}-{secrets.token_hex(5)}"


class MockAdapter:
    name = "mock"

    def __init__(self, comments: list[RemoteComment] | None = None, *,
                 conn: Any = None, page_id: str = MOCK_PAGE, currency: str = "PLN") -> None:
        self._conn = conn
        self.page_id = page_id
        self.currency = currency
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []  # (method, object id)
        self.replies: dict[str, list[str]] = {}
        self.hidden: set[str] = set()
        self._comments = list(comments or [])

    def _create(self, method: str, prefix: str, payload: Mapping[str, Any],
                approval: Verified, obj: dict[str, Any]) -> str:
        require(approval, CREATE, payload, self._conn)
        check_paused(payload)
        if "status" in obj and obj["status"] != PAUSED:
            raise AssertionError("mock adapter: creation must be PAUSED")
        oid = _id(prefix)
        self.objects[oid] = obj
        self.calls.append((method, oid))
        return oid

    def upload_image(self, png: bytes, payload: Mapping[str, Any],
                     approval: Verified) -> str:
        require(approval, CREATE, payload, self._conn)
        digest = hashlib.sha256(png).hexdigest()
        if digest not in {i.get("sha256") for i in payload.get("images", [])}:
            raise NotApproved("image is not one of the approved images")
        image_hash = "mockhash" + digest[:24]
        self.objects[image_hash] = {"type": "image", "sha256": digest}
        self.calls.append(("upload_image", image_hash))
        return image_hash

    def account_info(self) -> dict[str, str]:
        return {"currency": self.currency, "timezone_name": "Europe/Warsaw"}

    def pixel_check(self, pixel_id: str, landing_url: str, *,
                    event_type: str = "") -> dict[str, Any]:
        """A deterministic healthy pixel (read-only): found in the ad account, fired
        recently, events in the last 7 days (the campaign's event among them) and its id
        in the landing page. Nothing is fetched."""
        self.calls.append(("pixel_check", pixel_id))
        name = pixel.EVENT_NAMES.get(event_type, "Lead")
        events = {"total": 1240, "by_event": {"PageView": 1180, name: 60}}
        checks = [pixel.entry("pixel_found", "ok", "the pixel exists and is available"),
                  pixel.entry("account_ok", "ok", "the ad account can use the pixel"),
                  *pixel.activity_checks(self.PIXEL_FIRED, events, event_type or "LEAD",
                                         self.PIXEL_NOW),
                  pixel.entry("landing_found", "ok", "the pixel id is in the landing "
                                                     "page's HTML")]
        return pixel.result(pixel_id, checks, last_fired_time=self.PIXEL_FIRED,
                            events=events, landing_url=landing_url)

    PIXEL_FIRED = "2026-10-28T09:00:00+0000"
    PIXEL_NOW = 1793264400.0  # 2026-10-29 09:00 UTC: the mock's clock for the check

    def _delivery(self, payload: Mapping[str, Any], approval: Verified) -> dict[str, Any]:
        require(approval, CREATE, payload, self._conn)
        return delivery_of(payload, self.page_id)

    def create_campaign_paused(self, payload: Mapping[str, Any], approval: Verified) -> str:
        spec = self._delivery(payload, approval)
        return self._create("create_campaign_paused", "cmp", payload, approval, {
            "type": "campaign", **campaign_fields(payload, spec), "status": PAUSED})

    def create_lookalike(self, payload: Mapping[str, Any], approval: Verified) -> str:
        require(approval, CREATE, payload, self._conn)
        spec = lookalike_of(payload)
        oid = "mockaud" + secrets.token_hex(5)
        self.objects[oid] = {"type": "custom_audience", **spec}
        self.calls.append(("create_lookalike", oid))
        return oid

    def search_targeting(self, kind: str, query: str) -> list[dict[str, Any]]:
        """Deterministic stand-in for the targeting search: a numeric id per query."""
        self.calls.append(("search", kind))
        digest = int(hashlib.sha256(f"{kind}:{query.casefold()}".encode()).hexdigest(), 16)
        if kind == "adlocale":
            return [{"key": 1000 + digest % 9000, "name": query.strip()[:40]}]
        return [{"id": "6003" + str(digest % 10**9).zfill(9), "name": query.strip()[:80],
                 "audience_size_lower_bound": 100000 + digest % 900000}]

    def create_adset_paused(self, campaign_id: str, payload: Mapping[str, Any],
                            approval: Verified, *, lookalike_id: str = "") -> str:
        spec = self._delivery(payload, approval)
        if self.objects.get(campaign_id, {}).get("type") != "campaign":
            raise AdapterError("unknown campaign", code="not_found")
        return self._create("create_adset_paused", "adset", payload, approval, {
            "type": "adset", "campaign_id": campaign_id, "status": PAUSED,
            **adset_fields(payload, spec, lookalike_id)})

    def create_creative(self, image_hash: str, payload: Mapping[str, Any],
                        approval: Verified, *, form_id: str = "", job: AdJob | None = None,
                        hashes: Mapping[str, str] | None = None, video_id: str = "") -> str:
        spec = self._delivery(payload, approval)
        body = creative_spec(payload, spec, self.page_id, image_hash, form_id=form_id,
                             job=job, hashes=hashes, video_id=video_id)
        ad = ad_variants(payload)[job.variant if job else 0]
        story = body["object_story_spec"]
        data = story.get("link_data") or story.get("template_data") or {}
        cta = data.get("call_to_action") or (story.get("video_data") or {}).get(
            "call_to_action") or {}
        return self._create("create_creative", "creative", payload, approval, {
            "type": "creative", "image_hash": image_hash, "message": ad["primary_text"],
            "name": ad["headline"], "description": ad["description"],
            "cta": cta.get("type", ""), "link": data.get("link", payload.get("link", "")),
            "body": body, "format": job.fmt if job else "single"})

    def upload_video(self, mp4: bytes, payload: Mapping[str, Any], approval: Verified) -> str:
        require(approval, CREATE, payload, self._conn)
        digest = hashlib.sha256(mp4).hexdigest()
        if digest != ((payload.get("format") or {}).get("video") or {}).get("sha256"):
            raise NotApproved("video is not the approved video")
        oid = "mockvid" + digest[:16]
        self.objects[oid] = {"type": "video", "sha256": digest}
        self.calls.append(("upload_video", oid))
        return oid

    def create_ad_paused(self, adset_id: str, creative_id: str, payload: Mapping[str, Any],
                         approval: Verified, *, key: str = "ad") -> str:
        return self._create("create_ad_paused", "ad", payload, approval, {
            "type": "ad", "adset_id": adset_id, "creative_id": creative_id, "status": PAUSED,
            "key": key})

    def create_lead_form(self, payload: Mapping[str, Any], approval: Verified) -> str:
        form = lead_form_of(payload)
        self._delivery(payload, approval)
        oid = _id("form")
        self.objects[oid] = {"type": "lead_form", **form}
        self.calls.append(("create_lead_form", oid))
        return oid

    def _leads(self, form_id: str) -> list[Any]:
        from studio.leadads import RemoteLead

        out = []
        for raw in json.loads(LEADS.read_text())["data"]:
            fields = {f["name"]: str((f.get("values") or [""])[0]) for f in raw["field_data"]}
            ts = datetime.strptime(raw["created_time"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
            out.append(RemoteLead(f"{raw['id']}-{form_id[-6:]}", form_id, ts, fields))
        return out

    def list_leads(self, form_id: str, since: float = 0.0) -> list[Any]:
        self.calls.append(("list_leads", form_id))
        if self.objects.get(form_id, {}).get("type") != "lead_form":
            raise AdapterError("unknown form", code="not_found")
        return [lead for lead in self._leads(form_id) if lead.created_ts > since]

    def get_lead(self, lead_id: str) -> Any:
        self.calls.append(("get_lead", lead_id))
        for oid, obj in self.objects.items():
            if obj.get("type") == "lead_form":
                for lead in self._leads(oid):
                    if lead.external_id == lead_id:
                        return lead
        raise AdapterError("unknown lead", code="not_found")

    def preview(self, ad_id: str) -> Preview:
        ad = self.objects.get(ad_id)
        if not ad or ad.get("type") != "ad":
            raise AdapterError("unknown ad", code="not_found")
        creative = self.objects[ad["creative_id"]]
        self.calls.append(("preview", ad_id))
        return Preview("inline", fields={k: creative[k] for k in
                                         ("message", "name", "description", "cta", "link")})

    def activate(self, level: str, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, ACTIVATE, payload, self._conn)
        oid = activation_target(level, payload)
        obj = self.objects.get(oid)
        kind = "ad" if level.startswith("ad_") else level
        if not obj or obj.get("type") != kind:
            raise AdapterError(f"unknown {level}", code="not_found")
        obj["status"] = "ACTIVE"
        self.calls.append(("activate_" + level, oid))

    def list_comments(self, object_id: str, since: float = 0.0) -> list[RemoteComment]:
        self.calls.append(("list_comments", object_id))
        return [c for c in self._comments if c.object_id == object_id and c.created_ts > since]

    def reply(self, payload: Mapping[str, Any], approval: Verified) -> str:
        require(approval, REPLY, payload, self._conn)
        target = str(payload.get("external_id") or payload["comment_id"])
        reply_id = _id("reply")
        self.replies.setdefault(target, []).append(str(payload["message"]))
        self.calls.append(("reply", target))
        return reply_id

    def hide(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, HIDE, payload, self._conn)
        target = str(payload.get("external_id") or payload["comment_id"])
        self.hidden.add(target)
        self.calls.append(("hide", target))

    def insights_report(self, object_id: str, fields: list[str],
                        breakdowns: str = "") -> dict[str, Any]:
        self.calls.append(("insights_report", object_id))
        data = json.loads(INSIGHTS_FULL.read_text())
        rows = data["breakdowns"][breakdowns] if breakdowns else data["total"]
        keep = set(fields) | {"date_start", "date_stop", *breakdowns.split(",")} - {""}
        return {"data": [{k: v for k, v in r.items() if k in keep} for r in rows],
                "_provenance": "synthetic"}

    def update_budget(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, UPDATE, payload, self._conn)
        target, value = update_of(payload, "budget")
        obj = self.objects.get(target)
        if not obj or obj.get("type") != "adset":
            raise AdapterError("unknown adset", code="not_found")
        obj["daily_budget"] = value
        self.calls.append(("update_budget", target))

    def pause(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, UPDATE, payload, self._conn)
        target, _ = update_of(payload, "status")
        obj = self.objects.get(target)
        if not obj or obj.get("type") != "campaign":
            raise AdapterError("unknown campaign", code="not_found")
        obj["status"] = PAUSED
        self.calls.append(("pause", target))

    def insights(self, campaign_id: str = "") -> dict[str, Any]:
        self.calls.append(("insights", campaign_id))
        return json.loads(INSIGHTS.read_text())  # type: ignore[no-any-return]
