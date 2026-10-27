"""Strict JSON-schema structured output on top of Chat Completions."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from studio.muse.client import MuseClient, MuseError, MuseResult

T = TypeVar("T", bound=BaseModel)
_DROP = frozenset({"title", "default", "examples"})


class StructuredOutputError(MuseError):
    """The model returned something that does not validate against the schema."""


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    if not isinstance(node, dict):
        return node
    if "allOf" in node or "oneOf" in node:
        raise ValueError("strict schemas cannot use allOf/oneOf")
    named = ("properties", "$defs")  # keys of these maps are names, not keywords
    out = {k: _strictify(v) for k, v in node.items() if k not in _DROP and k not in named}
    for key in named:
        if key in node:
            out[key] = {name: _strictify(v) for name, v in node[key].items()}
    if out.get("type") == "object" or "properties" in out:
        props = out.get("properties", {})
        out["type"] = "object"
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False
    return out


def _inline(node: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """Replace local ``$ref``s with their definitions (the strict subset is safest flat)."""
    if depth > 32:
        raise ValueError("schema nesting too deep (recursive model?)")
    if isinstance(node, list):
        return [_inline(v, defs, depth) for v in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.removeprefix("#/$defs/")
        if name == ref or name not in defs:
            raise ValueError(f"unsupported $ref {ref!r}")
        return _inline(defs[name], defs, depth + 1)
    return {k: _inline(v, defs, depth) for k, v in node.items() if k != "$defs"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema rewritten to the strict subset: every object closed, every key
    required, no titles/defaults, no allOf/oneOf, ``$ref``s inlined, plain-object root."""
    raw = copy.deepcopy(model.model_json_schema())
    schema = _strictify(_inline(raw, raw.get("$defs", {})))
    if schema.get("type") != "object" or any(k in schema for k in ("anyOf", "enum", "not")):
        raise ValueError("strict schema root must be a plain object")
    return dict(schema)


def response_format(model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": model.__name__, "strict": True, "schema": strict_schema(model)},
    }


def structured(
    client: MuseClient,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    schema: type[T],
    *,
    tier: str,
    tier_reason: str,
    run_id: str,
    agent: str,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    envelope_sources: Sequence[str] = (),
) -> tuple[T, MuseResult]:
    """Call ``model`` with a strict schema and return the validated object and the result."""
    result = client.chat(
        model, messages, run_id=run_id, agent=agent, response_format=response_format(schema),
        max_tokens=max_tokens, reasoning_effort=reasoning_effort, tier=tier,
        tier_reason=tier_reason, envelope_sources=envelope_sources,
    )
    try:
        return schema.model_validate_json(result.text), result
    except ValidationError as exc:
        raise StructuredOutputError(
            f"model output does not match {schema.__name__} "
            f"(finish={result.finish or 'unknown'}, {exc.error_count()} errors)",
            code="invalid_output",
        ) from None
