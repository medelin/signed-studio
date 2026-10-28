"""Graph adapter (D3-6) against a hand-written cassette built from the v25.0 reference.

``graph_campaign.SYNTHETIC.json`` is not a recording (no Meta token exists yet). The tests
assert the contract that matters regardless: PAUSED in every creation body, the token in
the POST form body by default or in the Authorization header (never in a URL), the retry
matrix, preview URL validation, and no request at all without the approval.
"""

import hashlib
import json
from urllib.parse import parse_qs

import httpx
import pytest

from studio import db
from studio.meta.adapter import AdapterError, NotApproved, PausedOnly
from studio.meta.graph import GraphAdapter
from studio.meta.preview import from_graph_body
from tests.approve import approve
from tests.cassettes import load
from tests.test_meta_mock import PNG, payload

TOKEN = "EAAtest-token-not-a-secret"  # noqa: S105
PAGE_TOKEN = "EAApage-token-not-a-secret"  # noqa: S105
CASSETTE = load("graph_campaign.SYNTHETIC")


def test_cassette_is_marked_synthetic():
    assert CASSETTE["_synthetic"] is True and "NOT a recording" in CASSETTE["_note"]


class Replay:
    def __init__(self, items, errors=()):
        self.items = list(items)
        self.errors = list(errors)
        self.sent = []

    def __call__(self, request):
        form = parse_qs(request.content.decode()) if request.content else {}
        self.sent.append((request, {k: v[0] for k, v in form.items()}))
        if self.errors:
            e = self.errors.pop(0)
            return httpx.Response(e["status"], json=e["json"])
        item = self.items.pop(0)
        assert request.method == item["request"]["method"]
        assert request.url.path == item["request"]["path"], request.url.path
        return httpx.Response(item["response"]["status"], json=item["response"]["json"])


def adapter(replay, sleeps=None, **kw):
    kw.setdefault("dsa_beneficiary", "Demo Ceramics")
    kw.setdefault("dsa_payor", "Demo Ceramics")
    return GraphAdapter(TOKEN, "act_100000000000001", "600000000000001", PAGE_TOKEN,
                        transport=httpx.MockTransport(replay),
                        sleep=(sleeps.append if sleeps is not None else lambda s: None),
                        **kw)


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    return conn, ledger


def _assert_token_transport(replay, token=TOKEN, mode="form"):
    """Never in a URL. ``form``: writes carry ``access_token`` in the form body and no
    header; reads use the header. ``bearer``: the header everywhere, nothing in a form."""
    for request, form in replay.sent:
        assert token not in str(request.url) and "access_token" not in str(request.url)
        if request.method == "POST" and mode == "form":
            assert form["access_token"] == token and "authorization" not in request.headers
        else:
            assert "access_token" not in form and token not in request.content.decode()
            assert request.headers["authorization"] == f"Bearer {token}"


def test_an_eu_ad_set_without_dsa_names_is_refused_before_any_post(env):
    conn, ledger = env
    p = payload()  # targets PL
    v = approve(conn, ledger, "campaign.create", "cmp_g2", p, "run_m2")
    replay = Replay(CASSETTE["interactions"])
    g = adapter(replay, dsa_beneficiary="", dsa_payor="")
    with pytest.raises(AdapterError) as exc:
        g.create_adset_paused("120200000000000101", p, v)
    assert exc.value.code == "dsa_missing"
    assert not [r for r, _ in replay.sent if r.url.path.endswith("/adsets")]


