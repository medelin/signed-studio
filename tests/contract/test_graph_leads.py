"""Graph adapter lead ads against a hand-written v25.0 cassette
(``graph_leads.SYNTHETIC.json``): the form goes to the Page's ``leadgen_forms`` edge with
the Page token and the owner's privacy page; leads are read with the Page token, never
with a token in a URL."""

import json

import pytest

from studio import db, leadads
from studio.meta import objectives
from tests.approve import approve
from tests.cassettes import load
from tests.contract.test_graph import PAGE_TOKEN, Replay, adapter
from tests.test_meta_mock import payload

CASSETTE = load("graph_leads.SYNTHETIC")
PAGE = "600000000000001"
PRIVACY = "https://example.com/privacy"


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    return conn, db.open_ledger(conn, tmp_path / "ledger.key")


def test_cassette_is_marked_synthetic():
    assert CASSETTE["_synthetic"] is True and "NOT a recording" in CASSETTE["_note"]


def test_form_is_created_on_the_page_with_the_owners_privacy_page(env):
    conn, ledger = env
    spec = objectives.delivery("leads", page_id=PAGE, promotion={"privacy_policy_url": PRIVACY})
    p = payload(delivery=spec, lead_form=leadads.form_spec("Form", spec["lead_fields"],
                                                           PRIVACY, "https://example.com/"))
    v = approve(conn, ledger, "campaign.create", "cmp_gl1", p, "run_m1")
    replay = Replay(CASSETTE["create_form"])
    assert adapter(replay).create_lead_form(p, v) == "130000000000000505"
    (request, form), = replay.sent
    assert form["access_token"] == PAGE_TOKEN
    assert json.loads(form["privacy_policy"]) == {"url": PRIVACY, "link_text":
                                                  "example.com/privacy"}
    assert [q["type"] for q in json.loads(form["questions"])] == ["FULL_NAME", "EMAIL"]


def test_leads_are_read_with_the_page_token_and_parsed(env):
    replay = Replay(CASSETTE["leads"])
    leads = adapter(replay).list_leads("130000000000000505", since=1761600000.0)
    assert [x.external_id for x in leads] == ["140000000000000601", "140000000000000602"]
    assert leads[0].fields["email"] == "one@example.com"
    (request, _), = replay.sent
    assert request.headers["authorization"] == f"Bearer {PAGE_TOKEN}"
    assert PAGE_TOKEN not in str(request.url)
    flt = json.loads(request.url.params["filtering"])
    assert flt == [{"field": "time_created", "operator": "GREATER_THAN", "value": 1761600000}]
    lead = adapter(Replay(CASSETTE["lead"])).get_lead("140000000000000602")
    assert lead.form_id == "130000000000000505" and lead.fields["email"] == "two@example.com"
