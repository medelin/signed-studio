"""Lead rationale (D3-3) against a recorded Muse Spark 1.3 call."""

import respx

from studio import envelope
from studio.agents import lead_scorer
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, load, mount


def test_recorded_rationale_explains_the_fixed_score():
    lead = load("lead_rationale")["lead"]
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    rs = lead_scorer.rules(lead)
    with respx.mock() as router:
        sent = mount(router, "lead_rationale", BASE_URL)
        score, result = lead_scorer.explain(client, lead, rs, run_id="ld_rec")
    user = sent[0]["messages"][1]["content"]
    assert "buyer@example.com" not in user and "7946" not in user
    assert envelope.BEGIN in user and "Rule score (fixed, do not change): 90 (HOT)" in user
    assert (score.score, score.temp) == (90, "HOT") and score.persona
    assert 1 <= len(score.reasons) <= 4 and score.next_action
    (r,) = ledger.receipts("ld_rec")
    assert r.envelope and r.details["envelope_sources"] == ["lead"] and result.cost_usd > 0
