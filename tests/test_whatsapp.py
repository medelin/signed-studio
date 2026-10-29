"""WhatsApp Cloud API: settings, the webhook's HMAC and handshake, idempotency, unknown
senders, a voice note → transcript → Brief v1 draft → one reply, template payloads,
notifications next to e-mail, receipts without phone numbers, the account's number."""

import json
import re
import secrets
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import whatsapp
from studio.agents import producer
from studio.app import create_app
from studio.muse import voice
from studio.muse.client import MuseClient
from studio.settings import Budget, ConfigError, Settings
from studio.ui import i18n
from tests.cassettes import FAKE_KEY
from tests.test_account_pages import HTML, JUDGE, PASSWORD, login, post
from tests.test_brief_flow import OWNER, draft_json

SECRET = "as-" + secrets.token_hex(16)
VERIFY = "vt-" + secrets.token_hex(10)
NUMBER = "+48600111222"
SENDER = NUMBER[1:]
PHONE_ID = "1385668691299457"
WA_ENV = {"STUDIO_WHATSAPP": "mock", "META_APP_SECRET": SECRET,
          "META_WHATSAPP_PHONE_ID": PHONE_ID, "STUDIO_WHATSAPP_VERIFY_TOKEN": VERIFY,
          "STUDIO_PUBLIC_URL": "https://muse.example.test"}


def link(app, account_id, number, *, on=True):
    """Claim a number and prove it (the code sent from that number), as an owner does."""
    whatsapp.set_preferences(app.state.conn, account_id, number=number, on=on)
    code = whatsapp.issue_code(app.state.conn, account_id)
    got = whatsapp.verify_code(app.state.conn, number[1:], f"my code {code}")
    assert got is not None and got.account_id == account_id
    return got


def make(tmp_path, **extra):
    env = {"STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": JUDGE,
           "SESSION_SECRET": secrets.token_urlsafe(32), "STUDIO_REQUIRE_LOGIN": "1",
           **WA_ENV, **extra}
    app = create_app(Settings.load(env))
    app.state.accounts.create("ola", PASSWORD, display_name="Ola")
    ola = app.state.accounts.by_username("ola")
    app.state.accounts.update_profile(ola.id, {"lang": "pl"})
    link(app, ola.id, NUMBER)
    chats = []

    def handler(request):
        body = json.loads(request.content)
        chats.append(body)
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(draft_json())}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 400}})

    app.state.muse = MuseClient(FAKE_KEY, ledger=app.state.ledger, budget=Budget(1.0),
                                transport=httpx.MockTransport(handler))
    app.state.chats = chats
    return app


@pytest.fixture
def app(tmp_path):
    return make(tmp_path)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c


@pytest.fixture
def transcribed(monkeypatch):
    calls = []

    class T:
        text = "Warsztaty garncarskie w Krakowie, soboty o 10:00, 180 zł od osoby."

    def to_wav(data):
        calls.append(("wav", data))
        return b"RIFFwav"

    def transcribe(muse, wav, *, run_id, language_bias=("pl",), mode="ENDPOINTING"):
        calls.append(("transcribe", wav, language_bias))
        return T()

    monkeypatch.setattr(voice, "to_wav", to_wav)
    monkeypatch.setattr(voice, "transcribe", transcribe)
    return calls


def event(*messages, phone_id=PHONE_ID):
    return {"object": "whatsapp_business_account", "entry": [{"id": "1947426389722104",
            "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15551982857",
                             "phone_number_id": phone_id},
                "messages": list(messages)}}]}]}


def audio(mid="wamid.A1", sender=SENDER, media="771"):
    return {"from": sender, "id": mid, "timestamp": str(int(time.time())), "type": "audio",
            "audio": {"id": media, "mime_type": "audio/ogg; codecs=opus", "voice": True}}


def text(body, mid="wamid.T1", sender=SENDER):
    return {"from": sender, "id": mid, "timestamp": str(int(time.time())), "type": "text",
            "text": {"body": body}}


def deliver(c, payload, *, secret=SECRET, signature=None, ctype="application/json"):
    body = json.dumps(payload).encode()
    headers = {"content-type": ctype}
    sig = whatsapp.sign(body, secret) if signature is None else signature
    if sig:
        headers["x-hub-signature-256"] = sig
    return c.post("/webhooks/whatsapp", content=body, headers=headers)


