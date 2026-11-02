"""Conversions API (CAPI): instant-form leads sent as ``Lead`` events to the owner's dataset,
off by default (``STUDIO_CAPI=1`` and the account's own consent), contact data only as
SHA-256 of Meta's normalised form, one receipt per lead that names no contact data (not
even its hash), and the mock / replay transports never reaching Meta."""

import hashlib
import html as htmllib
import json
from urllib.parse import parse_qs

import httpx
import pytest

from studio import accounts, db, leadads
from studio.agents import media_buyer
from studio.approvals import Keys
from studio.meta import capi
from studio.meta.adapter import AdapterError
from studio.meta.graph import GraphAdapter
from studio.meta.mock import MockAdapter
from studio.receipts import sha256_hex, verify
from studio.replay import RecordingTransport, ReplayTransport
from studio.settings import ConfigError, Settings
from tests.approve import SESSION, approve
from tests.attack.test_lead_ads import lead_payload
from tests.attack.test_paused_only import PNG, RUN
from tests.contract.test_graph import PAGE_TOKEN, TOKEN
from tests.test_account_pages import PASSWORD
from tests.test_ui_studio import settings

DATASET = "1234567890123456"


def h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# -- Meta's normalisation and hashing -----------------------------------------------------

@pytest.mark.parametrize(("raw", "norm"), [
    ("  John.Doe@Example.COM ", "john.doe@example.com"),
    ("one@example.com", "one@example.com"),
    ("not an address", ""), ("", ""), ("a@b", ""),
])
def test_email_is_trimmed_and_lower_cased(raw, norm):
    assert capi.normalize_email(raw) == norm


@pytest.mark.parametrize(("raw", "norm"), [
    ("+48 600-123-456", "48600123456"),
    ("0048 600 123 456", "48600123456"),
    ("+44 (20) 7946 0958", "442079460958"),
    ("+48 000 000 101", "48000000101"),
    # no country code: Meta needs one and a guess could match someone else
    ("600 123 456", ""), ("07700 900123", ""),
    ("+1 23", ""), ("+48 600 abc", ""), ("", ""),
])
def test_phone_is_digits_with_the_country_code_only(raw, norm):
    assert capi.normalize_phone(raw) == norm


def test_the_lead_event_carries_hashes_and_no_raw_contact_data():
    row = {"name": "Demo Lead One", "email": " One@Example.com", "phone": "+48 000 000 101",
           "city": "Kraków", "company": "", "country": "", "kind": ""}
    event = capi.lead_event("l-0a1b2c3d4e", row, "4815162342", 1793264400.7)
    assert event == {
        "event_name": "Lead", "event_time": 1793264400, "event_id": "muse-lead-l-0a1b2c3d4e",
        "action_source": "system_generated",
        "user_data": {"em": [h("one@example.com")], "ph": [h("48000000101")],
                      "lead_id": 4815162342},
        "custom_data": {"event_source": "crm", "lead_event_source": "Muse Studio Lite"}}
    text = json.dumps(event)
    for raw in ("One@Example", "one@example.com", "000 000 101", "48000000101", "Demo",
                "Krak"):
        assert raw not in text
    capi.check_events([event])  # what both adapters check before any call


def test_a_lead_id_that_is_not_metas_number_is_left_out():
    event = capi.lead_event("l-1", {"email": "a@example.com"}, "lead-demo-1", 0.0)
    assert "lead_id" not in event["user_data"] and "ph" not in event["user_data"]


@pytest.mark.parametrize("bad", [
    {"em": ["one@example.com"]}, {"ph": ["48600123456"]}, {"em": "0" * 64},
    {"fn": [h("ola")]}, {"lead_id": "1"},
])
def test_unhashed_or_unknown_user_data_is_refused(bad):
    event = {**capi.lead_event("l-1", {"email": "a@example.com"}, "", 0.0), "user_data": bad}
    with pytest.raises(AdapterError) as exc:
        capi.check_events([event])
    assert exc.value.code == "unhashed"


# -- settings -------------------------------------------------------------------------------

