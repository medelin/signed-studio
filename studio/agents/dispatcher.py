"""Dispatcher: the only path from a model's tool call to code.

- The producer is offered tools only for model-callable skills whose ``side_effects`` is
  ``none`` or ``local`` (``skills.yaml``); an external skill has no tool at all.
- A call naming anything else — an external skill such as ``create_campaign``, an unknown
  name, a skill without a handler — raises ``ToolNotAvailable`` and leaves a ``tool_call``
  receipt with ``status: rejected``. The rejected name is recorded only if it is an opaque
  id; otherwise only its hash is kept.
- Arguments are validated against a closed pydantic model before the handler runs.
- Every executed call leaves a ``tool_call`` receipt (``ok`` or ``error``) with hashes of the
  arguments and the output.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from studio.agents.skills import Skill, load, model_callable
from studio.muse.client import ToolCall
from studio.muse.structured import strict_schema
from studio.receipts import Ledger, is_opaque, sha256_hex

MAX_ARGS = 4000


class ToolNotAvailable(Exception):
    """The model asked for a tool it does not have (external, unknown or unhandled)."""


class ToolError(Exception):
    """A tool ran but refused the request (e.g. a precondition); reported to the model."""


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CopywriterArgs(_Args):
    angle_index: int


class ArtDirectorArgs(_Args):
    headline: str


class ReviewersArgs(_Args):
    variant_index: int


class ApprovalArgs(_Args):
    summary: str


ARGS: dict[str, type[_Args]] = {
    "run_copywriter": CopywriterArgs,
    "run_art_director": ArtDirectorArgs,
    "run_reviewers": ReviewersArgs,
    "request_approval": ApprovalArgs,
}

Handler = Callable[[Any], dict[str, Any]]


@dataclass
class Outcome:
    call_id: str
    name: str
    status: str  # ok | error | rejected
    output: dict[str, Any]

    def as_input(self) -> dict[str, Any]:
        """Responses API ``function_call_output`` item for the next turn."""
        return {"type": "function_call_output", "call_id": self.call_id,
                "output": json.dumps(self.output, ensure_ascii=False)}


class Dispatcher:
    def __init__(
        self,
        handlers: dict[str, Handler],
        *,
        ledger: Ledger,
        run_id: str,
        registry: dict[str, Skill] | None = None,
    ) -> None:
        self.registry = registry if registry is not None else load()
        allowed = {s.name for s in model_callable(self.registry)}
        # A handler for an external or unknown skill is never wired up.
        self.handlers = {n: h for n, h in handlers.items() if n in allowed and n in ARGS}
        self.ledger = ledger
        self.run_id = run_id

    def tools(self) -> list[dict[str, Any]]:
        """Function tools for the producer: exactly the wired model-callable skills."""
        return [{
            "type": "function", "name": name,
            "description": self.registry[name].description,
            "parameters": strict_schema(ARGS[name]),
        } for name in self.handlers]

    def _receipt(self, call: ToolCall, status: str, output: Any = None,
                 reason: str = "") -> None:
        name = call.name if is_opaque(call.name) else "unknown"
        details: dict[str, Any] = {"tool": name, "status": status, "call_id_sha256":
                                   sha256_hex(call.call_id)}
        if name != call.name:
            details["tool_sha256"] = sha256_hex(call.name)
        skill = self.registry.get(call.name)
        if skill is not None:
            details["side_effects"] = skill.side_effects
        if reason:
            details["reason"] = reason
        self.ledger.append(
            self.run_id, agent="dispatcher", kind="tool_call",
            input_sha256=[sha256_hex(call.arguments)],
            output_sha256=sha256_hex(json.dumps(output, sort_keys=True)) if output else "",
            details=details,
        )

    def execute(self, call: ToolCall) -> Outcome:
        """Run one call. ``ToolNotAvailable`` for anything not wired (after its receipt)."""
        handler = self.handlers.get(call.name)
        if handler is None:
            skill = self.registry.get(call.name)
            reason = ("external skill: needs a signed human approval, not a model call"
                      if skill is not None and skill.side_effects == "external"
                      else "no such tool")
            self._receipt(call, "rejected", reason=reason)
            raise ToolNotAvailable(f"tool not available: {call.name[:60]!r}")
        try:
            if len(call.arguments) > MAX_ARGS:
                raise ValueError("arguments too long")
            raw = json.loads(call.arguments or "{}")
            args = ARGS[call.name].model_validate(raw)
        except (ValueError, ValidationError):
            out = {"error": "invalid arguments"}
            self._receipt(call, "error", out, reason="invalid arguments")
            return Outcome(call.call_id, call.name, "error", out)
        try:
            out = handler(args)
        except ToolError as exc:
            out = {"error": str(exc)[:300]}
            self._receipt(call, "error", out, reason="tool refused")
            return Outcome(call.call_id, call.name, "error", out)
        self._receipt(call, "ok", out)
        return Outcome(call.call_id, call.name, "ok", out)

    def refuse(self, call: ToolCall, reason: str) -> Outcome:
        """Not dispatched (a limit of the loop): a ``rejected`` receipt and an error output.

        ``reason`` is a fixed string chosen by code, never model text."""
        out = {"error": reason}
        self._receipt(call, "rejected", out, reason=reason)
        return Outcome(call.call_id, call.name, "rejected", out)

    def execute_or_refuse(self, call: ToolCall) -> Outcome:
        """Like ``execute``, but a missing tool becomes an error output for the model."""
        try:
            return self.execute(call)
        except ToolNotAvailable:
            return Outcome(call.call_id, call.name, "rejected",
                           {"error": "tool not available; external actions need a human "
                                     "signature and cannot be called"})