def sent(app):
    app.state.whatsapp_inbox.flush()
    app.state.notifier.flush()
    return app.state.whatsapp.transport.sent


def wa_receipts(app):
    return [r for r in app.state.ledger.receipts() if r.run_id == "whatsapp"]


# -- settings ----------------------------------------------------------------------------


def test_off_unless_configured_and_strict_when_on(tmp_path):
    assert not Settings.load({}).whatsapp.enabled
    real = {"STUDIO_WHATSAPP": "1", "META_WHATSAPP_TOKEN": "EAAG" + secrets.token_hex(20),
            "META_WHATSAPP_PHONE_ID": PHONE_ID, "META_APP_SECRET": SECRET,
            "STUDIO_WHATSAPP_VERIFY_TOKEN": VERIFY}
    s = Settings.load(real)
    assert s.whatsapp.transport == "graph" and s.whatsapp.phone_id == PHONE_ID
    assert real["META_WHATSAPP_TOKEN"] not in repr(s) and SECRET not in repr(s.whatsapp)
    for bad in ({"META_WHATSAPP_TOKEN": ""}, {"META_WHATSAPP_PHONE_ID": "abc"},
                {"META_APP_SECRET": ""}, {"STUDIO_WHATSAPP_VERIFY_TOKEN": "short"},
                {"META_WHATSAPP_WABA_ID": "x1"}, {"STUDIO_WHATSAPP": "maybe"}):
        with pytest.raises(ConfigError):
            Settings.load({**real, **bad})
    assert Settings.load({"STUDIO_WHATSAPP": "mock"}).whatsapp.transport == "mock"


def test_numbers_are_validated_as_e164():
    assert whatsapp.normalize_number("+48 600-111 222") == NUMBER
    assert whatsapp.normalize_number("0048600111222") == NUMBER
    assert whatsapp.normalize_number("(+48) 600.111.222") == NUMBER
    for bad in ("600111222", "+0123456789", "+48 600 abc", "+1234", "+" + "1" * 16, "",
                "+48600111222\n"[:0] + "+48٠600111222"):
        with pytest.raises(ValueError):
            whatsapp.normalize_number(bad)


# -- the webhook -------------------------------------------------------------------------