def test_off_by_default_and_switched_on_by_studio_capi(tmp_path):
    assert settings(tmp_path).capi is False
    on = settings(tmp_path, STUDIO_CAPI="1", STUDIO_CAPI_TEST_EVENT_CODE="TEST12345")
    assert on.capi is True and on.capi_test_event_code == "TEST12345"
    with pytest.raises(ConfigError):
        settings(tmp_path, STUDIO_CAPI="yes please")
    with pytest.raises(ConfigError):
        settings(tmp_path, STUDIO_CAPI="1", STUDIO_CAPI_TEST_EVENT_CODE="TEST 1; drop")


def test_a_live_meta_sandbox_refuses_the_conversions_api():
    import secrets
    env = {"STUDIO_SANDBOX": "1", "STUDIO_SANDBOX_LIVE_META": "1", "META_ADAPTER": "graph",
           "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
           "SESSION_SECRET": secrets.token_urlsafe(32), "META_ACCESS_TOKEN": TOKEN,
           "META_AD_ACCOUNT_ID": "act_100000000000001", "META_PAGE_ID": "600000000000001",
           "META_LANDING_URL": "https://example.com/", "STUDIO_CAPI": "1"}
    from studio.sandbox import SandboxConfig
    s = Settings.load(env)
    with pytest.raises(ConfigError, match="STUDIO_CAPI"):
        SandboxConfig.load(env).validate(s)


# -- the adapters -------------------------------------------------------------------------

def _events():
    return [capi.lead_event("l-1", {"email": "a@example.com", "phone": "+48 600 123 456"},
                            "4815162342", 1793264400.0)]


def test_the_mock_records_the_events_and_sends_nothing():
    mock = MockAdapter()
    out = mock.send_events(DATASET, _events(), test_event_code="TEST1")
    assert out["events_received"] == 1
    assert mock.calls == [("send_events", DATASET)]
    assert mock.capi == [{"dataset_id": DATASET, "data": _events(), "test_event_code": "TEST1"}]
    with pytest.raises(AdapterError):
        mock.send_events("not-a-dataset", _events())
    with pytest.raises(AdapterError):
        mock.send_events(DATASET, [{**_events()[0], "user_data": {"em": ["a@example.com"]}}])
    assert len(mock.capi) == 1


def _graph(handler):
    return GraphAdapter(TOKEN, "act_100000000000001", "600000000000001", PAGE_TOKEN,
                        transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_graph_posts_the_events_to_the_dataset_with_the_token_in_the_body():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"events_received": 1, "messages": [],
                                         "fbtrace_id": "AbCdEf123"})

    out = _graph(handler).send_events(DATASET, _events(), test_event_code="TEST12345")
    assert out == {"events_received": 1, "fbtrace_id": "AbCdEf123"}
    (req,) = sent
    assert req.method == "POST" and req.url.path == f"/v25.0/{DATASET}/events"
    assert TOKEN not in str(req.url)
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert form["access_token"] == TOKEN and form["test_event_code"] == "TEST12345"
    assert json.loads(form["data"]) == _events()
    assert "a@example.com" not in req.content.decode()


def test_graph_never_sends_twice_and_refuses_before_any_call():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(500, json={"error": {"code": 2, "is_transient": True,
                                                   "message": "x"}})

    g = _graph(handler)
    with pytest.raises(AdapterError) as exc:
        g.send_events(DATASET, _events())
    assert len(sent) == 1 and exc.value.transient
    for bad in ("act_1", "../me", ""):
        with pytest.raises(AdapterError):
            g.send_events(bad, _events())
    with pytest.raises(AdapterError):
        g.send_events(DATASET, [{**_events()[0], "user_data": {"ph": ["48600123456"]}}])
    assert len(sent) == 1


def test_replay_answers_the_events_itself_and_keeps_them():
    replay = ReplayTransport({"interactions": []})
    g = _graph(replay.handle_request)
    out = g.send_events(DATASET, _events())
    assert out["events_received"] == 1 and replay.stats["miss"] == 0
    assert [c["path"] for c in replay.capi] == [f"/v25.0/{DATASET}/events"]
    assert json.loads(replay.capi[0]["form"]["data"]) == _events()
    assert "access_token" not in replay.capi[0]["form"]


