"""Meta Model API client (OpenAI-compatible REST over ``httpx``).

Every call, successful or not, appends a ``model_call`` receipt naming the model id the API
reports, the tier and why it was chosen, request/output hashes, token usage and cost. The
API key is sent only in the ``Authorization`` header; it never appears in receipts,
exceptions or ``repr``. Read-only mode and the daily budget are enforced before any request.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from studio.receipts import Ledger, canonical, sha256_hex
from studio.settings import Budget, ReadOnlyMode, Settings

BASE_URL = "https://api.meta.ai/v1"
PROVIDER = "meta-model-api"

# USD per 1M tokens: (input, cached input, output) — dev.meta.ai pricing, 2026-10-27.
TOKEN_PRICES: dict[str, tuple[float, float, float]] = {
    "muse-spark-1.1": (1.25, 0.15, 4.25),
    "muse-spark-1.2": (1.25, 0.15, 4.25),
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.2-contributor": (0.10, 0.002, 0.20),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
}
TIERS = frozenset({"standard", "contributor"})


class MuseError(RuntimeError):
    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str  # JSON string; validate before use


@dataclass(frozen=True)
class MuseResult:
    text: str
    model: str
    usage: Usage
    cost_usd: float
    response_id: str = ""
    output_items: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_summary: str = ""
    finish: str = ""
    duration_ms: int = 0
    receipt_id: str = ""


def cost_usd(model: str, usage: Usage) -> float:
    price_in, price_cached, price_out = TOKEN_PRICES[model]
    cached = min(usage.cached_tokens, usage.input_tokens)
    return (
        (usage.input_tokens - cached) * price_in
        + cached * price_cached
        + usage.output_tokens * price_out
    ) / 1_000_000


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _image_hashes(node: Any) -> list[str]:
    """SHA-256 of every inline image (data URL) in a request body, in order."""
    out: list[str] = []
    if isinstance(node, str):
        if node.startswith("data:image/") and ";base64," in node:
            out.append(sha256_hex(base64.b64decode(node.split(",", 1)[1])))
    elif isinstance(node, Mapping):
        for v in node.values():
            out += _image_hashes(v)
    elif isinstance(node, list | tuple):
        for v in node:
            out += _image_hashes(v)
    return out


def check_tier(model: str, tier: str) -> None:
    if model not in TOKEN_PRICES:
        raise ValueError(f"unknown model {model!r}")
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    if (tier == "contributor") != model.endswith("-contributor"):
        raise ValueError(f"tier {tier!r} does not match model {model!r}")


class MuseClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        *,
        ledger: Ledger,
        budget: Budget | None = None,
        read_only: bool = False,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Model API base URL must use https")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.ledger = ledger
        self.budget = budget
        self.read_only = read_only
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @classmethod
    def from_settings(
        cls, settings: Settings, ledger: Ledger, budget: Budget | None = None
    ) -> MuseClient:
        return cls(
            settings.model_api_key,
            settings.model_api_base_url,
            ledger=ledger,
            budget=budget or Budget(settings.daily_budget_usd),
            read_only=settings.read_only,
        )

    def __repr__(self) -> str:
        return f"MuseClient(base_url={self.base_url!r}, read_only={self.read_only})"

    def close(self) -> None:
        self._http.close()

    # -- public API ------------------------------------------------------------------

    def respond(
        self,
        model: str,
        input: str | Sequence[Mapping[str, Any]],  # noqa: A002 - API field name
        *,
        run_id: str,
        agent: str,
        tools: Sequence[Mapping[str, Any]] | None = None,
        reasoning: Mapping[str, Any] | None = None,
        previous_response_id: str | None = None,
        store: bool = True,
        max_output_tokens: int | None = None,
        text_format: Mapping[str, Any] | None = None,
        tier: str = "standard",
        tier_reason: str = "",
    ) -> MuseResult:
        """Responses API (``POST /responses``): tools, reasoning, chained turns, images."""
        body: dict[str, Any] = {"model": model, "input": input, "store": store}
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        if reasoning:
            body["reasoning"] = dict(reasoning)
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens
        if text_format:
            body["text"] = {"format": dict(text_format)}
        data, ms = self._post("/responses", body, model, tier, tier_reason, run_id, agent)
        return self._finish(data, ms, model, tier, tier_reason, run_id, agent, body,
                            _parse_responses(data), "responses")

    def chat(
        self,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        run_id: str,
        agent: str,
        response_format: Mapping[str, Any] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        tier: str = "standard",
        tier_reason: str = "",
    ) -> MuseResult:
        """Chat Completions (``POST /chat/completions``): strict JSON output, image parts."""
        body: dict[str, Any] = {"model": model, "messages": list(messages)}
        if response_format:
            body["response_format"] = dict(response_format)
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_completion_tokens"] = max_tokens
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        data, ms = self._post("/chat/completions", body, model, tier, tier_reason, run_id, agent)
        return self._finish(data, ms, model, tier, tier_reason, run_id, agent, body,
                            _parse_chat(data), "chat.completions")

    # -- internals -------------------------------------------------------------------

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        model: str,
        tier: str,
        tier_reason: str,
        run_id: str,
        agent: str,
    ) -> tuple[dict[str, Any], int]:
        check_tier(model, tier)
        if self.read_only:
            raise ReadOnlyMode("STUDIO_READ_ONLY=1: model calls are disabled")
        if self.budget is not None:
            self.budget.check()
        if not self._api_key:
            raise MuseError("MODEL_API_KEY is not set", code="no_api_key")
        start = time.monotonic()
        try:
            resp = self._http.post(
                self.base_url + path,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            ms = int((time.monotonic() - start) * 1000)
            self._error_receipt(body, model, tier, tier_reason, run_id, agent, ms,
                                {"error": "network", "error_type": type(exc).__name__})
            raise MuseError(f"Model API unreachable ({type(exc).__name__})",
                            code="network") from None
        ms = int((time.monotonic() - start) * 1000)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or not isinstance(data, dict):
            err = data.get("error") if isinstance(data, dict) else None
            code = str(err.get("type") or err.get("code") or "") if isinstance(err, dict) else ""
            self._error_receipt(body, model, tier, tier_reason, run_id, agent, ms,
                                {"error_status": resp.status_code, "error_type": code})
            raise MuseError(f"Model API returned HTTP {resp.status_code} ({code or 'error'})",
                            status=resp.status_code, code=code)
        return data, ms

    def _error_receipt(
        self,
        body: dict[str, Any],
        model: str,
        tier: str,
        tier_reason: str,
        run_id: str,
        agent: str,
        ms: int,
        details: dict[str, Any],
    ) -> None:
        self.ledger.append(
            run_id, agent=agent, kind="model_call", provider=PROVIDER, model=model, tier=tier,
            tier_reason=tier_reason, prompt_sha256=sha256_hex(canonical(body)),
            input_sha256=_image_hashes(body), duration_ms=ms, details=details,
        )

    def _finish(
        self,
        data: dict[str, Any],
        ms: int,
        model: str,
        tier: str,
        tier_reason: str,
        run_id: str,
        agent: str,
        body: dict[str, Any],
        parsed: dict[str, Any],
        endpoint: str,
    ) -> MuseResult:
        used_model = str(data.get("model") or model)
        usage: Usage = parsed["usage"]
        cost = cost_usd(used_model if used_model in TOKEN_PRICES else model, usage)
        if self.budget is not None:
            self.budget.record(cost)
        details: dict[str, Any] = {
            "endpoint": endpoint,
            "response_id": str(data.get("id", "")),
            "finish": parsed["finish"],
            "reasoning_tokens": usage.reasoning_tokens,
        }
        if parsed["tool_calls"]:
            details["tool_calls"] = [c.name for c in parsed["tool_calls"]]
        if used_model != model:
            details["requested_model"] = model
        receipt = self.ledger.append(
            run_id, agent=agent, kind="model_call", provider=PROVIDER, model=used_model,
            tier=tier, tier_reason=tier_reason, prompt_sha256=sha256_hex(canonical(body)),
            input_sha256=_image_hashes(body), output_sha256=sha256_hex(parsed["text"]),
            tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
            cached_tokens=usage.cached_tokens, cost_usd=round(cost, 8), duration_ms=ms,
            details=details,
        )
        return MuseResult(
            text=parsed["text"], model=used_model, usage=usage, cost_usd=cost,
            response_id=str(data.get("id", "")), output_items=parsed["items"],
            tool_calls=parsed["tool_calls"], reasoning_summary=parsed["summary"],
            finish=parsed["finish"], duration_ms=ms, receipt_id=receipt.id,
        )


def _parse_responses(data: dict[str, Any]) -> dict[str, Any]:
    texts: list[str] = []
    summary: list[str] = []
    calls: list[ToolCall] = []
    items = [i for i in data.get("output") or [] if isinstance(i, dict)]
    for item in items:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    texts.append(str(part.get("text", "")))
        elif kind == "function_call":
            calls.append(ToolCall(str(item.get("call_id", "")), str(item.get("name", "")),
                                  str(item.get("arguments", ""))))
        elif kind == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, dict):
                    summary.append(str(part.get("text", "")))
    u = data.get("usage") or {}
    usage = Usage(
        input_tokens=_int(u.get("input_tokens")),
        output_tokens=_int(u.get("output_tokens")),
        cached_tokens=_int((u.get("input_tokens_details") or {}).get("cached_tokens")),
        reasoning_tokens=_int((u.get("output_tokens_details") or {}).get("reasoning_tokens")),
    )
    return {"text": "".join(texts), "summary": "\n".join(summary), "tool_calls": calls,
            "items": items, "usage": usage, "finish": str(data.get("status", ""))}


def _parse_chat(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    calls = [
        ToolCall(str(c.get("id", "")), str((c.get("function") or {}).get("name", "")),
                 str((c.get("function") or {}).get("arguments", "")))
        for c in msg.get("tool_calls") or [] if isinstance(c, dict)
    ]
    u = data.get("usage") or {}
    usage = Usage(
        input_tokens=_int(u.get("prompt_tokens")),
        output_tokens=_int(u.get("completion_tokens")),
        cached_tokens=_int((u.get("prompt_tokens_details") or {}).get("cached_tokens")),
        reasoning_tokens=_int(
            (u.get("completion_tokens_details") or {}).get("reasoning_tokens")),
    )
    return {"text": str(msg.get("content") or ""), "summary": "", "tool_calls": calls,
            "items": [msg] if msg else [], "usage": usage,
            "finish": str(choice.get("finish_reason", "")),
            "refusal": msg.get("refusal")}