def test_campaign_sequence_is_paused_in_every_creation_body(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g1", p, "run_m1")
    replay = Replay(CASSETTE["interactions"])
    g = adapter(replay)
    image = g.upload_image(PNG, p, v)
    cmp = g.create_campaign_paused(p, v)
    adset = g.create_adset_paused(cmp, p, v)
    creative = g.create_creative(image, p, v)
    ad = g.create_ad_paused(adset, creative, p, v)
    prev = g.preview(ad)
    assert (image, cmp, ad) == ("0f1e2d3c4b5a69788796a5b4c3d2e1f0", "120200000000000101",
                                "120200000000000404")
    assert prev.kind == "iframe" and prev.src.startswith(
        "https://www.facebook.com/ads/api/preview_iframe.php?d=AQKdemo&t=")
    posts = {r.url.path.rsplit("/", 1)[-1]: f for r, f in replay.sent if r.method == "POST"}
    for edge in ("campaigns", "adsets", "ads"):
        assert posts[edge]["status"] == "PAUSED", edge
    for _, form in replay.sent:
        assert "ACTIVE" not in json.dumps(form)
    assert posts["campaigns"]["special_ad_categories"] == "[]"
    assert posts["campaigns"]["objective"] == "OUTCOME_TRAFFIC"
    assert posts["adsets"]["daily_budget"] == "3000"
    assert json.loads(posts["adsets"]["targeting"])["geo_locations"] == {"countries": ["PL"]}
    assert posts["adsets"]["dsa_beneficiary"] == posts["adsets"]["dsa_payor"] == "Demo Ceramics"
    spec = json.loads(posts["adcreatives"]["object_story_spec"])
    assert spec["link_data"]["image_hash"] == image
    assert spec["link_data"]["message"] == "A mug for slow mornings."
    assert spec["link_data"]["call_to_action"]["type"] == "SHOP_NOW"
    assert json.loads(posts["ads"]["creative"]) == {"creative_id": creative}
    assert hashlib.sha256(PNG).hexdigest()  # bytes went base64 in the form, not a URL
    _assert_token_transport(replay)


def test_activation_needs_the_activate_approval(env):
    conn, ledger = env
    create = payload()
    v_create = approve(conn, ledger, "campaign.create", "cmp_g2", create, "run_m1")
    act = {"action": "campaign.activate", "campaign_id": "cmp_g2",
           "external_id": "120200000000000101", "adset_external_id": "120200000000000202",
           "ad_external_id": "120200000000000404", "daily_budget_minor": 3000,
           "ceiling_minor": 5000, "run_id": "run_m1"}
    replay = Replay(CASSETTE["activation"])
    g = adapter(replay)
    with pytest.raises(NotApproved):
        g.activate("ad", act, v_create)
    assert replay.sent == []
    v_act = approve(conn, ledger, "campaign.activate", "cmp_g2", act, "run_m1")
    for level in ("ad", "adset", "campaign"):
        g.activate(level, act, v_act)
    body = {"status": "ACTIVE", "access_token": TOKEN}
    assert [(r.url.path, f) for r, f in replay.sent] == [
        ("/v25.0/120200000000000404", body), ("/v25.0/120200000000000202", body),
        ("/v25.0/120200000000000101", body)]
    with pytest.raises(NotApproved):  # a level the signed payload does not name
        g.activate("creative", act, v_act)


def test_no_request_without_approval_or_with_active_payload(env):
    conn, ledger = env
    replay = Replay([])
    g = adapter(replay)
    with pytest.raises(NotApproved):
        g.create_campaign_paused(payload(), None)
    bad = payload(campaign={**payload()["campaign"], "status": "ACTIVE"})
    v = approve(conn, ledger, "campaign.create", "cmp_g3", bad, "run_m1")
    with pytest.raises(PausedOnly):
        g.create_campaign_paused(bad, v)
    assert replay.sent == []


@pytest.mark.parametrize("error", ["throttled", "rate_limit"])
def test_transient_codes_are_retried_with_backoff(env, error):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g4", p, "run_m1")
    sleeps = []
    replay = Replay(CASSETTE["interactions"][1:2], errors=[CASSETTE["errors"][error]] * 2)
    assert adapter(replay, sleeps).create_campaign_paused(p, v) == "120200000000000101"
    assert sleeps == [1, 2] and len(replay.sent) == 3


def test_transient_codes_give_up_after_the_retries(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g5", p, "run_m1")
    replay = Replay([], errors=[CASSETTE["errors"]["throttled"]] * 4)
    with pytest.raises(AdapterError) as exc:
        adapter(replay).create_campaign_paused(p, v)
    assert exc.value.transient and exc.value.graph_code == 17 and len(replay.sent) == 4


@pytest.mark.parametrize("error", ["expired_token", "invalid_param"])
def test_fatal_codes_are_not_retried_and_hide_the_body(env, error):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g6", p, "run_m1")
    replay = Replay([], errors=[CASSETTE["errors"][error]])
    with pytest.raises(AdapterError) as exc:
        adapter(replay).create_campaign_paused(p, v)
    assert not exc.value.transient and len(replay.sent) == 1
    assert "fbtrace" not in str(exc.value) and TOKEN not in str(exc.value)


def test_network_error_on_a_write_is_not_retried(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g7", p, "run_m1")
    calls = []

    def boom(request):
        calls.append(request)
        raise httpx.ConnectError("down")

    g = GraphAdapter(TOKEN, "act_100000000000001", "600000000000001",
                     transport=httpx.MockTransport(boom), sleep=lambda s: None)
    with pytest.raises(AdapterError) as exc:
        g.create_campaign_paused(p, v)
    assert exc.value.code == "network" and len(calls) == 1


def test_comments_poll_reply_and_hide_use_the_page_token(env):
    conn, ledger = env
    from studio.agents import community
    replay = Replay(CASSETTE["comments"])
    g = adapter(replay)
    (c,) = g.list_comments("200000000000001_300000000000001")
    assert c.external_id == "300000000000001_400000000000001" and c.author == "Demo Commenter"
    assert c.created_ts > 0
    cid = community.ingest(conn, c.external_id, c.message, c.author, c.created_ts,
                           c.object_id)
    assert community.ingest(conn, c.external_id, c.message, c.author, c.created_ts,
                            c.object_id) is None  # deduplicated by Graph id
    pending, reply = community.request(conn, ledger, cid, "reply.post", "Kubek: 89 PLN.")
    v = approve(conn, ledger, "reply.post", community.item_id_for(cid), reply,
                community.run_id_for(cid))
    assert g.reply(reply, v) == "300000000000001_400000000000002"
    hide = community.hide_payload({"id": cid, "external_id": c.external_id})
    vh = approve(conn, ledger, "comment.hide", community.item_id_for(cid), hide,
                 community.run_id_for(cid))
    g.hide(hide, vh)
    _assert_token_transport(replay, PAGE_TOKEN)
    assert replay.sent[0][0].headers["authorization"] == f"Bearer {PAGE_TOKEN}"
    assert replay.sent[1][1] == {"message": "Kubek: 89 PLN.", "access_token": PAGE_TOKEN}
    assert replay.sent[2][1] == {"is_hidden": "true", "access_token": PAGE_TOKEN}


@pytest.mark.parametrize("body", [
    '<iframe src="https://evil.test/ads/api/preview_iframe.php?d=x"></iframe>',
    '<iframe src="http://www.facebook.com/ads/api/preview_iframe.php?d=x"></iframe>',
    '<iframe src="https://www.facebook.com/other.php"></iframe>',
    '<iframe src="https://www.facebook.com.evil.test/ads/api/preview_iframe.php"></iframe>',
    '<iframe src="javascript:alert(1)"></iframe>',
    "<script>alert(1)</script>",
])
def test_preview_accepts_only_meta_preview_iframes(body):
    with pytest.raises(AdapterError):
        from_graph_body(body)


def test_adapter_repr_has_no_token():
    g = GraphAdapter(TOKEN, "100000000000001", "600000000000001")
    assert TOKEN not in repr(g) and g.account == "act_100000000000001"


APP_SECRET = "app-secret-not-a-secret"  # noqa: S105


def _sequence(g, p, v):
    image = g.upload_image(PNG, p, v)
    cmp = g.create_campaign_paused(p, v)
    adset = g.create_adset_paused(cmp, p, v)
    ad = g.create_ad_paused(adset, g.create_creative(image, p, v), p, v)
    g.preview(ad)


def test_bearer_transport_is_an_option(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g8", p, "run_m1")
    replay = Replay(CASSETTE["interactions"])
    _sequence(adapter(replay, token_transport="bearer"), p, v)
    _assert_token_transport(replay, mode="bearer")
    with pytest.raises(ValueError):
        adapter(replay, token_transport="query")


@pytest.mark.parametrize("mode", ["form", "bearer"])
def test_appsecret_proof_on_every_call_when_the_app_secret_is_set(env, mode):
    import hashlib as h
    import hmac
    from urllib.parse import parse_qs as pq
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g9" + mode[0], p, "run_m1")
    replay = Replay(CASSETTE["interactions"])
    _sequence(adapter(replay, token_transport=mode, app_secret=APP_SECRET), p, v)
    want = hmac.new(APP_SECRET.encode(), TOKEN.encode(), h.sha256).hexdigest()
    for request, form in replay.sent:
        got = form.get("appsecret_proof") or pq(request.url.query.decode()).get(
            "appsecret_proof", [""])[0]
        assert got == want, request.url
        assert APP_SECRET not in str(request.url) and APP_SECRET not in (
            request.content.decode())
    _assert_token_transport(replay, mode=mode)
    replay = Replay(CASSETTE["comments"][:1])
    adapter(replay, app_secret=APP_SECRET).list_comments("200000000000001_300000000000001")
    page_proof = hmac.new(APP_SECRET.encode(), PAGE_TOKEN.encode(), h.sha256).hexdigest()
    assert pq(replay.sent[0][0].url.query.decode())["appsecret_proof"] == [page_proof]


def test_no_proof_without_an_app_secret(env):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g10", p, "run_m1")
    replay = Replay(CASSETTE["interactions"])
    _sequence(adapter(replay), p, v)
    assert all("appsecret_proof" not in f and "appsecret_proof" not in str(r.url)
               for r, f in replay.sent)


@pytest.mark.parametrize("mode", ["form", "bearer"])
def test_token_never_in_errors_or_logs(env, mode, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g11" + mode[0], p, "run_m1")
    errors = [{"status": 400, "json": {"error": {"code": 190, "message": f"bad {TOKEN}"}}}]
    replay = Replay([], errors=errors)
    with pytest.raises(AdapterError) as exc:
        adapter(replay, token_transport=mode, app_secret=APP_SECRET).create_campaign_paused(
            p, v)
    assert TOKEN not in str(exc.value) and TOKEN not in repr(exc.value)
    for record in caplog.records:
        assert TOKEN not in record.getMessage() and APP_SECRET not in record.getMessage()
    assert not [r for r in ledger.receipts() if TOKEN in json.dumps(r.details)]


def _graph_error(code, **extra):
    return {"status": 400, "json": {"error": {"message": "x", "type": "OAuthException",
                                              "code": code, **extra}}}


@pytest.mark.parametrize("error", [_graph_error(80000), _graph_error(80004),
                                   _graph_error(80014), _graph_error(2, is_transient=True)])
def test_business_use_case_throttling_and_is_transient_are_retried(env, error):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g12", p, "run_m1")
    sleeps = []
    replay = Replay(CASSETTE["interactions"][1:2], errors=[error])
    assert adapter(replay, sleeps).create_campaign_paused(p, v) == "120200000000000101"
    assert sleeps == [1]


@pytest.mark.parametrize("error", [_graph_error(80015), _graph_error(2),
                                   _graph_error(190, is_transient=True),
                                   _graph_error(100, is_transient="true")])
def test_other_codes_are_fatal(env, error):
    conn, ledger = env
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_g13", p, "run_m1")
    replay = Replay([], errors=[error])
    with pytest.raises(AdapterError) as exc:
        adapter(replay).create_campaign_paused(p, v)
    assert not exc.value.transient and len(replay.sent) == 1
