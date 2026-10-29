"""A7/B7: Meta disapproved an ad. Read-only detection on every insights pull, Meta's
reason explained (EN/PL, policy link), one compliant variant re-checked by the reviewers,
and a replacement (new creative + new ad, PAUSED, same ad set) only under the owner's
signature. The disapproved ad is left as it is."""

import json
from urllib.parse import parse_qs

import httpx
import pytest

from studio import db, disapprovals
from studio.agents import copywriter, media_buyer
from studio.agents.schemas import CopyPack, CopyVariant
from studio.approvals import Keys
from studio.executor import ExecutionRefused
from studio.meta import measurement
from studio.meta.adapter import NotApproved, review_of
from studio.meta.graph import GraphAdapter
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve
from tests.attack.test_paused_only import RUN, payload


def _png():
    import hashlib
    import io

    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (1080, 1080), (200, 180, 150)).save(out, "PNG")
    data = out.getvalue()
    return data, hashlib.sha256(data).hexdigest()


PNG, PNG_SHA = _png()

NEW = CopyVariant(primary_text="A calm break with a mug that keeps tea warm.",
                  headline="A calm tea break", description="Stoneware", cta="SHOP_NOW")


class FakeModel:
    """Copywriter is patched; this answers the reviewer with ``verdict``."""

    def __init__(self, ledger, verdict="OK", findings=()):
        self.ledger = ledger
        self.verdict, self.findings = verdict, list(findings)

    def chat(self, *args, **kwargs):
        body = json.dumps({"verdict": self.verdict, "findings": self.findings,
                           "summary": "re-checked"})

        class R:
            text = body
            finish = "stop"
        return R()


