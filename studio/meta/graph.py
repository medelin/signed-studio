"""Graph API adapter: Marketing API (ads) + Pages API (comments) over ``httpx``.

Runs only with ``META_ADAPTER=graph`` on the operator's machine; the sandbox never has a
token. Shapes follow the Graph API v25.0 reference (campaigns, adsets, adimages,
adcreatives, ads, previews, comments). Writes are form-encoded with nested values as JSON
strings, as in the reference's cURL examples.

- The token is sent only as ``Authorization: Bearer <token>``; URLs never carry it and
  error messages never include the response body.
- Retry matrix: Graph error codes 4, 17, 32, 613 (throttling) are transient and retried
  with backoff (at most ``RETRIES`` times); 10, 100, 190 and anything unknown are fatal. A
  network error on a POST is not retried (the write may have happened; a duplicate PAUSED
  object is better found by a human than created twice silently).
- Campaign, ad set and ad bodies carry ``status=PAUSED`` from a literal; activation is
  ``POST /{campaign_id} status=ACTIVE`` behind a ``campaign.activate`` approval.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import httpx

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
from studio.meta.preview import from_graph_body

VERSION = "v25.0"
BASE = "https://graph.facebook.com"
TRANSIENT = frozenset({4, 17, 32, 613})
FATAL = frozenset({10, 100, 190})
RETRIES = 3
_ACCOUNT = re.compile(r"(?:act_)?(\d{1,30})")
_OBJECT_ID = re.compile(r"[0-9_]{1,80}")


def _form(fields: Mapping[str, Any]) -> dict[str, str]:
    """Graph form fields: strings as-is, everything else JSON-encoded."""
    return {k: v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
            for k, v in fields.items()}


def _oid(value: str) -> str:
    if not isinstance(value, str) or not _OBJECT_ID.fullmatch(value):
        raise AdapterError("not a Graph object id", code="bad_id")
    return value


class GraphAdapter:
    name = "graph"

    def __init__(self, access_token: str, ad_account_id: str, page_id: str,
                 page_token: str = "", *, version: str = VERSION,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0) -> None:
        m = _ACCOUNT.fullmatch(ad_account_id or "")
        if not access_token or m is None or not page_id:
            raise ValueError("graph adapter needs a token, an ad account id and a page id")
        self._token = access_token
        self._page_token = page_token or access_token
        self.account = f"act_{m.group(1)}"
        self.page_id = _oid(page_id)
        self.base = f"{BASE}/{version}"
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> GraphAdapter:
        return cls(settings.meta_access_token, settings.meta_ad_account_id,
                   settings.meta_page_id, settings.meta_page_token, **kwargs)

    def __repr__(self) -> str:
        return f"GraphAdapter(base={self.base!r})"

    # -- transport -----------------------------------------------------------------

    def _request(self, method: str, path: str, *, data: Mapping[str, Any] | None = None,
                 params: Mapping[str, Any] | None = None, page: bool = False) -> dict[str, Any]:
        token = self._page_token if page else self._token
        headers = {"Authorization": f"Bearer {token}"}
        for attempt in range(RETRIES + 1):
            try:
                resp = self._http.request(method, f"{self.base}/{path}", headers=headers,
                                          data=_form(data) if data is not None else None,
                                          params=dict(params or {}))
            except httpx.HTTPError as exc:
                if method == "GET" and attempt < RETRIES:
                    self._sleep(2 ** attempt)
                    continue
                raise AdapterError(f"Graph API unreachable ({type(exc).__name__})",
                                   code="network", transient=method == "GET") from None
            try:
                body = resp.json()
            except ValueError:
                body = None
            if resp.status_code < 400 and isinstance(body, dict) and "error" not in body:
                return body
            err = body.get("error") if isinstance(body, dict) else None
            code = int(err.get("code", 0)) if isinstance(err, dict) and isinstance(
                err.get("code"), int) else 0
            if code in TRANSIENT and attempt < RETRIES:
                self._sleep(2 ** attempt)
                continue
            raise AdapterError(f"Graph API error {code or resp.status_code}",
                               code="graph_error", transient=code in TRANSIENT,
                               graph_code=code)
        raise AdapterError("Graph API retries exhausted", code="graph_error", transient=True)

    def _post(self, path: str, fields: Mapping[str, Any], *, page: bool = False
              ) -> dict[str, Any]:
        return self._request("POST", path, data=fields, page=page)

    @staticmethod
    def _id(body: Mapping[str, Any]) -> str:
        oid = body.get("id")
        if not isinstance(oid, str) or not oid:
            raise AdapterError("Graph API returned no id", code="bad_response")
        return _oid(oid)

    # -- Marketing API -------------------------------------------------------------

    def upload_image(self, png: bytes, payload: Mapping[str, Any],
                     approval: Verified) -> str:
        require(approval, CREATE, payload)
        digest = hashlib.sha256(png).hexdigest()
        if digest not in {i.get("sha256") for i in payload.get("images", [])}:
            raise NotApproved("image is not one of the approved images")
        body = self._post(f"{self.account}/adimages",
                          {"bytes": base64.b64encode(png).decode()})
        images = body.get("images")
        if isinstance(images, dict):
            for item in images.values():
                if isinstance(item, dict) and isinstance(item.get("hash"), str):
                    return str(item["hash"])
        raise AdapterError("Graph API returned no image hash", code="bad_response")

    def create_campaign_paused(self, payload: Mapping[str, Any], approval: Verified) -> str:
        require(approval, CREATE, payload)
        check_paused(payload)
        return self._id(self._post(f"{self.account}/campaigns", {
            "name": campaign_name(payload), "objective": payload["campaign"]["objective"],
            "status": PAUSED, "special_ad_categories": [], "buying_type": "AUCTION",
            "is_adset_budget_sharing_enabled": "0"}))

    def create_adset_paused(self, campaign_id: str, payload: Mapping[str, Any],
                            approval: Verified) -> str:
        require(approval, CREATE, payload)
        check_paused(payload)
        return self._id(self._post(f"{self.account}/adsets", {
            "name": campaign_name(payload) + " · ad set", "campaign_id": _oid(campaign_id),
            "daily_budget": str(int(payload["campaign"]["daily_budget_minor"])),
            "billing_event": "IMPRESSIONS", "optimization_goal": "LINK_CLICKS",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "targeting": adset_targeting(payload),
            "status": PAUSED}))

    def create_creative(self, image_hash: str, payload: Mapping[str, Any],
                        approval: Verified) -> str:
        require(approval, CREATE, payload)
        check_paused(payload)
        ad, link = payload["ad"], str(payload.get("link", ""))
        return self._id(self._post(f"{self.account}/adcreatives", {
            "name": campaign_name(payload) + " · creative",
            "object_story_spec": {"page_id": self.page_id, "link_data": {
                "image_hash": image_hash, "link": link, "message": ad["primary_text"],
                "name": ad["headline"], "description": ad["description"],
                "call_to_action": {"type": ad["cta"], "value": {"link": link}}}}}))

    def create_ad_paused(self, adset_id: str, creative_id: str, payload: Mapping[str, Any],
                         approval: Verified) -> str:
        require(approval, CREATE, payload)
        check_paused(payload)
        return self._id(self._post(f"{self.account}/ads", {
            "name": campaign_name(payload) + " · ad", "adset_id": _oid(adset_id),
            "creative": {"creative_id": _oid(creative_id)}, "status": PAUSED}))

    def preview(self, ad_id: str) -> Preview:
        body = self._request("GET", f"{_oid(ad_id)}/previews",
                             params={"ad_format": "MOBILE_FEED_STANDARD"})
        data = body.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise AdapterError("Graph API returned no preview", code="bad_response")
        return from_graph_body(str(data[0].get("body", "")))

    def activate(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, ACTIVATE, payload)
        body = self._post(_oid(str(payload.get("external_id", ""))), {"status": "ACTIVE"})
        if body.get("success") is not True:
            raise AdapterError("Graph API did not confirm the activation",
                               code="bad_response")

    def insights(self, campaign_id: str) -> dict[str, Any]:
        return self._request("GET", f"{_oid(campaign_id)}/insights", params={
            "fields": "impressions,reach,clicks,ctr,spend,cpc", "time_increment": "1",
            "date_preset": "last_7d"})

    # -- Pages API -----------------------------------------------------------------

    def list_comments(self, object_id: str, since: float = 0.0) -> list[RemoteComment]:
        params: dict[str, Any] = {"fields": "id,message,from,created_time",
                                  "filter": "toplevel", "order": "reverse_chronological",
                                  "limit": "50"}
        if since:
            params["since"] = str(int(since))
        body = self._request("GET", f"{_oid(object_id)}/comments", params=params, page=True)
        out = []
        for c in body.get("data") or []:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str):
                continue
            try:
                ts = datetime.strptime(str(c.get("created_time", "")),
                                       "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                ts = 0.0
            author = c.get("from") if isinstance(c.get("from"), dict) else {}
            out.append(RemoteComment(c["id"], str(c.get("message", ""))[:2000],
                                     str((author or {}).get("name", "Facebook user"))[:60],
                                     ts, object_id))
        return out

    def reply(self, payload: Mapping[str, Any], approval: Verified) -> str:
        require(approval, REPLY, payload)
        return self._id(self._post(f"{_oid(str(payload.get('external_id', '')))}/comments",
                                   {"message": str(payload["message"])}, page=True))

    def hide(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, HIDE, payload)
        body = self._post(_oid(str(payload.get("external_id", ""))), {"is_hidden": "true"},
                          page=True)
        if body.get("success") is not True:
            raise AdapterError("Graph API did not confirm hiding", code="bad_response")
