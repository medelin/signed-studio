"""System prompts: a stable prefix (brand sheet + policy checklist), then the agent's role.

The prefix is byte-identical for every agent and every call, so the Model API can cache
it; anything that varies (brief, copy, comment) goes into the user message, never here.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"
AGENTS = ("producer", "copywriter", "art_director", "reviewer", "community", "lead_scorer",
          "analyst")
_RULE = re.compile(r"\*\*(P\d+)\b")


@cache
def _read(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


def brand_sheet() -> str:
    return _read("_brand")


def policy() -> str:
    return _read("_policy")


def stable_prefix() -> str:
    return f"{brand_sheet()}\n\n{policy()}\n\n"


def system(agent: str) -> str:
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}")
    return stable_prefix() + _read(agent)


def policy_rule_ids() -> list[str]:
    return _RULE.findall(policy())
