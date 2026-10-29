"""Finding levels by source (``studio.agents.rules``): Muse explains Meta's rules, it does
not invent new ones. One table in code decides every finding's level and source."""

import pytest

from studio.agents import prompts, reviewer, rules
from studio.agents.schemas import CopyVariant, Finding, Review

META, BOTH = ("meta",), ("meta", "google")


def test_every_checklist_rule_has_a_row_with_a_source():
    for rule_id in [*prompts.policy_rule_ids(), "P10", "F1", "S1", "S2"]:
        row = rules.RULES[rule_id]
        assert row.source, rule_id
        assert row.en and row.pl and row.en != row.pl


def test_blocks_on_meta_cite_a_meta_page_or_the_grounding_rule():
    for row in rules.RULES.values():
        if row.caps["meta"] == "block":
            assert row.source.startswith(("https://transparency.meta.com/",
                                          "https://www.facebook.com/business/")) \
                or row.source == rules.GROUNDING, row.id


@pytest.mark.parametrize(("rule_id", "channels", "level"), [
    ("P9", META, "tip"), ("P9", BOTH, "block"), ("P5", META, "tip"), ("P5", BOTH, "tip"),
    ("P1", META, "block"), ("P10", META, "block"), ("S2", META, "tip"),
    ("UNMAPPED", META, "warn"),
])
def test_caps(rule_id, channels, level):
    assert rules.level(rule_id, "block", channels) == level


def test_a_model_level_below_the_cap_is_kept_and_info_stays_a_note():
    assert rules.level("P1", "warn") == "warn"
    assert rules.level("P1", "tip") == "tip"
    assert rules.level("P9", "info", BOTH) == "info"


def test_f1_recommended_lengths_are_tips_and_hard_limits_blocks():
    assert rules.level("F1", "warn") == "tip"
    assert rules.level("F1", "block") == "block"
    ev = "Instagram Reels: primary_text is 94 characters, recommended length 44 (" \
         "https://www.facebook.com/business/ads-guide/update/image/instagram-reels, retrieved"
    assert rules.source("F1", ev).endswith("/instagram-reels")


def test_tips_never_raise_the_verdict():
    assert rules.verdict(["tip", "info", "tip"]) == "OK"
    assert rules.verdict(["tip", "warn"]) == "WARN"
    assert rules.verdict(["block", "tip"]) == "BLOCK"


def _review(verdict, *findings):
    return Review(verdict=verdict, summary="",
                  findings=[Finding(rule=r, severity=s, evidence="x") for r, s in findings])


def test_normalize_caps_model_findings_and_sets_sources():
    out = reviewer.normalize(_review("WARN", ("P5 Brand", "warn")))
    assert out.verdict == "OK"
    assert out.findings[0].severity == "tip" and out.findings[0].source
    out = reviewer.normalize(_review("BLOCK", ("P9", "block")), BOTH)
    assert out.verdict == "BLOCK" and "support.google.com" in out.findings[0].source


def test_a_model_verdict_without_findings_is_warn_at_most():
    assert reviewer.normalize(_review("BLOCK")).verdict == "WARN"
    assert reviewer.normalize(_review("OK")).verdict == "OK"


def test_grounding_net_is_a_block_with_the_grounding_source():
    from studio import brief as brief_v1
    from tests.test_brief import sample

    b = sample()
    copy = CopyVariant(primary_text="Only 7 left, 99 zl", headline="Mug", description="x",
                       cta="SHOP_NOW")
    found = reviewer.grounding_net(copy, b)
    if not brief_v1.ungrounded_numbers("Only 7 left, 99 zl", brief_v1.grounding_corpus(b)):
        pytest.skip("sample brief confirms these numbers")
    assert found and all(f.severity == "block" and f.source == rules.GROUNDING for f in found)


def test_tips_are_not_disagreements():
    a = _review("OK", ("P5", "tip"))
    b = _review("OK")
    assert reviewer.reconcile(reviewer.normalize(a), reviewer.normalize(b)).disagreements == []