def test_verify_challenge(client):
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe",
                                                  "hub.verify_token": VERIFY,
                                                  "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    for params in ({"hub.mode": "subscribe", "hub.verify_token": "wrong-token-123456",
                    "hub.challenge": "1"},
                   {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY,
                    "hub.challenge": "1"},
                   {"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                    "hub.challenge": "<script>"}, {}):
        assert client.get("/webhooks/whatsapp", params=params).status_code == 403


def test_signature_good_bad_missing(app, client, transcribed):
    assert deliver(client, event(text("x", mid="wamid.bad1")),
                   signature="sha256=" + "0" * 64).status_code == 403
    assert deliver(client, event(text("x", mid="wamid.bad2")), signature="").status_code == 403
    assert deliver(client, event(text("x", mid="wamid.bad3")),
                   secret="another-secret").status_code == 403
    assert deliver(client, event(text("x", mid="wamid.bad4")),
                   signature="md5=abc").status_code == 403
    assert sent(app) == [] and wa_receipts(app) == []
    r = deliver(client, event(text("Kubki z gliny, 40 zł dziennie.")))
    assert r.status_code == 200 and r.json()["queued"] == 1


def test_webhook_is_public_and_csrf_exempt_but_nothing_else_is(app, client):
    # no session, a form content type, no CSRF token: only the HMAC decides
    r = deliver(client, event(), ctype="application/x-www-form-urlencoded")
    assert r.status_code == 200
    assert deliver(client, event(), signature="",
                   ctype="application/x-www-form-urlencoded").status_code == 403
    assert client.post("/api/briefs/draft", content=b"a=b", headers={
        "content-type": "application/x-www-form-urlencoded"}).status_code == 403


def test_oversized_body_is_refused(client):
    big = event(text("x" * (whatsapp.MAX_WEBHOOK_BODY + 10)))
    assert deliver(client, big).status_code == 413


def test_unknown_sender_gets_no_reply_and_no_processing(app, client, transcribed):
    assert deliver(client, event(audio(sender="48999000111"))).json()["queued"] == 0
    assert sent(app) == [] and transcribed == [] and app.state.chats == []
    [r] = wa_receipts(app)
    assert r.details["status"] == "ignored_unknown_sender" and r.details["account"] == ""
    assert "48999000111" not in json.dumps(r.details)


def test_other_phone_number_ids_are_ignored(app, client):
    assert deliver(client, event(text("hej"), phone_id="999")).json()["queued"] == 0
    assert wa_receipts(app) == []


def test_each_message_is_handled_once(app, client, transcribed):
    payload = event(text("Kubki z gliny w Krakowie, 40 zł dziennie."))
    assert deliver(client, payload).json()["queued"] == 1
    assert deliver(client, payload).json()["queued"] == 0  # Meta's retry
    sent(app)
    assert len(app.state.chats) == 1
    with app.state.conn.lock:
        assert app.state.conn.execute(
            "SELECT count(*) FROM briefs WHERE channel = 'whatsapp'").fetchone()[0] == 1


def test_rate_limit_per_sender(app, client, monkeypatch):
    monkeypatch.setattr(whatsapp, "RATE_MAX", 2)
    monkeypatch.setattr(app.state.whatsapp_inbox, "make_brief",
                        lambda who, m, a: whatsapp.BriefResult("b_" + "0" * 16, 1, 0))
    queued = [deliver(client, event(text("hej", mid=f"wamid.R{i}"))).json()["queued"]
              for i in range(4)]
    assert queued == [1, 1, 0, 0]
    sent(app)
    assert [r.details["status"] for r in wa_receipts(app)
            if r.details["tool"] == "whatsapp.inbound"].count("rate_limited") == 2


def test_voice_note_becomes_a_brief_draft_and_one_reply(app, client, transcribed):
    app.state.whatsapp.transport.media["771"] = (b"OggS-opus-bytes", "audio/ogg")
    assert deliver(client, event(audio())).json()["queued"] == 1
    out = sent(app)
    assert transcribed[0] == ("wav", b"OggS-opus-bytes")
    assert transcribed[1][2] == ("pl",)  # the account's language biases the transcription
    with app.state.conn.lock:
        row = app.state.conn.execute(
            "SELECT id, source, channel, account_id, run_id, draft FROM briefs "
            "WHERE channel = 'whatsapp'").fetchone()
    ola = app.state.accounts.by_username("ola")
    assert row[1:5] == ("voice", "whatsapp", ola.id, "")  # a draft, never a run
    brief_id = row[0]
    [reply] = out
    p = reply["payload"]
    assert p["type"] == "text" and p["to"] == SENDER
    d = draft_json()
    facts, questions = json.loads(row[5])["confirmed_facts"], json.loads(row[5])[
        "open_questions"]
    assert p["text"]["body"] == i18n.PL["whatsapp.reply.ready"].format(
        facts=len(facts), questions=len(questions),
        link=f"https://muse.example.test/studio?brief={brief_id}")
    assert len(questions) >= len(d["open_questions"])
    # the transcript went to the model enveloped, as a web voice brief does
    assert "transcript" in json.dumps(app.state.chats[0]).lower()
    # nothing ran: no run_state, no approval
    with app.state.conn.lock:
        assert app.state.conn.execute("SELECT count(*) FROM run_state").fetchone()[0] == 0
        assert app.state.conn.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    # the link opens the stored draft for a signed-in owner, as a transcript
    assert client.get(f"/api/briefs/{brief_id}").status_code == 401
    assert login(client).status_code == 303
    got = client.get(f"/api/briefs/{brief_id}").json()
    assert got["brief_id"] == brief_id and got["source"] == "transcript"
    assert got["brief"]["confirmed_facts"] == facts
    assert client.get("/api/briefs/b_" + "f" * 16).status_code == 404


def test_typed_brief_from_whatsapp_runs_as_untrusted_text(app, client, monkeypatch):
    assert deliver(client, event(text(OWNER))).json()["queued"] == 1
    sent(app)
    with app.state.conn.lock:
        brief_id = app.state.conn.execute(
            "SELECT id FROM briefs WHERE channel = 'whatsapp' AND source = 'text'"
        ).fetchone()[0]
    started = []
    monkeypatch.setattr(producer, "run", lambda ctx, brief, *, run_id: started.append(
        ctx.brief_source) or producer.mark_failed(ctx.conn, run_id, "stopped_by_test"))
    assert login(client).status_code == 303
    d = client.get(f"/api/briefs/{brief_id}").json()
    r = client.post("/api/runs", json={
        "brief_id": brief_id, "brief_v1": d["brief"],
        "resolutions": [{"action": "strike"} for _ in d["brief"]["open_questions"]]})
    assert r.status_code == 202, r.text
    app.state.last_thread.join(10)
    assert started == ["transcript"]


def test_failure_is_told_to_the_owner(app, client, transcribed):
    # no media for this id: the download fails, the owner hears why, nothing is stored
    assert deliver(client, event(audio(media="404"))).json()["queued"] == 1
    [reply] = sent(app)
    assert "graph_100" in reply["payload"]["text"]["body"]
    assert [r.details["status"] for r in wa_receipts(app)
            if r.details["tool"] == "whatsapp.inbound"] == ["accepted", "brief_failed"]


def test_read_only_does_no_work(tmp_path):
    app = make(tmp_path, STUDIO_READ_ONLY="1")
    with TestClient(app, base_url="https://testserver") as c:
        assert deliver(c, event(text("hej"))).json()["queued"] == 0
    assert [r.details["status"] for r in wa_receipts(app)] == ["refused"]


def test_receipts_carry_no_phone_numbers(app, client, transcribed):
    app.state.whatsapp.transport.media["771"] = (b"OggS", "audio/ogg")
    deliver(client, event(audio(), text("hej", mid="wamid.T9"),
                          audio(mid="wamid.X", sender="48999000111")))
    app.state.notifier.event("lead_new", count=2)
    sent(app)
    dump = json.dumps([r.details for r in wa_receipts(app)])
    assert wa_receipts(app) and "wamid" not in dump
    for digits in (SENDER, "48999000111", "600111222", "999000111"):
        assert digits not in dump
    sends = [r for r in wa_receipts(app) if r.details["tool"] == "whatsapp.send"]
    assert sends and all(len(r.details["recipient_sha256"]) == 64 for r in sends)
    assert {r.details["side_effects"] for r in sends} == {"notice"}


# -- templates and notifications ---------------------------------------------------------


def test_template_payload_shape():
    t = whatsapp.TEMPLATES["approval_waiting"]
    p = whatsapp.template_message(NUMBER, t, "pl", ref="ab" * 16)
    assert p == {"messaging_product": "whatsapp", "recipient_type": "individual",
                 "to": SENDER, "type": "template", "template": {
                     "name": "muse_approval_waiting", "language": {"code": "pl"},
                     "components": [{"type": "button", "sub_type": "url", "index": "0",
                                     "parameters": [{"type": "text", "text": "ab" * 16}]}]}}
    leads = whatsapp.template_message(NUMBER, whatsapp.TEMPLATES["lead_new"], "en", count=3)
    assert leads["template"]["language"] == {"code": "en_US"}
    assert leads["template"]["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": "3"}]}]