@pytest.fixture
def env(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    (tmp_path / "assets" / RUN).mkdir(parents=True)
    (tmp_path / "assets" / RUN / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    mock = MockAdapter()
    p = payload(images=[{"placement": "feed_1x1", "sha256": PNG_SHA}],
                ad={"primary_text": "Lowers blood pressure in 7 days, guaranteed.",
                    "headline": "Healthy mornings", "description": "89 PLN",
                    "cta": "SHOP_NOW"})
    v = approve(conn, ledger, "campaign.create", "cmp_dp1", p, RUN)
    media_buyer.create(conn, ledger, mock, v, p, tmp_path)
    seen = []

    def fake_write(client, angle, persona, **kw):
        seen.append(kw)
        return CopyPack(variants=[NEW, NEW, NEW], risks=[]), None

    monkeypatch.setattr(copywriter, "write", fake_write)
    ad = media_buyer.campaign(conn, "cmp_dp1")["ad_external_id"]
    return conn, ledger, mock, tmp_path, ad, seen


def test_review_of_parses_meta_review_feedback():
    state = review_of({"effective_status": "DISAPPROVED", "ad_review_feedback": {
        "global": {"Personal Attributes": "Ads must not imply personal attributes."},
        "placement_specific": {"instagram": {"Text": "x"}}},
        "issues_info": [{"error_code": 1815869, "error_summary": "Ad disapproved",
                         "error_message": "m", "level": "AD"}]})
    assert state["effective_status"] == "DISAPPROVED"
    assert [(r["key"], r["scope"]) for r in state["reasons"]] == [
        ("Personal Attributes", "global"), ("Text", "placement:instagram")]
    assert state["issues"][0]["error_code"] == "1815869"
    assert review_of({"effective_status": "ACTIVE", "ad_review_feedback": "junk"})[
        "reasons"] == []


@pytest.mark.parametrize(("key", "text", "rule"), [
    ("Personal Attributes", "imply personal attributes", "P1"),
    ("Personal Health", "cure a health condition", "P8"),
    ("Misleading claims", "exaggerated", "P3"),
    ("Something new", "unknown", "META"),
])
def test_meta_reasons_map_to_a_rule_explained_in_both_languages(key, text, rule):
    assert disapprovals.rule_for([{"key": key, "text": text}]) == rule
    en, pl = disapprovals.explain(rule, "en"), disapprovals.explain(rule, "pl")
    assert en["sentence"] and pl["sentence"] and en["sentence"] != pl["sentence"]
    assert en["source"].startswith("https://transparency.meta.com/")


def test_an_insights_pull_detects_the_disapproved_ad_once(env):
    conn, ledger, mock, _, ad, _ = env
    assert "disapproved" not in measurement.pull_all(conn, ledger, mock, 50)
    mock.disapprove(ad)
    assert measurement.pull_all(conn, ledger, mock, 50)["disapproved"] == 1
    assert "disapproved" not in measurement.pull_all(conn, ledger, mock, 50)
    (d,) = disapprovals.listing(conn, "pl", full=True)
    assert d["rule"] == "P8" and d["status"] == "open"
    assert d["explain"]["sentence"].startswith("Meta odrzuca")
    (r,) = [x for x in ledger.receipts() if x.kind == "ad.review"]
    assert r.details["effective_status"] == "DISAPPROVED" and r.details["rule"] == "P8"
    assert r.details["side_effects"] == "read" and r.details["reasons"] == ["Personal Health"]
    assert not any(c[0].startswith(("create", "update", "pause", "activate"))
                   for c in mock.calls[-3:])  # reading only


def _proposed(env, verdict="OK"):
    conn, ledger, mock, tmp, ad, seen = env
    mock.disapprove(ad)
    measurement.pull_all(conn, ledger, mock, 50)
    (d,) = disapprovals.listing(conn, full=True)
    findings = [{"rule": "P8", "severity": "block", "evidence": "still a health claim",
                 "source": ""}] if verdict == "BLOCK" else []
    out = disapprovals.propose(conn, ledger, FakeModel(ledger, verdict, findings), None,
                               d["id"], tmp)
    return d, out


def test_the_proposal_is_reviewed_and_waits_for_a_signature(env):
    conn, ledger, mock, _, ad, seen = env
    calls_before = len(mock.calls)
    d, out = _proposed(env)
    assert out["status"] == "requested"
    p = out["content"]
    assert p["action"] == "campaign.update" and p["change"]["kind"] == "replace_creative"
    assert p["change"]["disapproved_ad"] == ad and p["change"]["new_ad_status"] == "PAUSED"
    assert p["ad"] == NEW.model_dump() and p["review"]["verdict"] == "OK"
    # the copywriter got Meta's reason as the blocked finding, the old ad in the evidence
    assert seen[-1]["blocked"][0]["rule"] == "P8"
    assert "Meta disapproved" in seen[-1]["blocked"][0]["evidence"]
    assert not any(c[0].startswith("create_replacement") for c in mock.calls[calls_before:])
    review = [x for x in ledger.receipts() if x.kind == "review"][-1]
    assert review.details["verdict"] == "OK"
    with pytest.raises(disapprovals.DisapprovalError):  # one proposal per disapproval
        disapprovals.propose(conn, ledger, FakeModel(ledger), None, d["id"], env[3])


def test_a_blocked_variant_is_not_proposed(env):
    conn, _, _, _, _, _ = env
    d, out = _proposed(env, verdict="BLOCK")
    assert out["status"] == "blocked" and "approval_id" not in out
    assert disapprovals.listing(conn, full=True)[0]["status"] == "blocked"
    with conn.lock:
        assert conn.execute("SELECT count(*) FROM approvals WHERE action = "
                            "'campaign.update'").fetchone()[0] == 0


def test_signing_creates_a_paused_ad_and_leaves_the_disapproved_one(env):
    conn, ledger, mock, tmp, ad, _ = env
    d, out = _proposed(env)
    p = out["content"]
    with pytest.raises(ExecutionRefused):  # nothing without the owner's signature
        media_buyer.update(conn, ledger, mock, None, p, 50, tmp)
    assert not any(c[0].startswith("create_replacement") for c in mock.calls)
    old = dict(mock.objects[ad])
    v = approve(conn, ledger, "campaign.update", "cmp_dp1", p, RUN)
    # the test helper signs a new pending; the proposal names its own approval
    with conn.lock, conn:
        conn.execute("UPDATE disapprovals SET approval_id = ? WHERE id = ?",
                     (v.pending_id, d["id"]))
    result = media_buyer.update(conn, ledger, mock, v, p, 50, tmp)
    new_ad = mock.objects[result["ad_id"]]
    assert new_ad["status"] == "PAUSED" and new_ad["key"] == "replacement"
    c = media_buyer.campaign(conn, "cmp_dp1")
    assert new_ad["adset_id"] == c["adset_external_id"]
    assert mock.objects[new_ad["creative_id"]]["message"] == NEW.primary_text
    assert mock.objects[ad] == old  # left as it is
    assert result["ad_id"] in c["more_ad_external_ids"]
    assert disapprovals.listing(conn, full=True)[0]["status"] == "replaced"
    steps = [x.details["tool"] for x in ledger.receipts()
             if x.kind == "tool_call" and x.details.get("approval_id") == v.pending_id]
    assert steps == ["replace_image", "replace_creative", "replace_ad"]
    export = tmp / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    assert verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok
    with pytest.raises(ExecutionRefused):  # one signature, one replacement
        media_buyer.update(conn, ledger, mock, v, p, 50, tmp)


def test_a_signed_replacement_that_was_never_proposed_is_refused(env):
    conn, ledger, mock, tmp, ad, _ = env
    d, out = _proposed(env)
    forged = {**out["content"], "disapproval": {"id": "dp_000000000000", "rule": "P8",
                                                "reasons": []}}
    v = approve(conn, ledger, "campaign.update", "cmp_dp1", forged, RUN)
    with pytest.raises(media_buyer.BuyError) as exc:
        media_buyer.update(conn, ledger, mock, v, forged, 50, tmp)
    assert exc.value.code == "not_proposed"
    assert not any(c[0].startswith("upload_replacement") for c in mock.calls)


def test_an_active_status_in_a_replacement_is_refused(env):
    conn, ledger, mock, tmp, ad, _ = env
    d, out = _proposed(env)
    bad = {**out["content"], "ad_status": {"status": "ACTIVE"}}
    v = approve(conn, ledger, "campaign.update", "cmp_dp1", bad, RUN)
    with conn.lock, conn:
        conn.execute("UPDATE disapprovals SET approval_id = ? WHERE id = ?",
                     (v.pending_id, d["id"]))
    with pytest.raises(Exception) as exc:
        media_buyer.update(conn, ledger, mock, v, bad, 50, tmp)
    assert getattr(exc.value, "code", "") == "paused_only"


# -- Graph contract (fake transport; field names per the Marketing API v25 Ad reference) --

class Fake:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def __call__(self, request):
        form = parse_qs(request.content.decode()) if request.content else {}
        self.sent.append((request, {k: v[0] for k, v in form.items()}))
        return httpx.Response(200, json=self.responses.pop(0))


def _graph(fake):
    return GraphAdapter("EAAtest-token-not-a-secret", "act_100000000000001",
                        "600000000000001", transport=httpx.MockTransport(fake),
                        sleep=lambda s: None)


def test_graph_reads_the_ad_review_with_the_documented_fields():
    fake = Fake([{"id": "120000000000003", "effective_status": "DISAPPROVED",
                  "ad_review_feedback": {"global": {"Personal Health": "no cures"}}}])
    state = _graph(fake).ad_review("120000000000003")
    request, _ = fake.sent[0]
    assert request.method == "GET" and request.url.path == "/v25.0/120000000000003"
    assert request.url.params["fields"] == "effective_status,ad_review_feedback,issues_info"
    assert "access_token" not in str(request.url)
    assert state["reasons"][0]["key"] == "Personal Health"


def test_graph_replacement_is_three_posts_with_a_paused_ad(env):
    conn, ledger, mock, tmp, ad, _ = env
    d, out = _proposed(env)
    p = {**out["content"], "external_id": "120000000000001",
         "adset_external_id": "120000000000002"}
    v = approve(conn, ledger, "campaign.update", "cmp_dp1", p, RUN)
    with conn.lock, conn:
        conn.execute("INSERT INTO executions (approval_id, action, item_id, run_id, status, "
                     "created_ts, updated_ts) VALUES (?, 'campaign.update', 'cmp_dp1', ?, "
                     "'running', 0, 0)", (v.pending_id, RUN))
    fake = Fake([{"images": {"x": {"hash": "abc123"}}}, {"id": "120000000000010"},
                 {"id": "120000000000011"}])
    g = _graph(fake)
    image = g.upload_replacement_image(PNG, p, v)
    creative = g.create_replacement_creative(image, p, v)
    new_ad = g.create_replacement_ad(creative, p, v)
    assert new_ad == "120000000000011"
    paths = [r.url.path for r, _ in fake.sent]
    assert paths == ["/v25.0/act_100000000000001/adimages",
                     "/v25.0/act_100000000000001/adcreatives",
                     "/v25.0/act_100000000000001/ads"]
    ad_form = fake.sent[2][1]
    assert ad_form["status"] == "PAUSED" and ad_form["adset_id"] == "120000000000002"
    story = json.loads(fake.sent[1][1]["object_story_spec"])
    assert story["link_data"]["message"] == NEW.primary_text
    with pytest.raises(NotApproved):  # a budget approval cannot replace a creative
        g.create_replacement_ad(creative, {**p, "change": {"kind": "budget"}}, v)


# -- the inbox (EN/PL) and the routes -----------------------------------------------------

def test_inbox_explains_the_disapproval_in_both_languages(tmp_path):
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from tests.test_ui_studio import login, settings

    app = create_app(settings(tmp_path))
    conn = app.state.conn
    body = review_of(MockAdapter.DISAPPROVAL)
    with conn.lock, conn:
        conn.execute("INSERT INTO disapprovals (id, campaign_id, ad_external_id, body, rule, "
                     "created_ts) VALUES ('dp_00000000000a', 'cmp_x', 'mock-ad-1', ?, 'P8', 1)",
                     (json.dumps(body),))
    with TestClient(app, base_url="https://testserver") as c:
        en = c.get("/inbox?lang=en").text
        assert "Meta disapproved an ad" in en and "Personal Health" in en
        assert "https://transparency.meta.com/" in en
        assert 'data-dp="propose"' not in en  # a visitor cannot propose
        pl = c.get("/inbox?lang=pl").text
        assert "Meta odrzuciła reklamę" in pl and "Meta odrzuca" in pl
        assert c.post("/api/disapprovals/dp_00000000000a/propose", json={}).status_code == 401
        login(c)
        assert 'data-dp="propose"' in c.get("/inbox").text
        assert c.post("/api/disapprovals/nope/propose", json={}).status_code == 404
        # the campaign does not exist: refused before any model call
        assert c.post("/api/disapprovals/dp_00000000000a/propose",
                      json={}).json()["error"] == "not_found"
        assert c.get("/api/disapprovals?lang=pl").json()["disapprovals"][0]["explain"][
            "rule"] == "P8"


def test_a_visitor_sees_only_the_public_view(tmp_path):
    """Like comments and leads: without a session no proposal, approval id, Graph ad id or
    raw issues, and every string redacted (API and inbox HTML)."""
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from tests.test_ui_studio import login, settings

    app = create_app(settings(tmp_path))
    conn = app.state.conn
    body = {"effective_status": "DISAPPROVED", "reasons": [
        {"key": "Personal Health", "text": "write to owner@example.com", "scope": "global"}],
        "issues": [{"error_code": "1815869", "error_summary": "s", "error_message": "m",
                    "level": "AD"}]}
    proposal = {"variant": {"headline": "SECRET-HEADLINE", "primary_text": "p"},
                "verdict": "OK"}
    with conn.lock, conn:
        conn.execute("INSERT INTO disapprovals (id, campaign_id, ad_external_id, body, rule, "
                     "status, approval_id, proposal, created_ts) VALUES ('dp_00000000000b', "
                     "'cmp_x', '120000000000777', ?, 'P8', 'requested', 'APPROVAL-ID-X', ?, 1)",
                     (json.dumps(body), json.dumps(proposal)))
    with TestClient(app, base_url="https://testserver") as c:
        (d,) = c.get("/api/disapprovals").json()["disapprovals"]
        assert set(d) <= {*disapprovals.PUBLIC_FIELDS, "body", "proposal", "approval_id"}
        assert d["proposal"] == {} and d["approval_id"] == "" and "issues" not in d["body"]
        page = c.get("/inbox").text
        for leak in ("SECRET-HEADLINE", "APPROVAL-ID-X", "120000000000777", "1815869",
                     "owner@example.com"):
            assert leak not in page and leak not in json.dumps(d)
        assert "[email]" in d["body"]["reasons"][0]["text"]
        login(c)
        (full,) = c.get("/api/disapprovals").json()["disapprovals"]
        assert full["approval_id"] == "APPROVAL-ID-X" and "SECRET-HEADLINE" in c.get(
            "/inbox").text
