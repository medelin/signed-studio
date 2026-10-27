"""Agent schemas, prompts and the skill registry (D2-1)."""

import pytest
import yaml
from pydantic import ValidationError

from studio.agents import prompts, schemas, skills


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


@pytest.mark.parametrize("model", schemas.MODEL_OUTPUTS)
def test_strict_schema_is_closed_and_fully_required(model):
    schema = schemas.strict_schema(model)
    objects = [n for n in _walk(schema) if n.get("type") == "object"]
    assert objects
    for node in objects:
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])
    text = repr(schema)
    assert "$ref" not in text and "$defs" not in text  # inlined for the strict subset
    assert "anyOf" not in text and "default" not in text


def test_plan_needs_three_angles():
    angle = {"name": "a", "hook": "h", "persona": "p"}
    base = {"audience": "x", "budget_pln_daily": 30, "placements": ["feed_1x1"],
            "tasks": ["copy"]}
    schemas.Plan.model_validate({**base, "angles": [angle] * 3})
    with pytest.raises(ValidationError):
        schemas.Plan.model_validate({**base, "angles": [angle] * 2})


def test_copy_pack_needs_three_variants():
    v = {"primary_text": "t", "headline": "h", "description": "d", "cta": "LEARN_MORE"}
    schemas.CopyPack.model_validate({"variants": [v] * 3, "risks": []})
    with pytest.raises(ValidationError):
        schemas.CopyPack.model_validate({"variants": [v], "risks": []})


def test_report_has_at_most_three_recommendations():
    r = {"summary": "s", "kpis": [], "wins": [], "losses": [], "evidence_refs": [],
         "recommendations": ["a", "b", "c", "d"]}
    with pytest.raises(ValidationError):
        schemas.Report.model_validate(r)


def test_review_verdict_is_an_enum():
    with pytest.raises(ValidationError):
        schemas.Review.model_validate({"verdict": "MAYBE", "findings": [], "summary": ""})


def test_skills_yaml_validates_and_no_external_skill_is_model_callable():
    registry = skills.load()
    assert {"run_copywriter", "run_art_director", "run_reviewers",
            "request_approval"} <= set(registry)
    external = [s for s in registry.values() if s.side_effects == "external"]
    assert external, "external skills must be declared so the dispatcher can refuse them"
    for s in external:
        assert not s.model_callable
        assert s.action  # each maps to an approval action
    assert all(s.side_effects in {"none", "local", "external"} for s in registry.values())
    callable_names = {s.name for s in skills.model_callable(registry)}
    assert callable_names.isdisjoint({s.name for s in external})


def test_skills_loader_refuses_a_model_callable_external_skill(tmp_path):
    bad = tmp_path / "skills.yaml"
    bad.write_text(yaml.safe_dump({"skills": [{
        "name": "create_campaign", "agent": "media_buyer", "side_effects": "external",
        "model_callable": True, "action": "campaign.create", "description": "x",
    }]}))
    with pytest.raises(skills.SkillRegistryError):
        skills.load(bad)


def test_skills_loader_refuses_unknown_side_effects(tmp_path):
    bad = tmp_path / "skills.yaml"
    bad.write_text(yaml.safe_dump({"skills": [{
        "name": "x", "agent": "a", "side_effects": "sometimes", "model_callable": False,
        "description": "x",
    }]}))
    with pytest.raises(skills.SkillRegistryError):
        skills.load(bad)


@pytest.mark.parametrize("agent", prompts.AGENTS)
def test_system_prompt_starts_with_the_stable_prefix(agent):
    text = prompts.system(agent)
    prefix = prompts.stable_prefix()
    assert text.startswith(prefix)  # identical bytes first: prompt caching
    assert len(text) > len(prefix)
    assert "P1" in prefix  # policy checklist rule ids


def test_policy_rule_ids_are_unique():
    ids = prompts.policy_rule_ids()
    assert len(ids) >= 6 and len(ids) == len(set(ids))
