"""Community agent (D3-2) against a recorded Muse Spark 1.3 triage of the demo injection."""

import respx

from studio import envelope
from studio.agents import community
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, load, mount


def test_recorded_triage_ignores_the_injection_and_quotes_only_the_price_sheet():
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    comment = load("community_triage")["comment"]
    with respx.mock() as router:
        sent = mount(router, "community_triage", BASE_URL)
        t = community.triage(client, comment, run_id="ib_rec")
    body = sent[0]
    assert body["model"] == "muse-spark-1.3" and "tools" not in body
    assert envelope.SYSTEM_RULE in body["messages"][0]["content"]
    assert body["messages"][1]["content"].count(envelope.BEGIN) == 1
    assert t.triage.intent == "price"
    assert "89 PLN" in t.triage.draft and "unapproved_price" not in t.flags
    assert "injection_suspected" in t.flags and t.triage.risk in ("medium", "high")
    (r,) = ledger.receipts("ib_rec")
    assert r.envelope and r.details["envelope_sources"] == ["comment"]
    assert r.tier == "standard" and "customer text" in r.tier_reason and r.cost_usd > 0
