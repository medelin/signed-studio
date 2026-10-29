"""The recorded ``leadform`` set: a Brief v1 whose owner chose an instant form (objective,
landing page, privacy page, form fields), replayed offline to a signed PAUSED campaign.
Recorded live on 2026-10-29 against the Muse Studio Lite ad account; the replay uses the
mock adapter, so the campaign, the form and its locale are checked on the signed payload."""

import json

import httpx
import pytest

from studio import demo, replay


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("replay-leadform")

    def no_network(request):
        raise AssertionError(f"network call in replay: {request.url}")

    real = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = lambda self, request: no_network(request)
    try:
        summary = demo.run("leadform", data_dir=tmp, log=lambda _: None)
    finally:
        httpx.HTTPTransport.handle_request = real
    receipts = [json.loads(line) for line in (tmp / "receipts.jsonl").read_text().splitlines()
                if '"manifest"' not in line]
    return summary, receipts


def test_leadform_set_is_a_recording_with_the_owners_choices():
    data = replay.load_set("leadform")
    assert data["_provenance"] == "recorded"
    owner = data["owner_fields"]
    assert owner["objective"] == {"goal": "leads", "sub": "instant_form"}
    assert owner["advertiser"]["landing_url"] == "https://muse.udun.eu/"
    assert owner["promotion"]["privacy_policy_url"] == "https://muse.udun.eu/privacy"


def test_replay_reaches_a_signed_paused_lead_campaign(run):
    summary, _ = run
    assert summary["campaign_status"] == "PAUSED"
    assert summary["verify_exit"] == 0


def test_the_replay_creates_an_instant_form_and_a_leads_campaign(run):
    _, receipts = run
    tools = {r["details"].get("tool"): r["details"] for r in receipts
             if r["kind"] == "tool_call" and r["details"].get("side_effects") == "external"}
    assert tools["lead_form"]["status"] == "ok"
    assert tools["campaign"]["objective"] == "OUTCOME_LEADS"
    assert tools["campaign"]["object_status"] == "PAUSED"
