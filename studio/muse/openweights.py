"""Muse Spark Open on Baseten: the open-weights second-opinion reviewer.

Baseten serves ``meta-models/muse-spark-open`` (the id its Model APIs list; the earlier
``-1026`` suffix is a 404) behind an OpenAI-compatible Chat Completions API
(``https://inference.baseten.co/v1``, ``Authorization: Bearer $BASETEN_API_KEY``); images
go in as ``image_url`` data URLs. It is a reasoning model: ``message.reasoning_content``
holds the reasoning and ``usage.completion_tokens_details.reasoning_tokens`` counts it
inside ``max_tokens``; an answer with reasoning but no content is an error
(``reasoning_only``), never an empty review. The client reuses the Muse client's receipt
and failure handling with provider ``baseten`` and tier ``open``. Cost uses Baseten's
published Model APIs price ($1.25 input, $0.15 cached input, $4.25 output per 1M tokens,
baseten.co/pricing, 2026-10-28); it is paid from Baseten credits, not the Model API budget
(``cost_basis: baseten_price``). Without a key the product degrades: ``available()`` is
false and the reviewer reports "second opinion unavailable" instead of failing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from studio.muse.client import MuseClient, MuseError, MuseResult, Usage
from studio.receipts import Ledger
from studio.settings import Settings

BASE_URL = "https://inference.baseten.co/v1"
MODEL = "meta-models/muse-spark-open"
PROVIDER = "baseten"
MODELS = frozenset({MODEL})
# USD per 1M tokens (input, cached input, output), Baseten pricing page, 2026-10-28
PRICE = (1.25, 0.15, 4.25)


class OpenWeightsUnavailable(MuseError):
    """No Baseten key configured: the second opinion is skipped, not failed."""


class OpenWeightsClient(MuseClient):
    provider = PROVIDER
    key_name = "BASETEN_API_KEY"
    api_label = "Baseten"
    open_weights = True

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

    def chat(self, *args: Any, **kwargs: Any) -> MuseResult:
        """Chat Completions; a reply that is all reasoning and no answer (``max_tokens``
        spent on reasoning) raises ``reasoning_only`` after its receipt is written."""
        result = super().chat(*args, **kwargs)
        if not result.text.strip() and not result.tool_calls:
            raise MuseError(f"{self.api_label} returned reasoning but no answer "
                            f"(finish={result.finish}); raise max_tokens",
                            code="reasoning_only")
        return result

    def _estimate(self, model: str, body: Mapping[str, Any]) -> float:
        return 0.0  # no Model API budget is reserved: Baseten credits pay for it

    def _cost(self, model: str, reported: str, usage: Usage, units: int) -> float:
        cached = min(usage.cached_tokens, usage.input_tokens)
        return ((usage.input_tokens - cached) * PRICE[0] + cached * PRICE[1]
                + usage.output_tokens * PRICE[2]) / 1_000_000

    def _cost_note(self) -> dict[str, str]:
        return {"cost_basis": "baseten_price"}

    def _receipt_model(self, model: str) -> str:
        # "meta-models/muse-spark-open" has a "/", which receipt ids do not allow
        return model.replace("/", ":") if model in MODELS else model