def test_template_definitions_match_what_is_sent():
    defs = list(whatsapp.iter_templates("https://muse.udun.eu"))
    assert {(d["name"], d["language"]) for d in defs} == {
        (t.name, lang) for t in whatsapp.TEMPLATES.values() for lang in ("pl", "en_US")}
    for d in defs:
        assert d["category"] == "UTILITY"
        body, buttons = d["components"]
        assert ("{{1}}" in body["text"]) == ("example" in body)
        [button] = buttons["buttons"]
        assert button["type"] == "URL" and len(button["text"]) <= 25
        assert button["url"].startswith("https://muse.udun.eu/")
        if d["name"] == "muse_approval_waiting":
            assert button["url"].endswith("/approvals/{{1}}") and button["example"]


def approval(app):
    with app.state.conn.lock:
        app.state.conn.execute(
            "INSERT INTO approvals (id, run_id, action, item_id, content_sha256, chain_head, "
            "nonce, ts) VALUES (?, 'run_x1', 'campaign.create', 'c1', ?, ?, ?, ?)",
            ("cd" * 16, "0" * 64, "0" * 64, "n_" + secrets.token_hex(16), int(time.time())))
    return "cd" * 16


def test_notifications_go_to_opted_in_numbers(app, client):
    ref = approval(app)
    app.state.notifier.event("approval_waiting")
    app.state.notifier.event("lead_new", count=4)
    app.state.notifier.event("ad.disapproved")  # the meta-rules hook name
    out = [s["payload"] for s in sent(app)]
    assert [p["template"]["name"] for p in out] == [
        "muse_approval_waiting", "muse_new_leads", "muse_ad_disapproved"]
    assert all(p["to"] == SENDER and p["template"]["language"]["code"] == "pl" for p in out)
    assert out[0]["template"]["components"][0]["parameters"][0]["text"] == ref
    assert out[1]["template"]["components"][0]["parameters"][0]["text"] == "4"
    # the approval link: the login gate first, then the studio following that run
    assert client.get(f"/approvals/{ref}").headers["location"].startswith("/login")
    assert login(client).status_code == 303
    assert client.get(f"/approvals/{ref}").headers["location"] == "/studio?run=run_x1"
    assert client.get("/approvals/nope").headers["location"] == "/studio"


