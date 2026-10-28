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
