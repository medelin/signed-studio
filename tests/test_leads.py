"""Lead scorer (D3-3): rules table, rationale receipt, CRM card + HOT task."""

import json

import httpx
import pytest
import respx

from studio import db, envelope
from studio.agents import lead_scorer
from studio.agents.lead_scorer import rules
from studio.muse.client import BASE_URL, MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY

UK = {"email": "buyer@example.com", "phone": "+44 20 7946 0958", "city": "London"}


@pytest.mark.parametrize(("lead", "score", "temp"), [
    (UK, 90, "HOT"),
    ({**UK, "kind": "private"}, 60, "WARM"),
    ({"email": "a@example.com"}, 40, "COLD"),
    ({}, 20, "COLD"),
    ({"email": "a@example.com", "phone": "+48 000 000 101", "city": "Kraków"}, 75, "WARM"),
    ({"email": "a@example.com", "phone": "+48 000 000 101", "city": "Kraków",
      "company": "Demo Cowork Sp. z o.o."}, 85, "HOT"),
    ({"phone": "07700 900123", "country": "UK", "message": "for my office"}, 70, "WARM"),
    ({"message": "prywatnie, prezent dla mamy"}, 0, "COLD"),
    ({**UK, "company": "Demo Ltd"}, 100, "HOT"),
])
def test_rules_table(lead, score, temp):
    rs = rules(lead)
    assert (rs.score, rs.temp) == (score, temp), rs.hits


def test_uk_counts_once_and_private_subtracts_30():
    assert rules(UK).hits == ["base 20", "+20 email", "+25 phone", "+10 city", "+15 UK"]
    assert "-30 private" in rules({**UK, "kind": "private"}).hits


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    return conn, ledger, MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))


def _reply(score=12, temp="COLD"):
    content = {"score": score, "temp": temp, "persona": "London office manager",
               "reasons": ["gave e-mail and phone", "UK number", "in London"],
               "next_action": "Call today and offer the office set."}
    return {"id": "c1", "model": "muse-spark-1.3", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 80}}


def test_rationale_cannot_change_the_score_and_never_sees_contact_data(env):
    conn, ledger, client = env
    with db.transaction(conn):
        conn.execute("INSERT INTO leads (id, name, email, phone, source, message, city, "
                     "created_ts) VALUES ('l-uk', 'Demo UK', ?, ?, 'form', 'wants twelve mugs', "
                     "'London', 0)", (UK["email"], UK["phone"]))
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_reply()))
        card = lead_scorer.score_lead(conn, ledger, client, "l-uk", now=1000.0)
    sent = route.calls[0].request.content.decode()
    assert "buyer@example.com" not in sent and "7946" not in sent
    assert envelope.BEGIN in json.loads(sent)["messages"][1]["content"]
    assert card["score"] == 90 and card["temp"] == "HOT"  # the model said 12/COLD
    assert card["owner"] and card["task_id"] and card["persona"] == "London office manager"
    task = conn.execute("SELECT title, due_ts FROM tasks WHERE id = ?",
                        (card["task_id"],)).fetchone()
    assert task[0].startswith("Call within 30 min") and task[1] == 1000.0 + 1800
    model, rationale = ledger.receipts(lead_scorer.run_id_for("l-uk"))
    assert model.kind == "model_call" and model.envelope and model.tier == "standard"
    assert rationale.details["tool"] == "lead_score" and rationale.details["score"] == 90
    assert rationale.details["model_receipt"] == model.id
    assert "+15 UK" in rationale.details["rules"]
    # scoring again does not add a second call task
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(200,
                                                                               json=_reply()))
        again = lead_scorer.score_lead(conn, ledger, client, "l-uk")
    assert again["task_id"] == ""


def test_comment_with_contact_data_becomes_a_lead(env):
    conn, ledger, _ = env
    db.seed(conn)
    lead_id = lead_scorer.lead_from_comment(conn, "c-2")
    assert lead_scorer.lead_from_comment(conn, "c-2") == lead_id  # idempotent
    lead = lead_scorer.load(conn, lead_id)
    assert lead["email"] == "visitor.b@example.com" and lead["source"] == "comment"
    card = lead_scorer.score_lead(conn, ledger, None, lead_id)  # rules only, no model
    assert card["score"] == 50 and "+10 business" in card["reasons"]


def test_contact_data_inside_the_message_is_redacted_before_the_model(env):
    _, _, client = env
    lead = {"email": "x@example.com", "phone": "+48 000 000 105",
            "message": "write to visitor.q@example.com or call 600 100 200"}
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_reply()))
        lead_scorer.explain(client, lead, rules(lead), run_id="ld_q")
    sent = route.calls[0].request.content.decode()
    assert "visitor.q@example.com" not in sent and "600 100 200" not in sent
    assert "[email]" in sent and "[phone]" in sent
