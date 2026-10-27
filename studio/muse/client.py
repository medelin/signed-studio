"""Meta Model API client (OpenAI-compatible REST over ``httpx``).

Every call, successful or not, appends a ``model_call`` receipt naming the model id the API
reports, the tier and why it was chosen, request/output hashes, token usage and cost. The
API key is sent only in the ``Authorization`` header; it never appears in receipts,
exceptions or ``repr``. Read-only mode and the daily budget are enforced before any request.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from studio.receipts import Ledger, canonical, normalize_id, sha256_hex
from studio.settings import Budget, ReadOnlyMode, Settings

BASE_URL = "https://api.meta.ai/v1"
PROVIDER = "meta-model-api"

# USD per 1M tokens: (input, cached input, output) — dev.meta.ai pricing, 2026-10-27.
# Only models whose price was checked are listed; an unlisted model is refused.
TOKEN_PRICES: dict[str, tuple[float, float, float]] = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "muse-wonder-1.0": (4.00, 0.20, 20.00),
}
# USD per unit (generated image / segmented image): priced per output, not per token.
UNIT_PRICES: dict[str, float] = {
    "muse-image-1.0": 0.01,
    "sam-3.1": 0.0025,  # $2.50 per 1k images
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
class GeneratedImage:
    data: bytes
    item_id: str = ""  # Responses ``image_generation_call`` id (a handle for later edits)


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
    images: list[GeneratedImage] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)  # SSE events (streamed calls)


def cost_usd(model: str, usage: Usage, units: int = 0) -> float:
    if model in UNIT_PRICES:
        return UNIT_PRICES[model] * units
    price_in, price_cached, price_out = TOKEN_PRICES[model]
    cached = min(usage.cached_tokens, usage.input_tokens)
    return (
        (usage.input_tokens - cached) * price_in
        + cached * price_cached
        + usage.output_tokens * price_out
    ) / 1_000_000


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _hash_image_ref(ref: Any) -> str:
    """Hash of an image reference; never raises (an audit hash must always exist).

    Inline data URLs hash to the decoded bytes; remote URLs and file ids to ``url:``/
    ``file:``-tagged hashes of the reference; anything malformed to an ``undecodable:``
    marker over the raw value.
    """
    raw = ref if isinstance(ref, str) else repr(ref)
    if isinstance(ref, str) and ref.startswith("data:"):
        header, _, payload = ref.partition(",")
        if header.endswith(";base64") and payload:
            try:
                return sha256_hex(base64.b64decode(payload, validate=True))
            except (binascii.Error, ValueError):
                pass
        return "undecodable:" + sha256_hex(raw)
    if isinstance(ref, str) and ref.startswith(("https://", "http://")):
        return "url:" + sha256_hex(ref)
    return "undecodable:" + sha256_hex(raw)


def input_hashes(body: Mapping[str, Any]) -> list[str]:
    """Hashes of image parts at their known positions, computed before the request is sent:
    Chat ``messages[].content[]`` ``image_url.url`` and Responses ``input[].content[]``
    ``input_image.image_url`` / ``file_id``."""
    out: list[str] = []
    containers = [body.get("messages"), body.get("input")]
    for items in containers:
        if not isinstance(items, list):
            continue
        for item in items:
            content = item.get("content") if isinstance(item, Mapping) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, Mapping):
                    continue
                if part.get("type") == "image_url":
                    ref = part.get("image_url")
                    out.append(_hash_image_ref(ref.get("url") if isinstance(ref, Mapping)
                                               else ref))
                elif part.get("type") == "input_image":
                    if part.get("file_id"):
                        out.append("file:" + sha256_hex(str(part["file_id"])))
                    else:
                        out.append(_hash_image_ref(part.get("image_url")))
    return out


DEFAULT_OUTPUT_ESTIMATE = 8192  # output tokens assumed when the call sets no cap
IMAGE_TOKENS_ESTIMATE = 2000  # per image part (data URLs are not counted as text)


def _strip_images(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("type") in ("image_url", "input_image"):
            return None
        return {k: _strip_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_images(v) for v in value]
    return value


def estimate_cost(model: str, body: Mapping[str, Any]) -> float:
    """Worst-case-ish cost of a request, reserved from the budget before it is sent: input
    at ~3 characters per token (uncached), images at a flat rate, output at its cap; for
    unit-priced models, the price times the number of outputs asked for."""
    if model in UNIT_PRICES:
        n = body.get("n")
        return UNIT_PRICES[model] * (n if isinstance(n, int) and n > 0 else 1)
    price_in, _, price_out = TOKEN_PRICES[model]
    text_tokens = len(canonical(_strip_images(dict(body)))) // 3 + 1
    images = len(input_hashes(body))
    cap = body.get("max_output_tokens") or body.get("max_completion_tokens")
    out = cap if isinstance(cap, int) and cap > 0 else DEFAULT_OUTPUT_ESTIMATE
    return ((text_tokens + images * IMAGE_TOKENS_ESTIMATE) * price_in + out * price_out) / 1e6


def _usage_of(data: Any) -> Usage | None:
    """Usage from a body that failed to parse otherwise (Chat or Responses names)."""
    u = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(u, dict):
        return None
    usage = Usage(
        input_tokens=_int(u.get("input_tokens", u.get("prompt_tokens"))),
        output_tokens=_int(u.get("output_tokens", u.get("completion_tokens"))),
    )
    return usage if usage.input_tokens or usage.output_tokens else None


def check_tier(model: str, tier: str) -> None:
    if model not in TOKEN_PRICES and model not in UNIT_PRICES:
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
            budget=budget or Budget.from_ledger(settings.daily_budget_usd, ledger),
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
        parallel_tool_calls: bool = True,
    ) -> MuseResult:
        """Responses API (``POST /responses``): tools, reasoning, chained turns, images."""
        body: dict[str, Any] = {"model": model, "input": input, "store": store}
        if tools:
            body["tools"] = list(tools)
            if any(t.get("type") == "function" for t in tools):
                body["tool_choice"] = "auto"
                body["parallel_tool_calls"] = parallel_tool_calls
        if reasoning:
            body["reasoning"] = dict(reasoning)
        if previous_response_id:
            body["previous_response_id"] = previous_response_id
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens
        if text_format:
            body["text"] = {"format": dict(text_format)}
        return self._call("/responses", "responses", _parse_responses, body, model, tier,
                          tier_reason, run_id, agent)

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
        parallel_tool_calls: bool = True,
    ) -> MuseResult:
        """Chat Completions (``POST /chat/completions``): strict JSON output, image parts."""
        body: dict[str, Any] = {"model": model, "messages": list(messages)}
        if response_format:
            body["response_format"] = dict(response_format)
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = parallel_tool_calls
        if max_tokens:
            body["max_completion_tokens"] = max_tokens
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        return self._call("/chat/completions", "chat.completions", _parse_chat, body, model,
                          tier, tier_reason, run_id, agent)

    def images(
        self,
        operation: str,
        body: Mapping[str, Any],
        *,
        run_id: str,
        agent: str,
        tier_reason: str = "",
    ) -> MuseResult:
        """Images API: ``operation`` is ``generations`` or ``edits`` (JSON body, base64 out)."""
        if operation not in ("generations", "edits"):
            raise ValueError(f"unknown images operation {operation!r}")
        full = {"response_format": "b64_json", **body}
        model = str(full.get("model", ""))
        return self._call(f"/images/{operation}", f"images.{operation}", _parse_images, full,
                          model, "standard", tier_reason, run_id, agent)

    def respond_stream(
        self,
        model: str,
        input: Sequence[Mapping[str, Any]],  # noqa: A002 - API field name
        *,
        run_id: str,
        agent: str,
        metadata: Mapping[str, str] | None = None,
        tier_reason: str = "",
    ) -> MuseResult:
        """Streamed Responses call (SSE); events are returned in ``MuseResult.events``."""
        body: dict[str, Any] = {"model": model, "input": list(input), "stream": True}
        if metadata:
            body["metadata"] = dict(metadata)
        return self._call("/responses", "responses.stream", _parse_stream, body, model,
                          "standard", tier_reason, run_id, agent, stream=True)

    # -- internals -------------------------------------------------------------------

    def _call(
        self,
        path: str,
        endpoint: str,
        parser: Callable[[dict[str, Any]], dict[str, Any]],
        body: dict[str, Any],
        model: str,
        tier: str,
        tier_reason: str,
        run_id: str,
        agent: str,
        stream: bool = False,
    ) -> MuseResult:
        """Send one request. Once anything may have been sent, a receipt is always written:
        the success receipt, or an error receipt for network, HTTP, parse or abort."""
        self._check(model, tier)
        # Caller-supplied ids fail here, before any I/O, never in the receipt after the call.
        rid = self._receipt_model(model)
        Ledger.validate_ids(run_id, agent, provider=self.provider, model=rid, tier=tier)
        if self.read_only:
            raise ReadOnlyMode("STUDIO_READ_ONLY=1: model calls are disabled")
        if not self._api_key:
            raise MuseError(f"{self.key_name} is not set", code="no_api_key")
        estimate = self._estimate(model, body)
        if self.budget is not None:
            self.budget.reserve(estimate)  # atomic: concurrent calls cannot overspend
        audit: dict[str, Any] = {
            "provider": self.provider,
            "tier": tier,
            "tier_reason": tier_reason,
            "prompt_sha256": sha256_hex(canonical(body)),
            "input_sha256": input_hashes(body),  # before sending; never raises
        }
        start = time.monotonic()
        try:
            data = self._send_stream(path, body) if stream else self._send(path, body)
            try:
                parsed = parser(data)
                used_model, reported_sha = normalize_id(
                    self._receipt_model(str(data.get("model") or model)))
                usage: Usage = parsed["usage"]
                cost = self._cost(model, used_model, usage, parsed.get("units", 0))
            except Exception:  # noqa: BLE001 - any malformed body is a bad response
                # The call was billed: record whatever usage the body reports, else the
                # estimate, so the spend (and the receipt the budget is rebuilt from) is real.
                bad_usage = _usage_of(data)
                raise _Failure({"error": "bad_response"},
                               MuseError(f"{self.api_label} returned an unexpected response shape",
                                         code="bad_response"),
                               usage=bad_usage,
                               cost=(self._cost(model, model, bad_usage, 0)
                                     if bad_usage and model in TOKEN_PRICES else None),
                               ) from None
        except _Failure as failure:
            charged = estimate if failure.cost is None else failure.cost
            self._settle(estimate, charged)
            u = failure.usage or Usage()
            basis = ({"cost_basis": "estimate"} if failure.cost is None
                     else {"cost_basis": "usage"} if failure.usage else {})
            self.ledger.append(run_id, agent=agent, kind="model_call", model=rid, **audit,
                               duration_ms=_ms(start), tokens_in=u.input_tokens,
                               tokens_out=u.output_tokens, cost_usd=round(charged, 8),
                               details={"endpoint": endpoint, **failure.details, **basis})
            raise failure.error from None
        except BaseException as exc:
            # Unknown whether the request was billed: charge the estimate (conservative).
            self._settle(estimate, estimate)
            self.ledger.append(run_id, agent=agent, kind="model_call", model=rid, **audit,
                               duration_ms=_ms(start), cost_usd=round(estimate, 8),
                               details={"endpoint": endpoint, "error": "aborted",
                                        "error_type": type(exc).__name__,
                                        "cost_basis": "estimate"})
            raise
        ms = _ms(start)
        self._settle(estimate, cost)
        details: dict[str, Any] = {
            "endpoint": endpoint,
            "response_id": str(data.get("id", "")),
            "finish": parsed["finish"],
            "reasoning_tokens": usage.reasoning_tokens,
        }
        if parsed.get("units"):
            details["units"] = parsed["units"]
        details.update(self._cost_note())
        if parsed["tool_calls"]:
            details["tool_calls"] = [c.name for c in parsed["tool_calls"]]
        if used_model != self._receipt_model(model):
            details["requested_model"] = self._receipt_model(model)
        if reported_sha:
            details["reported_model_sha256"] = reported_sha
        receipt = self.ledger.append(
            run_id, agent=agent, kind="model_call", model=used_model, **audit,
            output_sha256=sha256_hex(parsed["text"]), tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens, cached_tokens=usage.cached_tokens,
            cost_usd=round(cost, 8), duration_ms=ms, details=details,
        )
        return MuseResult(
            text=parsed["text"], model=used_model, usage=usage, cost_usd=cost,
            response_id=str(data.get("id", "")), output_items=parsed["items"],
            tool_calls=parsed["tool_calls"], reasoning_summary=parsed["summary"],
            finish=parsed["finish"], duration_ms=ms, receipt_id=receipt.id,
            images=parsed.get("images", []), events=data.get("_events", []),
        )

    # -- provider hooks (overridden by the open-weights client) -------------------------

    provider = PROVIDER
    key_name = "MODEL_API_KEY"
    api_label = "Model API"

    def _check(self, model: str, tier: str) -> None:
        check_tier(model, tier)

    def _estimate(self, model: str, body: Mapping[str, Any]) -> float:
        return estimate_cost(model, body)

    def _cost(self, model: str, reported: str, usage: Usage, units: int) -> float:
        priced = reported if reported in TOKEN_PRICES or reported in UNIT_PRICES else model
        return cost_usd(priced, usage, units)

    def _cost_note(self) -> dict[str, str]:
        return {}

    def _receipt_model(self, model: str) -> str:
        """The model id as written in receipts (must be an opaque id)."""
        return model

    def _settle(self, estimate: float, cost: float) -> None:
        if self.budget is not None:
            self.budget.settle(estimate, cost)

    def _send(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._http.post(
                self.base_url + path,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            # The request may have reached the API: charge the estimate (conservative).
            raise _Failure({"error": "network", "error_type": type(exc).__name__},
                           MuseError(f"{self.api_label} unreachable ({type(exc).__name__})",
                                     code="network"), cost=None) from None
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code >= 400 or not isinstance(data, dict):
            err = data.get("error") if isinstance(data, dict) else None
            code = str(err.get("type") or err.get("code") or "") if isinstance(err, dict) else ""
            raise _Failure({"error_status": resp.status_code, "error_type": code},
                           MuseError(f"{self.api_label} returned HTTP {resp.status_code} "
                                     f"({code or 'error'})", status=resp.status_code, code=code))
        return data


    def _send_stream(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST with ``stream: true``; returns the final response object plus ``_events``."""
        events: list[dict[str, Any]] = []
        try:
            with self._http.stream(
                "POST", self.base_url + path, json=body,
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    try:
                        data = resp.json()
                    except ValueError:
                        data = None
                    err = data.get("error") if isinstance(data, dict) else None
                    code = (str(err.get("type") or err.get("code") or "")
                            if isinstance(err, dict) else "")
                    raise _Failure({"error_status": resp.status_code, "error_type": code},
                                   MuseError(f"{self.api_label} returned HTTP {resp.status_code} "
                                             f"({code or 'error'})", status=resp.status_code,
                                             code=code))
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        events.append(event)
        except httpx.HTTPError as exc:
            raise _Failure({"error": "network", "error_type": type(exc).__name__},
                           MuseError(f"{self.api_label} unreachable ({type(exc).__name__})",
                                     code="network"), cost=None) from None
        final: dict[str, Any] = {}
        for event in events:
            if event.get("type") in ("response.completed", "response.incomplete",
                                     "response.failed") and isinstance(event.get("response"),
                                                                       dict):
                final = dict(event["response"])
        final["_events"] = events
        return final


class _Failure(Exception):
    """A failed call. ``cost`` is what it incurred: ``None`` = unknown (charge the
    estimate), ``0.0`` = nothing billed (HTTP error), else computed from ``usage``."""

    def __init__(
        self,
        details: dict[str, Any],
        error: MuseError,
        *,
        usage: Usage | None = None,
        cost: float | None = 0.0,
    ) -> None:
        super().__init__(error)
        self.details = details
        self.error = error
        self.usage = usage
        self.cost = cost


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _parse_responses(data: dict[str, Any]) -> dict[str, Any]:
    output = data.get("output", [])
    if not isinstance(output, list) or not isinstance(data.get("usage", {}), dict):
        raise ValueError("unexpected Responses body")
    texts: list[str] = []
    summary: list[str] = []
    calls: list[ToolCall] = []
    images: list[GeneratedImage] = []
    items = [i for i in output if isinstance(i, dict)]
    for item in items:
        kind = item.get("type")
        if kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    texts.append(str(part.get("text", "")))
        elif kind == "function_call":
            calls.append(ToolCall(str(item.get("call_id", "")), str(item.get("name", "")),
                                  str(item.get("arguments", ""))))
        elif kind == "image_generation_call" and item.get("result"):
            images.append(GeneratedImage(_image_bytes(item["result"]), str(item.get("id", ""))))
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
    # image results are returned in ``images``; keep the items light (no base64 copies)
    light = [{k: v for k, v in i.items() if k != "result"} for i in items]
    return {"text": "".join(texts), "summary": "\n".join(summary), "tool_calls": calls,
            "items": light, "usage": usage, "finish": str(data.get("status", "")),
            "images": images, "units": len(images)}


def _parse_chat(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("unexpected Chat Completions body: no choices")
    if not isinstance(data.get("usage", {}), dict):
        raise ValueError("unexpected Chat Completions body: usage")
    choice = choices[0]
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


def _image_bytes(b64: Any) -> bytes:
    if not isinstance(b64, str) or not b64:
        raise ValueError("missing image data")
    return base64.b64decode(b64, validate=True)


def _parse_images(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise ValueError("unexpected Images body: no data")
    images = [GeneratedImage(_image_bytes(i.get("b64_json"))) for i in items
              if isinstance(i, dict)]
    u = data.get("usage") or {}
    usage = Usage(input_tokens=_int(u.get("input_tokens")),
                  output_tokens=_int(u.get("output_tokens")))
    return {"text": "", "summary": "", "tool_calls": [], "items": [], "usage": usage,
            "finish": "completed", "images": images, "units": len(images)}


def _parse_stream(data: dict[str, Any]) -> dict[str, Any]:
    events = data.get("_events") or []
    if not events:
        raise ValueError("empty event stream")
    status = str(data.get("status", "")) or "eof"
    texts = [str(e.get("delta", "")) for e in events
             if e.get("type") == "response.output_text.delta"]
    if not texts:  # SAM emits the whole lane in content_part.done
        for e in events:
            part = e.get("part")
            if (e.get("type") == "response.content_part.done" and isinstance(part, dict)
                    and part.get("type") == "output_text"):
                texts.append(str(part.get("text", "")))
    u = data.get("usage") or {}
    usage = Usage(input_tokens=_int(u.get("input_tokens")),
                  output_tokens=_int(u.get("output_tokens")))
    return {"text": "".join(texts), "summary": "", "tool_calls": [], "items": [],
            "usage": usage, "finish": status, "units": 1 if status == "completed" else 0}
