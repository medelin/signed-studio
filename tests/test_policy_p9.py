"""P9 — government documents / official services and absolute tax or legal promises.

A human-written ad ("Załóż spółkę w Anglii w 24 h. Bez ZUS") was rejected by an ad
platform under "Government documents and official services" and "Exaggerated or
inaccurate claims". The checklist carries the rule for both reviewers, and a code check
(``reviewer.policy_net``) adds a P9 BLOCK whatever the models say.
"""

import json

import pytest

from studio.agents import prompts, reviewer
from studio.agents.schemas import CopyVariant, Review
from studio.receipts import Ledger
from tests.cassettes import CASSETTES, ROOT, interactions

ADS = json.loads((ROOT / "fixtures" / "eval" / "ads.SYNTHETIC.json").read_text())["ads"]
IMAGE = (CASSETTES / "files" / "image_series-1.png").read_bytes()


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
    found = reviewer.policy_net(_copy(ad))
    assert found and all(f.rule == "P9" and f.severity == "block" for f in found)
    assert "code check" in found[0].evidence


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


def test_a_p9_ad_is_blocked_even_when_the_model_says_ok():
    ad = next(a for a in ADS if a["id"] == "p9-zaloz-spolke")
    client = _Agreeable()
    out = reviewer.run(client, None, IMAGE, _copy(ad), run_id="run_p9")
    assert out.verdict == "BLOCK"
    assert {f.rule for f in out.primary.findings} == {"P9"}
    receipt = client.ledger.receipts("run_p9")[-1]
    assert receipt.kind == "review" and receipt.details["verdict"] == "BLOCK"
    assert receipt.details["rules"] == ["P9"] and receipt.details["policy_net"] == ["P9"]


def test_net_does_not_duplicate_a_p9_finding_the_model_made():
    ad = next(a for a in ADS if a["id"] == "p9-no-zus")
    model = Review.model_validate({"verdict": "BLOCK", "summary": "x", "findings": [
        {"rule": "P9", "evidence": "No ZUS obligation", "severity": "block"}]})
    merged = reviewer.with_policy_net(model, _copy(ad))
    assert [f.rule for f in merged.findings] == ["P9"]
