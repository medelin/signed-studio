"""A2: what the copywriter left out to keep the ad within Meta's rules is shown to the
owner (their words, the rule in one sentence, the policy link, the phrasing used), signed
with the ad, and never removed silently."""

import json

from studio.agents import copywriter, producer, rules
from studio.agents.schemas import CopyPack, CreativeImage, CreativeSet
from studio.ui import i18n
from tests.cassettes import ROOT
from tests.test_producer_content import PLAN, _variant

UI = ROOT / "studio" / "ui"


def _pack(**extra):
    return CopyPack.model_validate({"variants": [_variant("A"), _variant("B"), _variant("C")],
                                    "risks": ["P1: no weight claims"], "unfilled_facts": 0,
                                    **extra})


def test_a_recording_with_only_free_text_risks_still_parses():
    pack = _pack()
    assert pack.left_out == [] and pack.risks == ["P1: no weight claims"]


def test_the_model_must_fill_left_out_in_the_strict_schema():
    from studio.muse.structured import strict_schema

    schema = strict_schema(CopyPack)
    assert "left_out" in schema["required"]
    item = schema["properties"]["left_out"]["items"]
    assert set(item["required"]) == {"asked", "rule", "source", "instead"}


def test_code_sets_rule_id_and_source_from_the_table():
    pack = copywriter.explain_left_out(_pack(left_out=[
        {"asked": "Masz nadwagę? Schudnij do świąt", "rule": "P1 Personal attributes",
         "source": "https://evil.example/", "instead": "Dla tych, którzy chcą poczuć się lżej"},
        {"asked": "", "rule": "P3", "source": "", "instead": "x"},
        {"asked": "lowers blood pressure", "rule": "health", "source": "", "instead": ""}]))
    assert [x.rule for x in pack.left_out] == ["P1", "UNMAPPED"]
    assert pack.left_out[0].source == rules.RULES["P1"].source
    assert pack.left_out[1].source == rules.UNMAPPED.source  # never the model's URL


def test_the_signed_payload_carries_what_was_left_out():
    from studio.agents.schemas import Plan

    run = producer.RunResult(run_id="run-a2", status="running")
    run.plan = Plan.model_validate(PLAN)
    run.copy = copywriter.explain_left_out(_pack(left_out=[
        {"asked": "guaranteed", "rule": "P3", "source": "", "instead": "made to last"}]))
    img = CreativeImage(placement="feed_4x5", size="1x1", path="x", sha256="0" * 64,
                        width=1, height=1, response_id="")
    run.creatives = CreativeSet(cutout_path="c", cutout_sha256="0" * 64, foreground_pixels=1,
                                images=[img], prompts=[], response_ids=[])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [], "variant_index": 0}
    payload = producer.campaign_payload(run, 0)
    assert payload["left_out"] == [{"asked": "guaranteed", "rule": "P3",
                                    "source": rules.RULES["P3"].source,
                                    "instead": "made to last"}]
    run.copy = _pack()
    assert "left_out" not in producer.campaign_payload(run, 0)  # old hashes unchanged


def test_approval_screen_has_the_box_and_the_link_back_to_the_brief():
    html = (UI / "templates" / "studio.html").read_text()
    assert 'id="ap-left-out"' in html and "studio.ap.left_out" in html
    js = (UI / "static" / "app.js").read_text()
    assert "showLeftOut(data.content" in js and '"#brief-panel"' in js
    assert "left_out_notes" in js  # free-text risks of older recordings: plain lines


def test_every_rule_sentence_exists_in_both_languages():
    for r in rules.RULES.values():
        assert i18n.EN[f"js.rule_{r.id}"] == r.en
        assert i18n.PL[f"js.rule_{r.id}"] == r.pl
    assert "Meta" in i18n.EN["studio.ap.left_out"] and "Meta" in i18n.PL["studio.ap.left_out"]


def test_copywriter_prompt_asks_for_left_out_with_its_fields():
    text = (ROOT / "studio" / "agents" / "prompts" / "copywriter.md").read_text()
    for key in ("left_out", "asked", "rule", "source", "instead"):
        assert f"`{key}`" in text
    assert json.dumps("Never drop a claim silently")[1:-1] in text
