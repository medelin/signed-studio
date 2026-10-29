"""Rule F1: each platform's own format limits, checked by code before the model reviewers.
Hard limits block; recommended lengths warn and name the truncation point."""

import json

import pytest

from studio.agents import formats, reviewer
from studio.agents.schemas import CopyVariant, Review
from studio.receipts import Ledger
from tests.cassettes import ROOT
from tests.images import placement_png

CASES = json.loads((ROOT / "fixtures" / "eval" / "formats.SYNTHETIC.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_seeded_format_cases(case):
    size = tuple(case["image"]) if case.get("image") else None
    found = formats.check(case["placement"], case["ad"], size)
    assert sorted({f.severity for f in found}) == sorted(case["expect"]), [
        f.evidence for f in found]


def test_every_rule_cites_an_official_url_and_the_retrieval_date():
    for spec in formats.PLATFORMS.values():
        for lim in spec["text"]:
            assert lim.source.startswith(("https://www.facebook.com/business/",
                                          "https://developers.facebook.com/",
                                          "https://support.google.com/google-ads/",
                                          "https://ads.tiktok.com/help/"))
    found = formats.check("google_rsa", {"headline": "x" * 31})
    assert "retrieved 2026-10-28" in found[0].evidence
    assert "https://support.google.com/google-ads/answer/7684791" in found[0].evidence


def test_recommended_length_warns_with_the_truncation_point():
    f = formats.check("story_9x16", {"primary_text": "A" * 40 + "BCDEFGH"})
    assert [x.severity for x in f] == ["warn"]
    assert "'" + "A" * 40 + "BCDE'…" in f[0].evidence and "Instagram Reels" in f[0].evidence


def test_google_counts_double_width_characters_twice():
    assert formats.length("広告広告", double_width=True) == 8
    assert formats.check("google_rsa", {"headline": "広" * 16})[0].severity == "block"
    assert formats.check("google_rsa", {"headline": "広" * 15}) == []


def test_meta_images_must_fit_the_placement():
    assert formats.check("feed_1x1", {}, (1600, 1600)) == []
    assert formats.check("feed_4x5", {}, (1408, 1760)) == []
    assert formats.check("story_9x16", {}, (1152, 2048)) == []
    bad = formats.check("story_9x16", {}, (1600, 1600))
    assert bad and bad[0].severity == "block" and "9:16" in bad[0].evidence
    small = formats.check("feed_4x5", {}, (480, 600))
    assert small and "at least 600×750" in small[0].evidence


def test_only_meta_is_wired_to_publishing():
    assert formats.EXPORT_ONLY == {"google_rsa", "google_pmax", "google_demand_gen",
                                   "tiktok_in_feed"}


class _Ok:
    def __init__(self):
        self.ledger = Ledger()

    def chat(self, *args, **kwargs):
        class R:
            text = json.dumps({"verdict": "OK", "findings": [], "summary": "fine"})
            finish = "stop"
        return R()


def test_format_findings_join_the_review_and_its_receipt():
    copy = CopyVariant(primary_text="Hand-thrown stoneware mugs, 350 ml, 89 PLN.",
                       headline="A mug for slow, quiet autumn mornings", description="89 PLN",
                       cta="SHOP_NOW")  # 37 > 27 recommended on Facebook Feed
    client = _Ok()
    out = reviewer.run(client, None, placement_png("feed_1x1"), copy, run_id="run_f",
                       placement="feed_1x1")
    # a recommended length is Meta's own advice: a tip with the spec URL, never a WARN
    assert out.verdict == "OK" and [f.rule for f in out.primary.findings] == ["F1"]
    tip = out.primary.findings[0]
    assert tip.severity == "tip" and tip.source.startswith("https://www.facebook.com/business/")
    r = client.ledger.receipts("run_f")[-1]
    assert r.details["format"] == ["tip"] and r.details["rules"] == ["F1"]
    wrong = reviewer.run(client, None, placement_png("feed_1x1"), copy, run_id="run_g",
                         placement="story_9x16")
    assert wrong.verdict == "BLOCK"  # a square image is not a 9:16 story


def test_without_a_placement_nothing_changes():
    model = Review.model_validate({"verdict": "OK", "findings": [], "summary": ""})
    assert reviewer.with_findings(model, []) is model