def test_a_recording_never_stores_conversion_events():
    def real(request):
        return httpx.Response(200, json={"events_received": 1})

    rec = RecordingTransport(httpx.MockTransport(real))
    _graph(rec.handle_request).send_events(DATASET, _events())
    assert rec.interactions == []


# -- the send, end to end with the mock ------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    assets = tmp_path / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    store = accounts.Accounts(conn)
    owner = store.create("ola", PASSWORD, display_name="Ola")
    with db.transaction(conn):  # the session that signs (tests.approve.SESSION) is Ola's
        conn.execute("INSERT INTO account_sessions (sid_sha256, account_id, created_ts, "
                     "last_seen_ts, expires_ts, user_agent) VALUES (?, ?, 0, 0, 9e9, '')",
                     (sha256_hex(SESSION), owner.id))
    store.update_profile(owner.id, {"pixel_id": DATASET})
    mock = MockAdapter()
    p = lead_payload()
    v = approve(conn, ledger, "campaign.create", "cmp_capi1", p, RUN)
    out = media_buyer.create(conn, ledger, mock, v, p, tmp_path)
    return conn, ledger, mock, store, owner, out["lead_form_id"], tmp_path


def _capi_receipts(ledger, form_id):
    return [r for r in ledger.receipts(leadads.run_id_for(form_id)) if r.agent == capi.AGENT]


def test_with_the_flag_off_nothing_is_sent_and_nothing_recorded(env, tmp_path):
    conn, ledger, mock, store, owner, form_id, _ = env
    store.set_capi_consent(owner.id, True)
    assert capi.sender(conn, ledger, mock, settings(tmp_path)) is None
    out = leadads.poll(conn, ledger, mock, capi=None)
    assert out["new"] == 3
    assert mock.capi == [] and _capi_receipts(ledger, form_id) == []


def test_a_consenting_owner_gets_one_lead_event_per_lead(env, tmp_path):
    conn, ledger, mock, store, owner, form_id, data = env
    store.set_capi_consent(owner.id, True)
    send = capi.sender(conn, ledger, mock, settings(
        tmp_path, STUDIO_CAPI="1", STUDIO_CAPI_TEST_EVENT_CODE="TEST12345"))
    assert leadads.poll(conn, ledger, mock, capi=send)["new"] == 3
    assert leadads.poll(conn, ledger, mock, capi=send)["new"] == 0  # once per lead
    assert [c for c in mock.calls if c[0] == "send_events"] == [("send_events", DATASET)] * 3
    first = mock.capi[0]
    assert first["dataset_id"] == DATASET and first["test_event_code"] == "TEST12345"
    (event,) = first["data"]
    assert event["event_name"] == "Lead" and event["action_source"] == "system_generated"
    assert event["user_data"]["em"] == [h("one@example.com")]
    assert event["user_data"]["ph"] == [h("48000000101")]
    with conn.lock:
        lead = conn.execute("SELECT id FROM leads WHERE email = 'one@example.com'").fetchone()
    assert event["event_id"] == f"muse-lead-{lead[0]}"
    with conn.lock:  # Meta: the event is later than the lead itself
        made = conn.execute("SELECT created_ts FROM leads WHERE id = ?", lead).fetchone()[0]
    assert event["event_time"] >= int(made)

    receipts = _capi_receipts(ledger, form_id)
    assert [r.details["status"] for r in receipts] == ["ok"] * 3
    d = receipts[0].details
    assert d["tool"] == "capi.send" and d["side_effects"] == "measurement"
    assert d["lead"] == lead[0] and d["event_id"] == event["event_id"]
    assert d["fields"] == ["em", "ph"] and d["test"] is True and d["events_received"] == 1
    assert d["dataset_sha256"] == sha256_hex(DATASET)
    assert d["user_data_sha256"] == sha256_hex(json.dumps(
        event["user_data"], sort_keys=True, separators=(",", ":")))
    assert receipts[0].provider == "meta-mock"
    export = ledger.export_jsonl()
    for secret in ("one@example.com", "000 000 101", "48000000101", h("one@example.com"),
                   h("48000000101"), DATASET):
        assert secret not in export  # not even the hashes: the receipt export is public
    path = data / "e.jsonl"
    path.write_text(export)
    assert verify(path, ledger.public_key, approver_keys=Keys.registry(conn)).ok


