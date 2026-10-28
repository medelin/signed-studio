"""Day 2 fix round (minor): cassette replay checks the request body, not just the path."""

import json

import httpx
import pytest

from tests.cassettes import FAKE_KEY, interactions, transport

FIRST = interactions("spark_tool_call")[0]["request"]


def _post(tr, body):
    with httpx.Client(transport=tr, base_url="https://api.meta.ai/v1") as c:
        return c.post(FIRST["path"], content=json.dumps(body),
                      headers={"authorization": f"Bearer {FAKE_KEY}"})


def test_recorded_body_replays():
    tr, _ = transport("spark_tool_call")
    assert _post(tr, FIRST["json"]).status_code == 200


@pytest.mark.parametrize("change", [
    {"model": "muse-spark-1.3-contributor"},
    {"stream": True},
    {"previous_response_id": "resp_other"},
    {"tools": [{"type": "function", "name": "create_campaign", "parameters": {}}]},
])
def test_a_different_body_fails_the_replay(change):
    tr, _ = transport("spark_tool_call")
    with pytest.raises(AssertionError):
        _post(tr, {**FIRST["json"], **change})


def test_every_fixture_says_whether_it_is_recorded_or_synthetic():
    from tests.cassettes import ROOT
    for path in sorted((ROOT / "fixtures").glob("**/*.json")):
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            continue
        synthetic = ".SYNTHETIC" in path.name
        assert data.get("_provenance") == ("synthetic" if synthetic else "recorded"), path
        if synthetic:
            assert data.get("_synthetic") is True and data.get("_note"), path


def test_synthetic_insights_are_labelled_in_the_report(tmp_path):
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from studio.muse.client import MuseClient
    from studio.settings import Budget
    from tests.test_analyst import REPORT, _fake
    from tests.test_ui_studio import login, settings

    a = create_app(settings(tmp_path))
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(
                                  lambda req: httpx.Response(200, json=_fake(REPORT))))
    with TestClient(a, base_url="https://testserver") as c:
        login(c)
        r = c.post("/api/reports", json={})
        assert r.status_code == 200 and r.json()["insights"] == "synthetic"
        assert "(demo fixture, not Meta data)" in c.get("/report").text
