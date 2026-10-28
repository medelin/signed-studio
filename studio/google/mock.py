"""Mock Google Ads adapter: same contract as ``rest``, nothing leaves the process.

Used by tests, the judge sandbox, the demo and replay (``GOOGLE_ADS_ADAPTER=mock``, the
default). It keeps the objects it "created" in memory, asserts ``PAUSED`` on each, and
records every call in ``calls`` so tests can prove that a refused write made no call.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from studio.approvals import Verified
from studio.google.adapter import (
    ACTIVATE,
    CREATE,
    ENABLED,
    MOCK_CUSTOMER,
    PAUSED,
    activation_names,
    create_operations,
    spec_of,
)
from studio.meta.adapter import check_paused, require

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "google" / "insights.SYNTHETIC.json"


def _num() -> str:
    return str(10**9 + secrets.randbelow(9 * 10**9))


class MockAdapter:
    name = "mock"

    def __init__(self, *, conn: Any = None, currency: str = "GBP",
                 customer_id: str = MOCK_CUSTOMER) -> None:
        self._conn = conn
        self.currency = currency
        self.customer_id = customer_id
        self.objects: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []  # (method, resource name or "validate")

    def account_info(self) -> dict[str, str]:
        return {"currency": self.currency, "time_zone": "Europe/London"}

    def create_search_campaign(self, payload: Mapping[str, Any], approval: Verified, *,
                               validate_only: bool = False) -> dict[str, str]:
        require(approval, CREATE, payload, self._conn)
        check_paused(payload)
        spec = spec_of(payload, self.customer_id)
        ops = create_operations(spec)
        if validate_only:
            self.calls.append(("validate", ""))
            return {}
        cid = self.customer_id
        made: dict[str, str] = {}
        temp: dict[str, str] = {}
        for op in ops:
            ((kind, body),) = op.items()
            create = body["create"]
            if "status" in create and kind != "adGroupCriterionOperation":
                assert create["status"] == PAUSED, "mock adapter: creation must be PAUSED"
            collection = {"campaignBudgetOperation": "campaignBudgets",
                          "campaignOperation": "campaigns", "adGroupOperation": "adGroups",
                          "adGroupAdOperation": "adGroupAds"}.get(kind)
            name = ""
            if collection:
                real = _num()
                name = (f"customers/{cid}/{collection}/{real}" if collection != "adGroupAds"
                        else f"customers/{cid}/adGroupAds/{temp.get('adGroups', real)}~{real}")
                if "resourceName" in create:
                    temp[create["resourceName"].rsplit("/", 2)[1]] = real
            self.objects[name or f"{kind}-{secrets.token_hex(4)}"] = {
                "type": kind, **json.loads(json.dumps(create))}
            made[{"campaignBudgetOperation": "budget", "campaignOperation": "campaign",
                  "adGroupOperation": "ad_group", "adGroupAdOperation": "ad_group_ad"
                  }.get(kind, "")] = name
        made.pop("", None)
        self.calls.append(("create_search_campaign", made["campaign"]))
        return made

    def activate(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, ACTIVATE, payload, self._conn)
        names = activation_names(payload, self.customer_id)
        for key in ("ad_group_ad", "ad_group", "campaign"):  # the parent last
            obj = self.objects.get(names[key])
            if obj is None:
                raise AssertionError(f"mock adapter: no such object {names[key]}")
            obj["status"] = ENABLED
            self.calls.append(("activate", names[key]))

    def insights(self, campaign: str) -> list[dict[str, Any]]:
        """The synthetic week (``fixtures/google/insights.SYNTHETIC.json``), dated to end
        yesterday."""
        rows = json.loads(FIXTURE.read_text())["rows"]
        end = date.today() - timedelta(days=1)
        return [{**r, "date": (end - timedelta(days=len(rows) - 1 - i)).isoformat()}
                for i, r in enumerate(rows)]