@pytest.mark.parametrize("case", ["no_consent", "no_dataset", "trial", "deleted"])
def test_without_consent_a_dataset_or_an_own_account_nothing_is_sent(env, tmp_path, case):
    conn, ledger, mock, store, owner, form_id, _ = env
    store.set_capi_consent(owner.id, case != "no_consent")
    if case == "no_dataset":
        store.update_profile(owner.id, {"pixel_id": ""})
    if case == "trial":
        with db.transaction(conn):
            conn.execute("UPDATE accounts SET tier = 'trial' WHERE id = ?", (owner.id,))
    if case == "deleted":
        store.delete(owner.id, confirm="ola", password=PASSWORD)
    send = capi.sender(conn, ledger, mock, settings(tmp_path, STUDIO_CAPI="1"))
    assert leadads.poll(conn, ledger, mock, capi=send)["new"] == 3
    assert mock.capi == []
    reasons = {r.details["reason"] for r in _capi_receipts(ledger, form_id)}
    assert reasons == {"no_account" if case in ("trial", "deleted") else case}
    assert {r.details["side_effects"] for r in _capi_receipts(ledger, form_id)} == {"none"}


def test_a_failed_send_is_a_receipt_and_the_lead_is_still_stored(env, tmp_path):
    conn, ledger, mock, store, owner, form_id, _ = env
    store.set_capi_consent(owner.id, True)

    def refuse(*a, **k):
        raise AdapterError("no permission on the dataset", code="graph_200")

    mock.send_events = refuse
    send = capi.sender(conn, ledger, mock, settings(tmp_path, STUDIO_CAPI="1"))
    assert leadads.poll(conn, ledger, mock, capi=send)["new"] == 3
    receipts = _capi_receipts(ledger, form_id)
    assert {(r.details["status"], r.details["error"]) for r in receipts} == {
        ("error", "graph_200")}


def test_read_only_sends_nothing(env, tmp_path):
    conn, ledger, mock, *_ = env
    assert capi.sender(conn, ledger, mock, settings(
        tmp_path, STUDIO_CAPI="1", STUDIO_READ_ONLY="1")) is None


# -- consent: stored per account, off by default ---------------------------------------------

def test_consent_is_off_by_default_dated_and_erased_with_the_account(env):
    conn, _, _, store, owner, *_ = env
    assert store.capi_consent(owner.id) == 0
    assert "conversions_api" not in store.export(owner.id, [])
    assert store.set_capi_consent(owner.id, True) is True
    when = store.capi_consent(owner.id)
    assert when > 0
    assert store.set_capi_consent(owner.id, True) is False  # the first date is kept
    assert store.capi_consent(owner.id) == when
    assert store.export(owner.id, [])["conversions_api"] == {"consent": True,
                                                               "consent_ts": when}
    store.delete(owner.id, confirm="ola", password=PASSWORD)
    with conn.lock:
        assert conn.execute("SELECT capi_consent_ts FROM accounts WHERE id = ?",
                            (owner.id,)).fetchone()[0] == 0


# -- the account page ------------------------------------------------------------------------

def _page_app(tmp_path, **extra):
    from tests.test_account_pages import make_app
    a = make_app(tmp_path, **extra)
    a.state.accounts.create("ola", PASSWORD, display_name="Ola")
    return a


def _save(c, **fields):
    from tests.test_account_pages import post
    return post(c, "/account/profile", display_name="Ola", advertiser_type="person", **fields)