def test_leads_are_rate_limited_and_opt_out_stops_everything(app):
    app.state.notifier.event("lead_new", count=1)
    app.state.notifier.event("lead_new", count=2)
    assert len(sent(app)) == 1
    ola = app.state.accounts.by_username("ola")
    whatsapp.set_preferences(app.state.conn, ola.id, number=NUMBER, on=False)
    approval(app)
    app.state.notifier.event("approval_waiting")
    assert len(sent(app)) == 1


def test_no_approval_no_template(app):
    app.state.notifier.event("approval_waiting")
    assert sent(app) == []


def test_disabled_channel_sends_nothing(tmp_path):
    app = make(tmp_path, STUDIO_WHATSAPP="")
    assert app.state.whatsapp is None and app.state.notifier.whatsapp is None
    app.state.notifier.event("lead_new", count=1)
    app.state.notifier.flush()
    with TestClient(app, base_url="https://testserver") as c:
        assert deliver(c, event(text("hej"))).status_code == 403
        assert c.get("/webhooks/whatsapp", params={
            "hub.mode": "subscribe", "hub.verify_token": VERIFY,
            "hub.challenge": "1"}).status_code == 403


def test_graph_errors_are_receipted_without_raising_into_the_notifier(app):
    app.state.whatsapp.transport.fail = whatsapp.WhatsAppError("graph_131030")
    app.state.notifier.event("lead_new", count=1)
    sent(app)
    [r] = [r for r in wa_receipts(app) if r.details["tool"] == "whatsapp.send"]
    assert r.details["status"] == "failed" and r.details["error"] == "graph_131030"


