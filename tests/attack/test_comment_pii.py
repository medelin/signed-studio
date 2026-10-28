"""Attack suite (day 3 fix round, I8): contact data in a comment never reaches the model.

PII is detected on the original comment (flag, tier); the model gets the comment with
e-mail addresses, phone numbers, NIP/PESEL and long digit runs replaced, inside the
untrusted envelope."""

import json

import httpx
import pytest
import respx

from studio import db, envelope
from studio.agents import community
from studio.muse.client import BASE_URL, MuseClient
from studio.pii import redact
from studio.settings import Budget
from tests.attack.test_injection import _chat
from tests.cassettes import FAKE_KEY

BENIGN = {"intent": "question", "sentiment": "neutral", "risk": "low",
          "requires_human": False, "draft": "Dziękujemy, odezwiemy się w wiadomości.",
          "reason": "contact request", "confidence": 0.8}
SECRETS = ["jan.kowalski@example.com", "600 123 456", "600123456", "+48 600-123-456",
           "5260250274", "44051401359", "0048 22 123 45 67"]


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    return conn, ledger, MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))


@pytest.mark.parametrize("secret", SECRETS)
def test_contact_data_is_redacted_before_the_model_call(env, secret):
    conn, ledger, client = env
    comment = f"Chcę zamówić 3 kubki, kontakt: {secret} — dzięki!"
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_chat(BENIGN)))
        t = community.triage(client, comment, run_id="ib_pii1")
    sent = route.calls[0].request.content.decode()
    user = json.loads(sent)["messages"][1]["content"]
    assert secret not in sent and secret not in user
    digits = "".join(c for c in secret if c.isdigit())
    assert not digits or digits not in "".join(c for c in user if c.isdigit())
    assert "Chcę zamówić 3 kubki" in user and user.count(envelope.BEGIN) == 1
    assert "pii_in_comment" in t.flags  # detected on the original
    assert json.loads(sent)["model"] == "muse-spark-1.3"


def test_redact_keeps_ordinary_text_and_prices():
    text = "Kubek 350 ml za 89 PLN, dostawa od 150 PLN. Super!"
    assert redact(text) == text
    assert redact("pisz: a@b.pl") == "pisz: [email]"