def test_with_the_flag_off_the_page_and_the_form_are_unchanged(tmp_path):
    from tests.test_account_pages import client_for, login
    app = _page_app(tmp_path)
    with client_for(app) as c:
        login(c)
        page = c.get("/account").text
        assert "capi_consent" not in page and "Conversions API" not in page
        assert "data-needs-website hidden" in page
        assert _save(c, capi_consent="1").status_code == 303
    aid = app.state.accounts.by_username("ola").id
    assert app.state.accounts.capi_consent(aid) == 0


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_with_the_flag_on_the_owner_ticks_consent_and_it_leaves_a_receipt(tmp_path, lang):
    from studio.ui import i18n
    from tests.test_account_pages import client_for, login
    app = _page_app(tmp_path, STUDIO_CAPI="1")
    store = app.state.accounts
    aid = store.by_username("ola").id
    store.update_profile(aid, {"lang": lang})
    with client_for(app) as c:
        login(c)
        page = c.get("/account").text
        box = page.split('name="capi_consent"', 1)[1].split(">", 1)[0]
        assert "checked" not in box  # off by default
        assert i18n.CATALOGS[lang]["account.capi.consent"] in htmllib.unescape(page)
        # instant-form leads need a dataset even without a website: the field is shown
        assert "data-needs-website" not in page and 'id="pf-pixel-field" hidden' not in page
        assert "<script>" not in page
        assert _save(c, pixel_id=DATASET, capi_consent="1").status_code == 303
        assert store.capi_consent(aid) > 0
        assert 'name="capi_consent" value="1" checked' in c.get("/account").text
        assert _save(c, pixel_id=DATASET).status_code == 303  # unticked: withdrawn
        assert store.capi_consent(aid) == 0
    consents = [r.details for r in app.state.ledger.receipts()
                if r.agent == capi.AGENT and r.details.get("tool") == "capi.consent"]
    assert [d["consent"] for d in consents] == [True, False]
    assert all(d["account"] == aid for d in consents)


def test_the_consent_box_is_not_offered_to_the_judge_or_a_trial_account(tmp_path):
    from tests.test_account_pages import client_for, login
    app = _page_app(tmp_path, STUDIO_CAPI="1")
    aid = app.state.accounts.by_username("ola").id
    with db.transaction(app.state.conn):
        app.state.conn.execute("UPDATE accounts SET tier = 'trial' WHERE id = ?", (aid,))
    with client_for(app) as c:
        login(c)
        assert "capi_consent" not in c.get("/account").text
        assert _save(c, capi_consent="1").status_code == 303
    assert app.state.accounts.capi_consent(aid) == 0


@pytest.mark.parametrize("key", ["account.capi.legend", "account.capi.consent",
                                 "account.capi.hint_html"])
def test_the_consent_texts_exist_in_both_languages(key):
    from studio.ui import i18n
    assert i18n.EN[key].strip() and i18n.PL[key].strip() and i18n.EN[key] != i18n.PL[key]


# -- the routes: the leadgen webhook and the poll endpoint pass the sender -----------------

@pytest.mark.parametrize("flag", ["0", "1"])
def test_the_webhook_offers_its_new_lead_to_the_conversions_api(tmp_path, flag):
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from tests.attack.test_lead_ads import SECRET, _event, _sign
    app = create_app(settings(tmp_path, META_APP_SECRET=SECRET, STUDIO_CAPI=flag))
    with TestClient(app, base_url="https://testserver") as c:
        conn, ledger, mock = app.state.conn, app.state.ledger, app.state.adapter
        store = app.state.accounts
        owner = store.create("ola", PASSWORD, display_name="Ola")
        with db.transaction(conn):
            conn.execute("INSERT INTO account_sessions (sid_sha256, account_id, created_ts, "
                         "last_seen_ts, expires_ts, user_agent) VALUES (?, ?, 0, 0, 9e9, '')",
                         (sha256_hex(SESSION), owner.id))
        store.update_profile(owner.id, {"pixel_id": DATASET})
        store.set_capi_consent(owner.id, True)
        approve(conn, ledger, "campaign.create", "cmp_hook_capi", lead_payload(), RUN)
        form_id = "mock-form-0a1b2c3d4e"
        mock.objects[form_id] = {"type": "lead_form"}
        leadads.record_form(conn, form_id, "cmp_hook_capi", RUN, mock.name)
        body = _event(form_id, f"lead-demo-1-{form_id[-6:]}")
        r = c.post("/api/meta/webhook", content=body,
                   headers={"X-Hub-Signature-256": _sign(body)})
        assert r.status_code == 200 and r.json() == {"received": 1}
    assert len(mock.capi) == (1 if flag == "1" else 0)
    assert len(_capi_receipts(ledger, form_id)) == (1 if flag == "1" else 0)
