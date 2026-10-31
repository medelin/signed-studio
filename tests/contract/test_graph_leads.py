"""Graph adapter lead ads against a hand-written v25.0 cassette
(``graph_leads.SYNTHETIC.json``): the form goes to the Page's ``leadgen_forms`` edge with
the Page token and the owner's privacy page; leads are read with the Page token, never
with a token in a URL."""

import json
import time

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


class Pages:
    """Graph list pages: each body as given; a ``None`` body is a fatal Graph error."""

    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.sent = []

    def __call__(self, request):
        import httpx

        self.sent.append(request)
        body = self.bodies.pop(0)
        if body is None:
            return httpx.Response(400, json={"error": {"code": 100, "message": "x"}})
        return httpx.Response(200, json=body)


def _lead(n):
    return {"id": f"14000000000000{n:04d}", "created_time": "2026-10-30T10:00:00+0000",
            "form_id": "130000000000000505",
            "field_data": [{"name": "email", "values": [f"p{n}@example.com"]}]}


def _page(items, after=""):
    paging = {"cursors": {"after": after},
              "next": "https://graph.facebook.com/v25.0/x?access_token=SECRET&after=" + after
              } if after else {"cursors": {"after": "end"}}
    return {"data": items, "paging": paging}


def test_leads_and_comments_follow_every_page_without_the_next_url(env):
    """Review fix E8: a form with more leads than one page (and a post with more comments)
    is read to the end, by cursor; the ``next`` URL (it carries a token) is never used."""
    replay = Pages([_page([_lead(1), _lead(2)], "c1"), _page([_lead(3)])])
    leads = adapter(replay).list_leads("130000000000000505")
    assert [x.fields["email"] for x in leads] == [f"p{n}@example.com" for n in (1, 2, 3)]
    assert replay.sent[1].url.params["after"] == "c1"
    assert all("SECRET" not in str(r.url) for r in replay.sent)
    comment = {"id": "1_2", "message": "hi", "created_time": "2026-10-30T10:00:00+0000"}
    replay = Pages([_page([comment], "k1"), _page([{**comment, "id": "1_3"}])])
    got = adapter(replay).list_comments("600000000000001_1")
    assert [c.external_id for c in got] == ["1_2", "1_3"]


def test_a_failed_form_keeps_its_mark_and_does_not_stop_the_others(env):
    conn, ledger = env
    leadads.record_form(conn, "130000000000000505", "cmp_x", "run_x", "graph")
    leadads.record_form(conn, "130000000000000506", "cmp_y", "run_y", "graph")
    # the first form fails on its second page; the second form reads fine
    replay = Pages([_page([_lead(1)], "c1"), None, _page([_lead(2)])])
    out = leadads.poll(conn, ledger, adapter(replay), now=2_000_000_000)
    assert out == {"forms": 1, "fetched": 1, "new": 1, "failed": 1}
    assert dict(leadads.forms(conn, "graph")) == {"130000000000000505": 0.0,
                                                  "130000000000000506": 2_000_000_000.0}
    g = adapter(Pages([_page([_lead(1)], "c1"), _page([_lead(3)]), _page([])]))
    assert leadads.poll(conn, ledger, g, now=2_000_000_100)["new"] == 2
    assert leadads.forms(conn, "graph")[0] == ("130000000000000505", 2_000_000_100.0)


class Backlog:
    """A form's leads as Graph serves them: newest first, ``time_created`` filtering,
    two leads a page, cursor paging."""

    def __init__(self, created):
        self.leads = [{**_lead(n), "created_time": time.strftime(
            "%Y-%m-%dT%H:%M:%S+0000", time.gmtime(ts)), "_ts": ts}
            for n, ts in enumerate(created)]
        self.sent = []

    def __call__(self, request):
        import httpx

        self.sent.append(request)
        rows = sorted(self.leads, key=lambda x: -x["_ts"])
        for rule in json.loads(request.url.params.get("filtering", "[]")):
            op = {"GREATER_THAN": int.__gt__, "LESS_THAN": int.__lt__}[rule["operator"]]
            rows = [r for r in rows if op(r["_ts"], rule["value"])]
        start = int(request.url.params.get("after", "0"))
        chunk = [{k: v for k, v in r.items() if k != "_ts"} for r in rows[start:start + 2]]
        more = start + 2 < len(rows)
        return httpx.Response(200, json=_page(chunk, str(start + 2) if more else ""))


def test_a_backlog_bigger_than_the_page_cap_is_read_over_successive_polls(env):
    """Security follow-up to E8: a backlog over the cap neither wedges the form nor skips
    a lead; each poll reads the oldest window that fits and moves the mark to its end."""
    conn, ledger = env
    leadads.record_form(conn, "130000000000000505", "cmp_x", "run_x", "graph")
    now = 1_900_000_000
    server = Backlog([now - 50_000 + 997 * i for i in range(13)])
    seen, marks = 0, []
    for _ in range(10):
        g = adapter(server, clock=lambda: now)
        g.LEAD_PAGES = 2  # four leads a window
        out = leadads.poll(conn, ledger, g, now=now)
        assert "failed" not in out
        seen += out["new"]
        marks.append(leadads.forms(conn, "graph")[0][1])
        if marks[-1] == now:
            break
    assert seen == 13 and marks == sorted(marks) and len(marks) > 1
    with conn.lock:
        assert conn.execute("SELECT count(*) FROM leads").fetchone()[0] == 13
