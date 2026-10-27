"""Muse Spark Open on Baseten: the open-weights second-opinion reviewer.

Baseten serves ``meta-models/muse-spark-open-1026`` behind an OpenAI-compatible Chat
Completions API (``https://inference.baseten.co/v1``, ``Authorization: Bearer
$BASETEN_API_KEY``); images go in as ``image_url`` data URLs. The client reuses the Muse
client's receipt and failure handling with provider ``baseten`` and tier ``open``. It is
paid from Baseten credits, not the Model API budget, and its per-token price is not
verified, so receipts record ``cost_usd=0`` (``cost_basis: baseten_credits``). Without a
key the product degrades: ``available()`` is false and the reviewer reports "second
opinion unavailable" instead of failing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from studio.muse.client import MuseClient, MuseError, Usage
from studio.receipts import Ledger
from studio.settings import Settings

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open-1026"
PROVIDER = "baseten"
MODELS = frozenset({MODEL})


class OpenWeightsUnavailable(MuseError):
    """No Baseten key configured: the second opinion is skipped, not failed."""


class OpenWeightsClient(MuseClient):
    provider = PROVIDER
    key_name = "BASETEN_API_KEY"
    api_label = "Baseten"

    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        *,
        ledger: Ledger,
        read_only: bool = False,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(api_key, base_url, ledger=ledger, budget=None, read_only=read_only,
                         timeout=timeout, transport=transport)

    @classmethod
    def from_settings(  # type: ignore[override]
        cls, settings: Settings, ledger: Ledger, budget: Any = None
    ) -> OpenWeightsClient:
        return cls(settings.baseten_api_key, ledger=ledger, read_only=settings.read_only)

    def available(self) -> bool:
        return bool(self._api_key)

    def __repr__(self) -> str:
        return f"OpenWeightsClient(base_url={self.base_url!r}, available={self.available()})"

    def _check(self, model: str, tier: str) -> None:
        if model not in MODELS:
            raise ValueError(f"unknown open-weights model {model!r}")
        if tier != "open":
            raise ValueError(f"tier {tier!r} does not match model {model!r}")

    def _estimate(self, model: str, body: Mapping[str, Any]) -> float:
        return 0.0

    def _cost(self, model: str, reported: str, usage: Usage, units: int) -> float:
        return 0.0

    def _cost_note(self) -> dict[str, str]:
        return {"cost_basis": "baseten_credits"}

    def _receipt_model(self, model: str) -> str:
        # "meta-models/muse-spark-open-1026" has a "/", which receipt ids do not allow
        return model.replace("/", ":") if model in MODELS else model
