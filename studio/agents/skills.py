"""Skill registry (``skills.yaml``): what each callable may do to the world.

The loader fails closed: unknown side-effect classes, duplicate names, or an external skill
marked model-callable are a ``SkillRegistryError`` at startup, not a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from studio.approvals import ACTIONS

SKILLS_FILE = Path(__file__).resolve().parent / "skills.yaml"
SIDE_EFFECTS = frozenset({"none", "local", "external"})


class SkillRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    agent: str
    side_effects: str
    model_callable: bool
    description: str
    action: str = ""


def _skill(raw: Any) -> Skill:
    if not isinstance(raw, dict):
        raise SkillRegistryError("each skill must be a mapping")
    allowed = {"name", "agent", "side_effects", "model_callable", "description", "action"}
    if set(raw) - allowed:
        raise SkillRegistryError(f"unknown skill keys: {sorted(set(raw) - allowed)}")
    try:
        skill = Skill(
            name=str(raw["name"]), agent=str(raw["agent"]),
            side_effects=str(raw["side_effects"]),
            model_callable=raw["model_callable"], description=str(raw["description"]),
            action=str(raw.get("action", "")),
        )
    except KeyError as exc:
        raise SkillRegistryError(f"skill is missing {exc.args[0]!r}") from None
    if not isinstance(skill.model_callable, bool):
        raise SkillRegistryError(f"{skill.name}: model_callable must be a boolean")
    if skill.side_effects not in SIDE_EFFECTS:
        raise SkillRegistryError(f"{skill.name}: unknown side_effects {skill.side_effects!r}")
    if skill.side_effects == "external":
        if skill.model_callable:
            raise SkillRegistryError(f"{skill.name}: an external skill cannot be model-callable")
        if skill.action not in ACTIONS:
            raise SkillRegistryError(f"{skill.name}: external skill needs an approval action")
    elif skill.action:
        raise SkillRegistryError(f"{skill.name}: only external skills map to an action")
    return skill


def load(path: Path = SKILLS_FILE) -> dict[str, Skill]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise SkillRegistryError("skills.yaml must contain a 'skills' list")
    registry: dict[str, Skill] = {}
    for raw in data["skills"]:
        skill = _skill(raw)
        if skill.name in registry:
            raise SkillRegistryError(f"duplicate skill {skill.name!r}")
        registry[skill.name] = skill
    return registry


def model_callable(registry: dict[str, Skill]) -> list[Skill]:
    """Skills the producer may see as tools: never an external one."""
    return [s for s in registry.values() if s.model_callable and s.side_effects != "external"]
