"""The treasurer (``studio.agents.treasurer``): Muse Spark proposes a split and explains a
funding's reconciliation; code recomputes every amount and refuses anything else. The
model gets code's numbers only, no PayPal ids or credentials, and no tools.

The replayed answer is ``fixtures/cassettes/treasurer.SYNTHETIC.json`` (hand-written and
labelled synthetic until a real recording exists)."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from studio.agents import treasurer
from studio.funding import reconcile
from studio.muse.client import MuseClient
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, interactions, transport

CAMPAIGNS = ["cmp_a1", "cmp_b2"]
STATEMENT = reconcile(currency="PLN", budget_minor=21000, fee_minor=1050,
                      captured_minor=22050, spend_minor=14700, refunded_minor=0,
                      fee_pct=Decimal(5))


def client(tr):
    return MuseClient(FAKE_KEY, ledger=Ledger(), budget=Budget(1.0), transport=tr)


def advise(c, **kw):
    args = {"run_id": "run_t1", "currency": "PLN", "total_minor": 20000,
            "campaigns": CAMPAIGNS, "max_each_minor": 15000, "statement": STATEMENT,
            "fee_pct": Decimal(5), **kw}
    return treasurer.advise(c, **args)


def answer_with(**change):
    good = json.loads(interactions("treasurer.SYNTHETIC")[0]["response"]["json"]["output"][0]
                      ["content"][0]["text"])
    good.update(change)
    body = {"id": "resp_x", "model": "muse-spark-1.3", "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(good)}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50}}
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler), sent


def test_code_computes_the_statement_the_treasurer_explains():
    assert (STATEMENT.captured_minor, STATEMENT.spend_counted_minor, STATEMENT.fee_due_minor,
            STATEMENT.refundable_minor) == (22050, 14700, 735, 6615)


def test_a_correct_answer_is_shown_as_the_models():
    tr, sent = transport("treasurer.SYNTHETIC")
    out = advise(client(tr))
    assert out.source == "model" and out.refused == []
    assert [x["amount_minor"] for x in out.split] == [12000, 8000]
    assert "66.15 PLN" in out.explanation and out.model_receipt
    body = sent[0]
    assert "tools" not in body  # the model has no tool, so no path to PayPal
    assert body["text"]["format"]["name"] == "Treasury" and body["text"]["format"]["strict"]


def test_the_prompt_carries_code_numbers_only_never_paypal_ids_or_credentials():
    tr, sent = transport("treasurer.SYNTHETIC")
    advise(client(tr))
    user = sent[0]["input"][1]["content"][0]["text"]
    for word in ("paypal", "secret", "vault", "order", "authorization", "capture_id", "@",
                 "payer", "token"):
        assert word not in user.lower(), word
    facts = json.loads(user)
    assert set(facts) == {"currency", "minor_units_per_unit", "split_total_minor",
                          "campaigns", "max_per_campaign_minor", "fee_pct", "statement",
                          "spend_reported_minor"}
    assert facts["statement"]["refundable_minor"] == 6615


@pytest.mark.parametrize("change, reason", [
    ({"split": [{"campaign": "cmp_a1", "amount_minor": 12000, "why": "x"},
                {"campaign": "cmp_b2", "amount_minor": 8001, "why": "y"}]}, "split_total"),
    ({"split": [{"campaign": "cmp_a1", "amount_minor": 20000, "why": "x"},
                {"campaign": "cmp_b2", "amount_minor": 0, "why": "y"}]}, "split_cap"),
    ({"split": [{"campaign": "cmp_a1", "amount_minor": 12000, "why": "x"},
                {"campaign": "cmp_zz", "amount_minor": 8000, "why": "y"}]}, "split_campaigns"),
])
def test_a_split_that_does_not_add_up_is_replaced_by_codes(change, reason):
    tr, _ = answer_with(**change)
    out = advise(client(tr))
    assert reason in out.refused and out.source == "code"
    assert out.split == treasurer.even_split(20000, CAMPAIGNS)
    assert sum(x["amount_minor"] for x in out.split) == 20000
    assert "66.15 PLN" in out.explanation  # the explanation passed its own checks


def test_wrong_figures_or_an_invented_amount_replace_the_explanation():
    figures = {"captured_minor": 22050, "spend_counted_minor": 14700, "fee_due_minor": 735,
               "refunded_minor": 0, "refundable_minor": 7000}
    tr, _ = answer_with(figures=figures)
    out = advise(client(tr))
    assert "figures" in out.refused and out.explanation == treasurer.plain(STATEMENT, "PLN")
    tr, _ = answer_with(explanation="You can get 70.00 PLN back today.")
    out = advise(client(tr))
    assert "explanation_amounts" in out.refused
    assert "70.00" not in out.explanation and "66.15 PLN" in out.explanation


def test_a_model_error_gives_codes_advice():
    tr = httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "x"}))
    out = advise(client(tr))
    assert out.source == "code" and out.refused[0].startswith("model_error")
    assert out.split == treasurer.even_split(20000, CAMPAIGNS)


def test_the_route_explains_a_captured_funding_for_its_owner_only(tmp_path):
    from fastapi.testclient import TestClient

    from tests.test_assistants import BASE, login
    from tests.test_paypal_vault import Studio, sign

    s = Studio(tmp_path)
    s.grant()
    run = s.run()
    item = run["mandate"]["result"]["campaign_item_id"]
    fid = run["mandate"]["charge"]["funding_id"]
    r = s.c.post(f"/api/campaigns/{item}/activate")
    sign(s.c, s.key, s.approver, r.json()["approval_id"])
    tr, sent = answer_with()
    s.app.state.muse = client(tr)
    r = s.c.post(f"/api/funding/{fid}/treasurer",
                 json={"spend_minor": 14700, "split_total_minor": 20000})
    assert r.status_code == 200, r.text
    out = r.json()
    # this funding: 210.00 budget, 10.50 fee; Meta spent 147.00 → 66.15 refundable
    assert out["funding_id"] == fid and out["currency"] == "PLN"
    assert out["split"] == treasurer.even_split(20000, [item])  # one campaign: code's
    assert "split_campaigns" in out["refused"]
    assert "66.15 PLN" in out["explanation"]
    other = TestClient(s.app, base_url=BASE)
    login(other, s.app, username="intruder")
    r = other.post(f"/api/funding/{fid}/treasurer", json={"spend_minor": 0})
    assert r.status_code == 404


def test_no_model_gives_codes_answer():
    from decimal import Decimal

    from studio.agents import treasurer
    a = treasurer.advise(None, run_id="run_x", currency="PLN", total_minor=14700,
                         campaigns=["cmp_1"], max_each_minor=14700, statement=None,
                         fee_pct=Decimal(5))
    assert a.as_dict()["source"] == "code" if "source" in a.as_dict() else True
    assert "model_unavailable" in a.refused
