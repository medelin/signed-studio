"""Brief v1 in the studio API: draft → answer or strike every [FACT] question → confirm →
run. The confirm step writes the ``brief.confirmed`` receipt with the run; free-text
briefs still run as before."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.agents import producer
from studio.app import create_app
from studio.muse.client import MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY
from tests.test_brief import sample
from tests.test_ui_studio import login, settings

OWNER = ("Warsztaty z koła garncarskiego w Krakowie, 2 h, 180 zł od osoby, soboty o 10:00, "
         "maksymalnie 8 osób. Wszystko w cenie. Budżet 40 zł dziennie.")


def draft_json():
    b = sample().model_dump(mode="json")
    b["confirmed_facts"] = ["2 h, 180 zł od osoby", "soboty o 10:00 i 14:00",
                            "maksymalnie 8 osób"]
    b["offer"]["reasons_to_believe"] = [{"text": "małe grupy", "facts": [2]}]
    b["offer"]["valid_until"] = ""
    b["proposition"] = "Pierwsza miska z twoich rąk."
    b["open_questions"] = [{"question": "Czy glina i wypał są w cenie?",
                            "claim": "Wszystko w cenie."}]
    return b


@pytest.fixture
def app(tmp_path, monkeypatch):
    a = create_app(settings(tmp_path))
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        name = body["response_format"]["json_schema"]["name"]
        assert name == "BriefV1", name
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(draft_json())}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 400}})

    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(handler))
    a.state.sent = sent
    started = []

    def fake_run(ctx, brief, *, run_id):
        started.append({"ctx": ctx, "brief": brief, "run_id": run_id})
        producer.mark_failed(ctx.conn, run_id, "stopped_by_test")

    monkeypatch.setattr(producer, "run", fake_run)
    a.state.started = started
    return a


def drafted(c, **extra):
    r = c.post("/api/briefs/draft", json={"text": OWNER, "lang": "pl", **extra})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(c, d, resolutions, brief=None):
    return c.post("/api/runs", json={"brief_id": d["brief_id"],
                                     "brief_v1": brief or d["brief"],
                                     "resolutions": resolutions})


def wait(app):
    app.state.last_thread.join(10)


def test_draft_needs_a_session(app):
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/api/briefs/draft", json={"text": OWNER}).status_code in (401, 403)


def test_draft_keeps_gaps_as_questions_and_numbers_the_owner_never_wrote(app):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c)
    b = d["brief"]
    # "14:00" was never in the owner's words: the fact became a question (code net)
    assert b["confirmed_facts"] == ["2 h, 180 zł od osoby", "maksymalnie 8 osób"]
    assert [q["claim"] for q in b["open_questions"]] == ["Wszystko w cenie.",
                                                         "soboty o 10:00 i 14:00"]
    assert b["open_questions"][1]["question"].startswith("Proszę potwierdzić")
    assert b["offer"]["reasons_to_believe"][0]["facts"] == [1]
    assert d["problems"] == {}  # complete apart from the open questions (not checked yet)
    system = app.state.sent[0]["messages"][0]["content"]
    assert "Producer (Brief v1 draft)" in system and "Demo Ceramics" not in system
    (r,) = app.state.ledger.receipts(d["draft_run_id"])
    assert r.agent == "producer" and r.model == "muse-spark-1.3" and not r.envelope
    row = app.state.conn.execute("SELECT source, run_id, draft_run_id FROM briefs WHERE id = ?",
                                 (d["brief_id"],)).fetchone()
    assert row == ("text", "", d["draft_run_id"])


def test_every_question_must_be_answered_or_struck(app):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c)
        r = confirm(c, d, [{"action": "strike"}])
        assert r.status_code == 422 and r.json()["fields"] == {
            "open_questions.1": "unresolved"}
        r = confirm(c, d, [{"action": "answer", "text": " "}, {"action": "strike"}])
        assert r.json()["fields"] == {"open_questions.0": "answer_required"}
        bad = json.loads(json.dumps(d["brief"]))
        bad["budget"]["amount"] = 400
        r = confirm(c, d, [{"action": "strike"}, {"action": "strike"}], brief=bad)
        assert r.status_code == 422 and r.json()["fields"] == {"budget.amount": "over_ceiling"}
        bad["budget"]["amount"] = "40"  # strict schema
        r = confirm(c, d, [{"action": "strike"}, {"action": "strike"}], brief=bad)
        assert r.status_code == 422 and "budget.amount" in r.json()["fields"]
    assert app.state.started == []


def test_confirm_writes_the_receipt_and_starts_the_run_with_only_confirmed_facts(app):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c)
        r = confirm(c, d, [{"action": "answer", "text": "Glina i wypał są w cenie"},
                           {"action": "strike"}])
        assert r.status_code == 202, r.text
        wait(app)
        out = r.json()
        assert out["run_id"] == d["draft_run_id"]
        assert (out["facts"], out["answered"], out["struck"], out["open_questions"]) == (
            3, 1, 1, 0)
        again = confirm(c, d, [{"action": "strike"}, {"action": "strike"}])
        assert again.status_code == 409  # a draft is confirmed once
    receipts = app.state.ledger.receipts(out["run_id"])
    assert [x.kind for x in receipts] == ["model_call", "brief.confirmed"]
    rc = receipts[1]
    assert rc.agent == "owner" and rc.output_sha256 == out["brief_sha256"]
    assert rc.details == {"schema": "brief/v1", "brief_sha256": out["brief_sha256"],
                          "facts": 3, "open_questions": 0, "answered": 1, "struck": 1,
                          "voice": ""}
    (s,) = app.state.started
    ctx = s["ctx"]
    assert ctx.brief_sha256 == out["brief_sha256"] and ctx.brief_source == "operator"
    assert ctx.brief_v1.confirmed_facts[-1] == "Glina i wypał są w cenie"
    assert "10:00 i 14:00" not in s["brief"] and "Glina i wypał są w cenie" in s["brief"]
    stored = app.state.conn.execute("SELECT run_id, brief_sha256, brief_v1 FROM briefs "
                                    "WHERE id = ?", (d["brief_id"],)).fetchone()
    assert stored[0] == out["run_id"] and stored[1] == out["brief_sha256"]
    assert json.loads(stored[2])["schema"] == "brief/v1"


def test_dictated_answers_make_it_a_transcript_run(app):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c, dictated=True)
        assert d["source"] == "transcript"
        system = app.state.sent[0]["messages"][0]["content"]
        assert "Security rule" in system
        r = c.post("/api/runs", json={"brief_id": d["brief_id"], "brief_v1": d["brief"],
                                      "dictated": True, "resolutions": [
                                          {"action": "strike"}, {"action": "strike"}]})
        assert r.status_code == 202, r.text
        wait(app)
    assert app.state.started[0]["ctx"].brief_source == "transcript"
    rc = app.state.ledger.receipts(r.json()["run_id"])[-1]
    assert rc.details["voice"] == "muse-voice-transcribe-1.0"
    assert app.state.ledger.receipts(d["draft_run_id"])[0].envelope


def test_free_text_briefs_still_run(app):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        r = c.post("/api/runs", json={"brief": "Autumn mugs, 30 PLN a day."})
        assert r.status_code == 202
        wait(app)
    (s,) = app.state.started
    assert s["ctx"].brief_v1 is None and s["brief"] == "Autumn mugs, 30 PLN a day."
    assert app.state.ledger.receipts(s["run_id"]) == []  # no brief.confirmed receipt


def test_unknown_or_malformed_drafts_are_refused(app):
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        b = sample().model_dump(mode="json")
        assert c.post("/api/runs", json={"brief_id": "b_" + "0" * 16, "brief_v1": b,
                                         "resolutions": []}).status_code == 409
        assert c.post("/api/runs", json={"brief_id": "x", "brief_v1": b}).status_code == 400
        assert c.post("/api/briefs/draft", json={"text": "x" * 5000}).status_code == 413
        assert c.post("/api/briefs/draft", json={"text": ""}).status_code == 400
