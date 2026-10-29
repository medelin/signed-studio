"""D4-4: the README a judge reads in two minutes stays true to the code."""

from pathlib import Path

import pytest

from studio.muse import openweights
from studio.muse.client import TOKEN_PRICES, UNIT_PRICES

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()


def model_ids_in_code():
    return sorted({*TOKEN_PRICES, *UNIT_PRICES, openweights.MODEL})


@pytest.mark.parametrize("model", model_ids_in_code())
def test_readme_lists_every_model_id_used_in_code(model):
    assert f"`{model}`" in README


def test_readme_has_the_judge_sections():
    for needle in ("https://muse.udun.eu", "built during the event (27 Oct – 4 Nov 2026)",
                   "make demo-replay", "docs/eval.md", "docs/architecture.md",
                   "docs/demo-script.md", "docs/SECURITY.md", "docs/build-log/",
                   "Muse Code", "studio verify"):
        assert needle in README, needle


@pytest.mark.parametrize("doc", ["architecture.md", "demo-script.md", "SECURITY.md",
                                 "eval.md"])
def test_docs_exist(doc):
    assert (ROOT / "docs" / doc).stat().st_size > 500


def test_architecture_has_a_diagram():
    assert "```mermaid" in (ROOT / "docs" / "architecture.md").read_text()


def test_demo_script_opens_with_what_was_built_and_has_the_p9_beat():
    text = (ROOT / "docs" / "demo-script.md").read_text()
    first = next(line for line in text.splitlines() if line.startswith("**First line"))
    assert "from scratch" in first
    assert "P9" in text and "15-second" in text


def test_security_names_the_residual_risks():
    text = (ROOT / "docs" / "SECURITY.md").read_text()
    assert "TOFU" in text and "Heuristic PII" in text


def test_policy_doc_cites_official_sources_and_explains_p9():
    text = (ROOT / "docs" / "policy.md").read_text()
    for url in ("https://www.facebook.com/business/ads-guide/update/image/facebook-feed",
                "https://support.google.com/google-ads/answer/7684791",
                "https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads"):
        assert url in text
    assert "retrieved **2026-10-28**" in text and "model-invented legal/tax claim" in text
    assert "docs/policy.md" in README