def test_graph_transport_request(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.X"}]})

    real = httpx.Client

    def client(**kw):
        return real(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(whatsapp.httpx, "Client", client)
    cfg = whatsapp.WhatsAppConfig("graph", token="tok-" + "a" * 20, phone_id=PHONE_ID,
                                  app_secret=SECRET)
    t = whatsapp.GraphTransport(cfg)
    t.post(f"{PHONE_ID}/messages", {"a": 1})
    [req] = seen
    assert str(req.url).startswith(f"https://graph.facebook.com/v25.0/{PHONE_ID}/messages?")
    assert req.headers["authorization"] == "Bearer tok-" + "a" * 20
    assert "appsecret_proof=" in str(req.url)
    with pytest.raises(whatsapp.WhatsAppError):
        t.download("https://evil.example/x", 10)


# -- the account page --------------------------------------------------------------------


def test_account_page_stores_a_valid_number_and_refuses_an_invalid_one(app, client):
    assert login(client).status_code == 303
    page = client.get("/account", headers=HTML).text
    assert i18n.EN["account.whatsapp.legend"] in page and 'name="whatsapp_number"' in page
    r = post(client, "/account/notifications", campaigns="1", leads="1",
             whatsapp_number="600 111", whatsapp="1")
    assert r.status_code == 422
    ola = app.state.accounts.by_username("ola")
    assert whatsapp.preferences(app.state.conn, ola.id) == {"number": NUMBER, "on": True,
                                                           "verified": True}
    r = post(client, "/account/notifications", campaigns="1", leads="1",
             whatsapp_number="+44 20 7946 0958", whatsapp="1")
    # a new number is pending: the page shows a one-time code to send from it
    assert r.status_code == 200 and i18n.PL["account.whatsapp.pending"] in r.text
    code = re.search(r"MUSE-[0-9]{6}", r.text).group(0)
    assert whatsapp.preferences(app.state.conn, ola.id) == {"number": "+442079460958",
                                                           "on": True, "verified": False}
    with app.state.conn.lock:  # hashed at rest
        stored = app.state.conn.execute("SELECT code_sha256 FROM whatsapp_codes").fetchall()
    assert len(stored) == 1 and code not in stored[0][0]
    r = post(client, "/account/whatsapp/code")
    assert r.status_code == 200 and re.search(r"MUSE-[0-9]{6}", r.text)
    assert post(client, "/account/notifications", whatsapp_number="").status_code == 303
    assert whatsapp.preferences(app.state.conn, ola.id) == {"number": "", "on": False,
                                                           "verified": False}
    assert post(client, "/account/notifications", whatsapp="1").status_code == 422


def test_deleting_the_account_erases_the_number(app):
    ola = app.state.accounts.by_username("ola")
    app.state.accounts.delete(ola.id, confirm="ola", password=PASSWORD)
    assert whatsapp.preferences(app.state.conn, ola.id) == {"number": "", "on": False,
                                                           "verified": False}
    assert whatsapp.account_by_sender(app.state.conn, SENDER) is None


def test_i18n_keys_in_both_languages():
    from studio.ui.i18n_whatsapp import EN_WHATSAPP, PL_WHATSAPP
    assert set(EN_WHATSAPP) == set(PL_WHATSAPP)
    for key in EN_WHATSAPP:
        assert i18n.placeholders(EN_WHATSAPP[key]) == i18n.placeholders(PL_WHATSAPP[key])
        assert i18n.EN[key] == EN_WHATSAPP[key] and i18n.PL[key] == PL_WHATSAPP[key]


# -- attacks: another account's brief, a number nobody proved ------------------------------


def second_account(app, name="bob"):
    app.state.accounts.create(name, PASSWORD, display_name=name.title())
    return app.state.accounts.by_username(name)


def whatsapp_brief(app, client, transcribed):
    assert deliver(client, event(text(OWNER, mid="wamid.own1"))).json()["queued"] == 1
    sent(app)
    with app.state.conn.lock:
        return app.state.conn.execute(
            "SELECT id FROM briefs WHERE channel = 'whatsapp'").fetchone()[0]


def test_another_account_cannot_read_or_confirm_a_whatsapp_brief(app, client, transcribed):
    brief_id = whatsapp_brief(app, client, transcribed)
    second_account(app)
    assert login(client, user="bob").status_code == 303
    assert client.get(f"/api/briefs/{brief_id}").status_code == 404
    d = draft_json()
    r = client.post("/api/runs", json={"brief_id": brief_id, "brief_v1": d,
                                       "resolutions": [{"action": "strike"}]})
    assert r.status_code == 409 and r.json()["error"] == "unknown_draft"
    r = client.post("/api/briefs/draft", json={"text": "x", "brief_id": brief_id})
    assert r.status_code in (409, 503)
    # the owner still can
    client.post("/logout", data={"csrf": client.cookies.get("studio_csrf")}, headers=HTML)
    client.cookies.clear()
    assert login(client).status_code == 303
    assert client.get(f"/api/briefs/{brief_id}").status_code == 200


def test_the_judge_demo_account_cannot_read_a_whatsapp_brief(app, client, transcribed):
    brief_id = whatsapp_brief(app, client, transcribed)
    assert login(client, user="judge", password=JUDGE).status_code == 303
    assert client.get(f"/api/briefs/{brief_id}").status_code == 404


def test_claim_without_proof_gets_no_notifications_and_no_intake(app, client, transcribed):
    bob = second_account(app)
    other = "+48600999888"
    whatsapp.set_preferences(app.state.conn, bob.id, number=other, on=True)
    assert whatsapp.preferences(app.state.conn, bob.id)["verified"] is False
    assert whatsapp.account_by_sender(app.state.conn, other[1:]) is None
    app.state.notifier.event("lead_new", count=1)
    assert [s["payload"]["to"] for s in sent(app)] == [SENDER]  # only the verified one
    assert deliver(client, event(text("hej", mid="wamid.p1", sender=other[1:])
                                 )).json()["queued"] == 0
    assert transcribed == []
    [r] = [r for r in wa_receipts(app) if r.details["tool"] == "whatsapp.inbound"]
    assert r.details["status"] == "ignored_unknown_sender"


def test_a_number_already_claimed_by_someone_else_cannot_be_taken_without_proof(app, client):
    bob = second_account(app)
    # bob claims ola's verified number: ola keeps it, bob stays pending
    whatsapp.set_preferences(app.state.conn, bob.id, number=NUMBER, on=True)
    assert whatsapp.account_by_sender(app.state.conn, SENDER).account_id == \
        app.state.accounts.by_username("ola").id
    code = whatsapp.issue_code(app.state.conn, bob.id)
    # the code sent from a different number proves nothing
    assert deliver(client, event(text(code, mid="wamid.v1", sender="48600999888"))
                   ).json()["queued"] == 0
    assert whatsapp.preferences(app.state.conn, bob.id)["verified"] is False
    statuses = [r.details["status"] for r in wa_receipts(app)]
    assert statuses == ["verification_failed"]
    assert sent(app) == []  # no reply to a failed proof


def test_expired_or_wrong_code_fails(app):
    bob = second_account(app)
    whatsapp.set_preferences(app.state.conn, bob.id, number="+48600999888", on=True)
    code = whatsapp.issue_code(app.state.conn, bob.id, now=time.time() - 16 * 60)
    assert whatsapp.verify_code(app.state.conn, "48600999888", code) is None
    code = whatsapp.issue_code(app.state.conn, bob.id)
    wrong = f"MUSE-{(int(code[5:]) + 1) % 10**6:06d}"
    assert whatsapp.verify_code(app.state.conn, "48600999888", wrong) is None
    assert whatsapp.verify_code(app.state.conn, "48600999888", code) is not None
    assert whatsapp.verify_code(app.state.conn, "48600999888", code) is None  # one use


def test_proof_moves_a_number_and_clears_the_old_link(app, client):
    ola = app.state.accounts.by_username("ola")
    bob = second_account(app)
    whatsapp.set_preferences(app.state.conn, bob.id, number=NUMBER, on=True)
    code = whatsapp.issue_code(app.state.conn, bob.id)
    assert deliver(client, event(text(f"kod: {code}", mid="wamid.v2"))
                   ).json()["queued"] == 0
    [reply] = sent(app)
    assert reply["payload"]["type"] == "text" and reply["payload"]["to"] == SENDER
    assert whatsapp.account_by_sender(app.state.conn, SENDER).account_id == bob.id
    assert whatsapp.preferences(app.state.conn, ola.id) == {"number": "", "on": False,
                                                           "verified": False}
    got = {(r.details["status"], r.details["account"]) for r in wa_receipts(app)
           if r.details["tool"] == "whatsapp.inbound"}
    assert got == {("verified", bob.id), ("unlinked", ola.id)}


def test_one_account_per_verified_number_in_the_database(app):
    import sqlite3
    bob = second_account(app)
    with pytest.raises(sqlite3.IntegrityError), app.state.conn.lock:
        app.state.conn.execute("UPDATE accounts SET whatsapp_number = ?, "
                               "whatsapp_verified_ts = 1 WHERE id = ?", (NUMBER, bob.id))


def test_mcp_cannot_read_another_accounts_whatsapp_brief(app, client, transcribed):
    from studio import mcp_server, routes_studio
    brief_id = whatsapp_brief(app, client, transcribed)
    bob = second_account(app)
    assert not routes_studio.owns("whatsapp", app.state.accounts.by_username("ola").id, bob.id)
    assert routes_studio.owns("whatsapp", bob.id, bob.id)
    assert not routes_studio.owns("whatsapp", "", None)
    assert routes_studio.owns("", "", None)

    class S:
        conn = app.state.conn
        account_id = bob.id
    with pytest.raises(mcp_server.McpRefused):
        mcp_server.read_brief(S, "agent", brief_id)
    assert brief_id not in json.dumps(mcp_server.list_briefs(S, "agent"))
