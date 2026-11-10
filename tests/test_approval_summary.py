"""The plain-language card on the approval screen: what is being approved in words, before
the hashes. The real ``i18n.js`` and ``summary.js`` run in node against the real catalogs
(skipped without node); the page structure and the catalogs are checked everywhere."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.ui import i18n
from tests.test_assistants import BASE, account, login, make_app

UI = Path(__file__).resolve().parent.parent / "studio" / "ui"
NEEDS_NODE = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

CAMPAIGN = {"action": "campaign.create",
            "campaign": {"daily_budget_minor": 1500, "currency": "EUR", "angle": "Spring sale"}}
BLOCK = {"currency": "EUR", "amount_minor": 4725, "budget_minor": 4500, "fee_minor": 225,
         "order_id": "5O190127TN364715T"}
HOLD = {"total": "47.25", "budget": "45.00", "fee": "2.25", "currency": "EUR", "days": 3}
MANDATE = {"action": "mandate.grant", "mandate": {
    "max_daily_budget": "20", "currency": "EUR", "max_campaigns": 5,
    "valid_until": 1_900_000_000, "objectives": ["traffic"], "countries": ["PL"],
    "landing_hosts": ["example.com"], "token_ids": ["tok_1"], "funding": None}}
REFUND = {"action": "funding.refund", "refund": {
    "schema": "refund/v1", "funding_id": "fnd_1", "capture_id": "3C679366HH908993F",
    "amount_minor": 2000, "currency": "EUR"}}


def lines(lang: str, payload: dict, hold: dict | None = None, action: str | None = None):
    strings = {k[3:]: v for k, v in i18n.CATALOGS[lang].items() if k.startswith("js.")}
    block = json.dumps(json.dumps(strings))
    code = (
        "global.window = global;"
        f"global.document = {{getElementById: () => ({{textContent: {block}}})}};"
        + (UI / "static" / "i18n.js").read_text() + (UI / "static" / "summary.js").read_text()
        + f"console.log(JSON.stringify(window.ApprovalSummary.lines({json.dumps(payload)}, "
        f"{json.dumps(hold)})));")
    out = subprocess.run([shutil.which("node") or "node", "-e", code],  # noqa: S603
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def content(base: dict, **extra) -> dict:
    return {"fields": {"action": base["action"]}, "content": {**base, **extra}}


@NEEDS_NODE
def test_a_campaign_card_says_what_runs_what_is_held_and_that_nothing_is_taken():
    text = " ".join(lines("en", content(CAMPAIGN), HOLD))
    assert "Spring sale" in text and "15.00 EUR" in text and "3 days" in text
    assert "47.25 EUR" in text and "45.00 EUR" in text and "2.25 EUR" in text
    assert "PAUSED" in text and "second signature" in text
    assert "nothing is taken" in text.lower()


@NEEDS_NODE
def test_a_funded_campaign_gets_its_amounts_from_the_signed_funding_block():
    text = " ".join(lines("en", content(CAMPAIGN, funding=BLOCK)))
    assert "47.25 EUR" in text and "3 days" in text  # 4500 / 1500 days; no order call needed


@NEEDS_NODE
def test_before_the_order_exists_the_card_does_not_invent_a_total():
    text = " ".join(lines("en", content(CAMPAIGN)))
    assert "15.00 EUR" in text and "PAUSED" in text
    assert "47.25" not in text and "45.00" not in text


@NEEDS_NODE
def test_a_mandate_card_states_its_limits_in_words():
    text = " ".join(lines("en", content(MANDATE)))
    assert "5" in text and "20 EUR" in text and "paused" in text.lower()
    assert "your signature" in text


@NEEDS_NODE
def test_a_refund_card_states_the_amount_refunded():
    text = " ".join(lines("en", content(REFUND)))
    assert "20.00 EUR" in text and "refund" in text.lower()


@NEEDS_NODE
@pytest.mark.parametrize("payload,hold", [(content(CAMPAIGN), HOLD), (content(MANDATE), None),
                                          (content(REFUND), None)])
def test_the_cards_exist_in_polish_with_the_same_amounts(payload, hold):
    pl = " ".join(lines("pl", payload, hold))
    en = " ".join(lines("en", payload, hold))
    assert pl != en
    for amount in re.findall(r"\d+\.\d\d EUR|\b20 EUR", en):
        assert amount in pl


@NEEDS_NODE
def test_other_actions_get_no_card():
    assert lines("en", {"fields": {"action": "campaign.activate"},
                        "content": {"action": "campaign.activate"}}) == []


def test_the_approval_screen_has_the_card_above_open_proof_details(tmp_path):
    app = make_app(tmp_path)
    account(app, "owner")
    c = TestClient(app, base_url=BASE)
    login(c, app, "owner")
    html = c.get("/studio").text
    panel = html[html.index('id="approve-panel"'):]
    card, proof = panel.index('id="ap-summary"'), panel.index('id="ap-proof"')
    assert card < proof < panel.index('id="ap-sha"') < panel.index('id="approve-button"')
    assert re.search(r"<details[^>]*id=\"ap-proof\"[^>]*\bopen\b", panel)  # open by default
    assert i18n.text("en", "summary.proof") in panel
    assert '<script src="/static/summary.js"' in html
    assert html.index("/static/summary.js") < html.index("/static/app.js")
    assert "<script>" not in html.replace('<script type="application/json"', "")


def test_the_card_strings_exist_in_en_and_pl_with_the_same_placeholders():
    keys = [k for k in i18n.EN if k.startswith(("summary.", "js.sum_"))]
    assert len(keys) >= 8
    for k in keys:
        assert i18n.PL[k].strip() and i18n.placeholders(i18n.EN[k]) == i18n.placeholders(
            i18n.PL[k])
    assert i18n.EN["summary.proof"] == "Proof details"
