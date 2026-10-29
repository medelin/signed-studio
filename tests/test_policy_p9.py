"""P9 — government documents / official services and absolute tax or legal promises.

A human-written ad ("Załóż spółkę w Anglii w 24 h. Bez ZUS") was rejected by an ad
platform under "Government documents and official services" and "Exaggerated or
inaccurate claims". The checklist carries the rule for both reviewers, and a code check
(``reviewer.policy_net``) adds a P9 finding whatever the models say.

P9 is a Google Ads policy, not a Meta one (docs/scenarios.md): it is a BLOCK when the copy
also runs on Google Search (``channels`` meta + google), and a tip with its Google source
on a Meta-only campaign (``rules.RULES``).
"""

import json

import pytest

from studio.agents import prompts, reviewer
from studio.agents.schemas import CopyVariant, Review
from studio.receipts import Ledger
from tests.cassettes import CASSETTES, ROOT, interactions

ADS = json.loads((ROOT / "fixtures" / "eval" / "ads.SYNTHETIC.json").read_text())["ads"]
IMAGE = (CASSETTES / "files" / "image_series-1.png").read_bytes()
GOOGLE = ("meta", "google")


def _copy(ad):
    return CopyVariant(**ad["copy"])


def test_checklist_has_p9_for_both_reviewers():
    assert "P9" in prompts.policy_rule_ids()
    text = prompts.policy()
    assert "Government documents and official services" in text
    assert "Exaggerated or inaccurate claims" in text
    assert "P1-P9" in prompts.system("reviewer")


@pytest.mark.parametrize("ad", [a for a in ADS if a["expect"] == "P9"], ids=lambda a: a["id"])
def test_net_flags_every_p9_ad(ad):
    found = reviewer.policy_net(_copy(ad), GOOGLE)
    assert found and all(f.rule == "P9" and f.severity == "block" for f in found)
    assert "code check" in found[0].evidence
    assert found[0].source.startswith("https://support.google.com/adspolicy/")


@pytest.mark.parametrize("ad", [a for a in ADS if a["expect"] == "P9"], ids=lambda a: a["id"])
def test_on_meta_alone_p9_is_a_tip_with_its_google_source(ad):
    found = reviewer.policy_net(_copy(ad))
    assert found and all(f.rule == "P9" and f.severity == "tip" for f in found)
    assert "support.google.com" in found[0].source


@pytest.mark.parametrize("ad", [a for a in ADS if a["expect"] != "P9"], ids=lambda a: a["id"])
def test_net_leaves_other_ads_to_the_models(ad):
    assert reviewer.policy_net(_copy(ad)) == []


def test_net_leaves_the_recorded_demo_copy_alone():
    from studio.agents.schemas import CopyPack

    pack = CopyPack.model_validate_json(interactions("copy_contributor")[0]["response"]["json"][
        "choices"][0]["message"]["content"])
    assert all(reviewer.policy_net(v) == [] for v in pack.variants)


class _Agreeable:
    """A model that approves anything (the worst case for the net)."""

    def __init__(self):
        self.ledger = Ledger()

    def chat(self, *args, **kwargs):
        class R:
            text = json.dumps({"verdict": "OK", "findings": [], "summary": "Looks fine."})
            finish = "stop"
        return R()


def test_a_p9_ad_on_meta_only_is_ok_with_a_tip():
    ad = next(a for a in ADS if a["id"] == "p9-zaloz-spolke")
    out = reviewer.run(_Agreeable(), None, IMAGE, _copy(ad), run_id="run_p9m")
    assert out.verdict == "OK"
    assert {(f.rule, f.severity) for f in out.primary.findings} == {("P9", "tip")}


def test_a_p9_ad_is_blocked_on_google_even_when_the_model_says_ok():
    ad = next(a for a in ADS if a["id"] == "p9-zaloz-spolke")
    client = _Agreeable()
    out = reviewer.run(client, None, IMAGE, _copy(ad), run_id="run_p9", channels=GOOGLE)
    assert out.verdict == "BLOCK"
    assert {f.rule for f in out.primary.findings} == {"P9"}
    receipt = client.ledger.receipts("run_p9")[-1]
    assert receipt.kind == "review" and receipt.details["verdict"] == "BLOCK"
    assert receipt.details["rules"] == ["P9"] and receipt.details["policy_net"] == ["P9"]


def test_net_does_not_duplicate_a_p9_finding_the_model_made():
    ad = next(a for a in ADS if a["id"] == "p9-no-zus")
    model = Review.model_validate({"verdict": "BLOCK", "summary": "x", "findings": [
        {"rule": "P9", "evidence": "No ZUS obligation", "severity": "block"}]})
    merged = reviewer.with_policy_net(model, _copy(ad), GOOGLE)
    assert [f.rule for f in merged.findings] == ["P9"]


@pytest.mark.parametrize("severity", ["info", "warn"])
def test_a_weak_model_p9_finding_does_not_switch_the_net_off_on_google(severity):
    ad = next(a for a in ADS if a["id"] == "p9-no-zus")
    model = Review.model_validate({"verdict": "OK", "summary": "x", "findings": [
        {"rule": "P9", "evidence": "mentions ZUS", "severity": severity}]})
    merged = reviewer.with_policy_net(model, _copy(ad), GOOGLE)
    assert merged.verdict == "BLOCK"
    assert any(f.rule == "P9" and f.severity == "block" for f in merged.findings)


def test_recorded_spark_review_blocks_the_p9_ad_on_its_own():
    """Real Muse Spark 1.3 vision call (cassette ``review_p9``), before the code check."""
    import respx

    from studio.muse.client import BASE_URL, MuseClient
    from studio.settings import Budget
    from tests.cassettes import FAKE_KEY, load, mount

    assert load("review_p9")["_provenance"] == "recorded"
    ad = next(a for a in ADS if a["id"] == "p9-zaloz-spolke")
    image = (ROOT / "fixtures" / "eval" / "product.SYNTHETIC.png").read_bytes()
    client = MuseClient(FAKE_KEY, ledger=Ledger(), budget=Budget(1.0))
    with respx.mock() as router:
        sent = mount(router, "review_p9", BASE_URL)
        result = reviewer.review(client, image, _copy(ad), run_id="run_p9", channels=GOOGLE)
    assert result.verdict == "BLOCK"
    assert any(f.rule == "P9" and f.severity == "block" for f in result.findings)
    assert "P9" in sent[0]["messages"][0]["content"]  # the checklist the model was given


@pytest.mark.parametrize("text", [
    # The human-written ads Google Ads disapproved (operator's account, verbatim).
    "Zmęczony wysokimi kosztami, podatkami ? Załóż spółkę LTD z oddziałem z o.o. lub "
    "przenieś firmę do UK",
    "Załóż własną spółkę LTD lub przenieś swoją firmę z Polski do Anglii, zredukuj koszty "
    "i podatki.",
    "Niskie podatki, brak obowiązku ZUS",
])
def test_net_catches_the_ads_an_ad_platform_rejected(text):
    copy = CopyVariant(primary_text=text, headline="Spółka LTD", description="UK",
                       cta="LEARN_MORE")
    assert reviewer.policy_net(copy, GOOGLE)[0].severity == "block"
