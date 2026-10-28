"""Brief v1 and the objective table: the sub-choice, the ids each objective needs (a field
error, never a guess) and the grounding of ids in the owner's words."""

import pytest

from studio import brief as bv
from tests.test_brief import sample
from tests.test_objectives import IDS


@pytest.mark.parametrize(("goal", "sub", "needs"), [
    ("traffic", "", set()), ("traffic", "landing_page_views", set()),
    ("awareness", "ad_recall_lift", set()), ("engagement", "whatsapp", set()),
    ("engagement", "event_responses", {"event_id"}),
    ("leads", "", {"privacy_policy_url"}), ("leads", "website", {"pixel_id"}),
    ("app_promotion", "", {"application_id", "object_store_url"}),
    ("sales", "", {"pixel_id"}),
    ("sales", "catalog", {"pixel_id", "product_catalog_id", "product_set_id"})])
def test_each_objective_asks_for_exactly_its_ids(goal, sub, needs):
    b = sample(objective={"goal": goal, "kpi": "x", "sub": sub}, promotion={})
    got = {k.split(".")[1] for k, v in bv.problems(b).items()
           if v == "required_for_objective"}
    assert got == needs
    full = sample(objective={"goal": goal, "kpi": "x", "sub": sub}, promotion=IDS)
    assert bv.problems(full) == {}


def test_a_sub_choice_of_another_goal_is_a_field_error():
    b = sample(objective={"goal": "traffic", "kpi": "x", "sub": "catalog"})
    assert bv.problems(b)["objective.sub"] == "bad_sub"


def test_old_briefs_without_the_new_fields_still_validate():
    data = sample().model_dump(mode="json")
    data.pop("promotion")
    data["objective"].pop("sub")
    b = bv.BriefV1.model_validate(data)
    assert b.objective.sub == "" and b.promotion.pixel_id == ""


def test_ids_the_owner_never_wrote_are_cleared_from_the_draft():
    draft = sample(objective={"goal": "sales", "kpi": "x"},
                   promotion={"pixel_id": "123456789", "privacy_policy_url":
                              "https://clay.example/privacy"})
    grounded = bv.ground_draft(draft, "Sprzedaż, piksel 987654321, "
                               "https://clay.example/privacy 2 180 10:00 14:00 8 2026 11 30 "
                               "40 03", "pl")
    assert grounded.promotion.pixel_id == ""  # never in the owner's words: not a guess
    assert grounded.promotion.privacy_policy_url == "https://clay.example/privacy"
    assert bv.problems(grounded)["promotion.pixel_id"] == "required_for_objective"


def test_render_names_the_sub_choice():
    text = bv.render(sample(objective={"goal": "engagement", "kpi": "x", "sub": "messenger"}))
    assert "Objective: engagement (messenger); KPI: x" in text
    assert "Objective: leads; KPI:" in bv.render(sample())
