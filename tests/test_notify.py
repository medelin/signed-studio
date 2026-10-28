"""E-mail notifications: configuration, the fake SMTP (no network), who gets what, the rate
limit, receipts without addresses, security mails that cannot be turned off, the judge
account, and a sender that never blocks or breaks a request."""

import logging
import secrets
import smtplib
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import notify
from studio.app import create_app
from studio.receipts import verify
from studio.settings import ConfigError, Settings
from studio.ui import i18n
from tests.forms import csrf
from tests.test_account_pages import HTML, JUDGE, PASSWORD, enable_2fa, login, post

SMTP_PASSWORD = "Sm7-" + secrets.token_hex(12)
SMTP_ENV = {"STUDIO_SMTP_HOST": "smtp.example.test", "STUDIO_SMTP_USER": "muse@udun.eu",
            "STUDIO_SMTP_PASSWORD": SMTP_PASSWORD, "STUDIO_PUBLIC_URL": "https://muse.example.test"}


class FakeSMTP:
    """Stands in for ``smtplib.SMTP_SSL``; class-level log, optional failure or gate."""

    sent: list = []
    calls: list = []
    fail: Exception | None = None
    gate: threading.Event | None = None

    def __init__(self, host, port, timeout=None, **kw):
        FakeSMTP.calls.append({"host": host, "port": port, "timeout": timeout})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.gate is not None:
            FakeSMTP.gate.wait(5)
        if FakeSMTP.fail is not None:
            raise FakeSMTP.fail
        FakeSMTP.calls[-1].update(user=user, password=password)

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.fail, FakeSMTP.gate = [], [], None, None
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def make(tmp_path, env=None):
    base = {"STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": JUDGE,
            "SESSION_SECRET": secrets.token_urlsafe(32), "STUDIO_REQUIRE_LOGIN": "1"}
    base.update(SMTP_ENV if env is None else env)
    app = create_app(Settings.load(base))
    app.state.accounts.create("ola", PASSWORD, display_name="Ola")
    ola = app.state.accounts.by_username("ola")
    app.state.accounts.update_profile(ola.id, {"email": "ola@example.org", "lang": "en"})
    return app


@pytest.fixture
def app(tmp_path):
    return make(tmp_path)


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        assert login(c).status_code == 303
        app.state.notifier.flush()
        FakeSMTP.sent.clear()  # the sign-in mail is not what these tests look at
        yield c


def ola_id(app):
    return app.state.accounts.by_username("ola").id


def mails(app):
    app.state.notifier.flush()
    return FakeSMTP.sent


# -- configuration -----------------------------------------------------------------------


def test_disabled_without_a_host(tmp_path):
    app = make(tmp_path, env={})
    n = app.state.notifier
    assert not n.enabled
    n.event("lead_new")
    n.flush()
    assert FakeSMTP.calls == [] and n.outbox == []
    assert not [r for r in app.state.ledger.receipts() if r.run_id == "notify"]


def test_settings_defaults_and_errors():
    s = Settings.load({**SMTP_ENV})
    assert s.mail.port == 465 and s.mail.sender == "Muse Studio Lite <muse@udun.eu>"
    assert s.public_url == "https://muse.example.test"
    assert SMTP_PASSWORD not in repr(s) and SMTP_PASSWORD not in repr(s.mail)
    for bad in ({"STUDIO_SMTP_PORT": "x"}, {"STUDIO_SMTP_PORT": "0"},
                {"STUDIO_SMTP_USER": ""}, {"STUDIO_SMTP_HOST": "bad host"},
                {"STUDIO_MAIL_FROM": "nobody"}, {"STUDIO_PUBLIC_URL": "http://muse.example.test"},
                {"STUDIO_PUBLIC_URL": "https://u:p@muse.example.test"},
                {"STUDIO_PUBLIC_URL": "https://muse.example.test/x"}):
        with pytest.raises(ConfigError):
            Settings.load({**SMTP_ENV, **bad})
    assert Settings.load({"STUDIO_PUBLIC_URL": "http://localhost:8000"}).public_url
    assert not Settings.load({}).mail.enabled


def test_smtp_is_implicit_tls_with_login_and_a_ten_second_timeout(app):
    app.state.notifier.security("signin", app.state.accounts.by_username("ola"))
    assert len(mails(app)) == 1
    call = FakeSMTP.calls[0]
    assert (call["host"], call["port"], call["timeout"]) == ("smtp.example.test", 465, 10.0)
    assert (call["user"], call["password"]) == ("muse@udun.eu", SMTP_PASSWORD)


# -- security mails ----------------------------------------------------------------------


def test_password_change_sends_a_security_mail_with_a_receipt(app, client, tmp_path):
    post(client, "/account/password", current=PASSWORD, new="Xy9-" + secrets.token_hex(10),
         confirm="")  # mismatch: no mail
    assert mails(app) == []
    new = "Xy9-" + secrets.token_hex(10)
    assert post(client, "/account/password", current=PASSWORD, new=new,
                confirm=new).status_code == 303
    (msg,) = mails(app)
    assert msg["To"] == "ola@example.org" and msg["From"] == "Muse Studio Lite <muse@udun.eu>"
    assert msg["Subject"] == i18n.text("en", "notify.password_changed.subject")
    text = msg.get_body(("plain",)).get_content()
    assert "https://muse.example.test/account#password" in text
    assert msg.get_body(("html",)) is not None
    assert new not in text and PASSWORD not in text
    (r,) = [r for r in app.state.ledger.receipts()
            if r.details.get("event") == "password_changed"]
    assert r.details["status"] == "ok" and r.details["event"] == "password_changed"
    assert r.details["account"] == ola_id(app)
    exported = app.state.ledger.export_jsonl()
    assert "ola@example.org" not in exported and SMTP_PASSWORD not in exported
    path = tmp_path / "r.jsonl"
    path.write_text(exported)
    assert verify(path, app.state.ledger.public_key).ok


def test_two_factor_and_sign_in_and_export_and_deletion_mails(app, client):
    secret, _ = enable_2fa(client)
    assert [m["Subject"] for m in mails(app)] == [
        i18n.text("en", "notify.two_factor_on.subject")]
    client.get("/account/export.json")
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as other:
        login(other)
        from studio import accounts as acc
        other.post("/login/2fa", data={"code": acc.totp_code(secret, acc.totp_step() + 1),
                                       "csrf": csrf(other)}, headers=HTML)
    subjects = [m["Subject"] for m in mails(app)]
    assert i18n.text("en", "notify.export_ready.subject") in subjects
    assert i18n.text("en", "notify.signin.subject") in subjects


def test_deletion_mail_goes_to_the_address_read_before_erasure(app, client):
    assert post(client, "/account/delete", confirm="ola", password=PASSWORD).status_code == 200
    (msg,) = mails(app)
    assert msg["To"] == "ola@example.org"
    assert msg["Subject"] == i18n.text("en", "notify.deletion_requested.subject")


def test_security_mails_ignore_the_preferences(app, client):
    post(client, "/account/notifications")  # both categories off
    prefs = notify.preferences(app.state.conn, ola_id(app))
    assert prefs == {"campaigns": False, "leads": False, "security": True}
    app.state.notifier.security("password_changed", app.state.accounts.by_username("ola"))
    assert len(mails(app)) == 1


def test_polish_account_gets_polish_mail(app):
    ola = app.state.accounts.update_profile(ola_id(app), {"lang": "pl"})
    app.state.notifier.security("signin", ola)
    (msg,) = mails(app)
    assert msg["Subject"] == i18n.text("pl", "notify.signin.subject")
    assert msg.get_body(("plain",)).get_content().startswith(
        str(i18n.text("pl", "notify.signin.body")))


def test_no_mail_without_an_address_and_never_for_the_demo_account(app):
    n = app.state.notifier
    app.state.accounts.update_profile(ola_id(app), {"email": ""})
    n.security("signin", app.state.accounts.by_username("ola"))
    n.event("approval_waiting")
    judge = app.state.accounts.by_username("judge")
    assert judge is not None and judge.is_demo
    n.security("signin", judge)
    with app.state.conn.lock:
        app.state.conn.execute("UPDATE accounts SET email = 'j@example.org' WHERE id = ?",
                               (judge.id,))
    n.event("approval_waiting")
    assert mails(app) == []


# -- studio events -------------------------------------------------------------------------


def test_studio_events_follow_the_category_switches(app, client):
    n = app.state.notifier
    n.event("approval_waiting")
    assert len(mails(app)) == 1
    post(client, "/account/notifications", leads="1")  # campaigns off
    n.event("approval_waiting")
    n.event("campaign_launched")
    n.event("lead_new", count=3)
    sent = mails(app)
    assert len(sent) == 2
    assert i18n.text("en", "notify.lead_new.subject") == sent[1]["Subject"]
    assert "3" in sent[1].get_body(("plain",)).get_content()


def test_preferences_are_stored_and_shown(app, client):
    page = client.get("/account").text
    assert 'name="campaigns" value="1" checked' in page and 'id="notifications"' in page
    r = post(client, "/account/notifications", campaigns="1")
    assert r.status_code == 303 and r.headers["location"].startswith("/account?done=notify")
    assert notify.preferences(app.state.conn, ola_id(app)) == {
        "campaigns": True, "leads": False, "security": True}
    assert 'name="leads" value="1" checked' not in client.get("/account").text


def test_judge_cannot_save_notification_settings(app):
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        assert login(c, "judge", JUDGE).status_code == 303
        assert post(c, "/account/notifications", leads="1").status_code == 403
        assert "notifications" in c.get("/account").text


def test_leads_are_coalesced_within_ten_minutes(app):
    clock = [1000.0]
    n = app.state.notifier
    n.clock = lambda: clock[0]
    n.event("lead_new", count=2)
    clock[0] += 60
    n.event("lead_new", count=1)
    n.event("lead_new", count=4)
    assert len(mails(app)) == 1
    clock[0] += notify.COALESCE_S
    n.event("lead_new", count=1)
    sent = mails(app)
    assert len(sent) == 2
    assert i18n.text("en", "notify.lead_new.body", count=6) in \
        sent[1].get_body(("plain",)).get_content()
    # a different event type has its own window
    n.event("insights_ready", count=1)
    assert len(mails(app)) == 3


def test_a_lead_mail_holds_a_count_and_a_link_only(app):
    app.state.notifier.event("lead_new", count=2)
    (msg,) = mails(app)
    body = msg.get_body(("plain",)).get_content() + msg.get_body(("html",)).get_content()
    assert "https://muse.example.test/leads" in body
    with app.state.conn.lock:
        rows = app.state.conn.execute("SELECT name, email, phone FROM leads").fetchall()
    for row in rows:
        for value in row:
            assert not value or value not in body


def test_executors_announce_created_launched_and_paused(app, monkeypatch):
    from studio.agents import media_buyer

    for name in ("create", "activate"):
        monkeypatch.setattr(media_buyer, name, lambda *a, **k: {"ok": 1})
    monkeypatch.setattr(media_buyer, "update", lambda *a, **k: {"ok": 1})
    ex = app.state.executors
    ex["campaign.create"](None, {})
    ex["campaign.activate"](None, {})
    ex["campaign.update"](None, {"change": {"kind": "budget"}})  # no mail for a budget move
    ex["campaign.update"](None, {"change": {"kind": "status"}})
    assert [m["Subject"] for m in mails(app)] == [
        i18n.text("en", f"notify.{k}.subject")
        for k in ("campaign_created", "campaign_launched", "campaign_paused")]


# -- failure and blocking ------------------------------------------------------------------


def test_a_failing_server_is_receipted_logged_without_secrets_and_harmless(app, caplog):
    FakeSMTP.fail = smtplib.SMTPAuthenticationError(535, b"bad credentials " + b"x")
    with caplog.at_level(logging.DEBUG):
        app.state.notifier.security("signin", app.state.accounts.by_username("ola"))
        app.state.notifier.flush()
    (r,) = [r for r in app.state.ledger.receipts() if r.run_id == "notify"]
    assert r.details["status"] == "failed" and "SMTPAuthenticationError 535" in r.details["error"]
    text = caplog.text
    assert SMTP_PASSWORD not in text and "ola@example.org" not in text
    assert "bad credentials" not in text


def test_sending_does_not_block_the_request(app, client):
    FakeSMTP.gate = gate = threading.Event()
    new = "Xy9-" + secrets.token_hex(10)
    r = post(client, "/account/password", current=PASSWORD, new=new, confirm=new)
    assert r.status_code == 303  # answered while the SMTP login is still waiting
    assert FakeSMTP.sent == []
    gate.set()
    assert len(mails(app)) == 1


def test_mock_host_keeps_mails_in_the_outbox_and_never_connects(tmp_path):
    app = make(tmp_path, env={"STUDIO_SMTP_HOST": "mock"})
    app.state.notifier.security("signin", app.state.accounts.by_username("ola"))
    app.state.notifier.flush()
    (sent,) = app.state.notifier.outbox
    assert sent.to == "ola@example.org" and sent.event == "signin"
    assert FakeSMTP.calls == []
    (r,) = [r for r in app.state.ledger.receipts() if r.run_id == "notify"]
    assert r.details["transport"] == "mock"


# -- JMAP ----------------------------------------------------------------------------------

JMAP_ENV = {"STUDIO_MAIL_TRANSPORT": "jmap", "STUDIO_MAIL_USER": "muse@udun.eu",
            "STUDIO_MAIL_PASSWORD": SMTP_PASSWORD,
            "STUDIO_JMAP_SESSION_URL": "https://mail.example.test/jmap/session",
            "STUDIO_PUBLIC_URL": "https://muse.example.test"}
SESSION = {"apiUrl": "https://mail.example.test/jmap/", "primaryAccounts": {
    "urn:ietf:params:jmap:submission": "acc1", "urn:ietf:params:jmap:mail": "acc1"}}


def jmap_routes(respx_mock, *, sent="sent1", send_reply=None, identity_email="muse@udun.eu"):
    """Session GET plus an API that answers the setup call and the send call."""
    respx_mock.get("https://mail.example.test/jmap/session").respond(json=SESSION)
    calls = []

    def api(request):
        import json as _json
        body = _json.loads(request.content)
        calls.append(body)
        names = [c[0] for c in body["methodCalls"]]
        if names[0] == "Identity/get":
            return httpx.Response(200, json={"methodResponses": [
                ["Identity/get", {"list": [{"id": "idOther", "email": "x@udun.eu"},
                                           {"id": "id1", "email": identity_email}]}, "i"],
                ["Mailbox/query", {"ids": [sent] if sent else []}, "m"]]})
        return httpx.Response(200, json=send_reply or {"methodResponses": [
            ["Email/set", {"created": {"m": {"id": "e1"}}}, "e"],
            ["EmailSubmission/set", {"created": {"s": {"id": "s1"}}}, "s"]]})

    respx_mock.post("https://mail.example.test/jmap/").mock(side_effect=api)
    return calls


@pytest.fixture
def jmap_app(tmp_path):
    return make(tmp_path, env=JMAP_ENV)


def test_jmap_settings_and_errors():
    s = Settings.load(JMAP_ENV)
    assert s.mail.transport == "jmap" and SMTP_PASSWORD not in repr(s)
    assert Settings.load({"STUDIO_JMAP_SESSION_URL": JMAP_ENV["STUDIO_JMAP_SESSION_URL"],
                          "STUDIO_MAIL_USER": "a", "STUDIO_MAIL_PASSWORD": "b"}
                         ).mail.transport == "jmap"  # inferred
    for bad in ({"STUDIO_JMAP_SESSION_URL": "http://mail.example.test/s"},
                {"STUDIO_MAIL_PASSWORD": ""}, {"STUDIO_MAIL_TRANSPORT": "carrier-pigeon"}):
        with pytest.raises(ConfigError):
            Settings.load({**JMAP_ENV, **bad})


def test_jmap_creates_in_sent_and_submits_in_one_post(jmap_app, respx_mock):
    calls = jmap_routes(respx_mock)
    n = jmap_app.state.notifier
    n.security("signin", jmap_app.state.accounts.by_username("ola"))
    n.flush()
    setup, send = calls
    assert [c[0] for c in setup["methodCalls"]] == ["Identity/get", "Mailbox/query"]
    assert setup["methodCalls"][1][1]["filter"] == {"role": "sent"}
    assert [c[0] for c in send["methodCalls"]] == ["Email/set", "EmailSubmission/set"]
    assert send["using"] == ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail",
                             "urn:ietf:params:jmap:submission"]
    email = send["methodCalls"][0][1]["create"]["m"]
    assert email["mailboxIds"] == {"sent1": True} and email["keywords"]["$seen"] is True
    assert email["to"] == [{"email": "ola@example.org"}]
    assert email["from"][0]["email"] == "muse@udun.eu"
    assert {v["value"] != "" for v in email["bodyValues"].values()} == {True}
    sub = send["methodCalls"][1][1]
    assert sub["create"]["s"] == {"identityId": "id1", "emailId": "#m"}
    assert sub["onSuccessUpdateEmail"] == {"#s": {"keywords/$draft": None}}
    assert respx_mock.calls[0].request.headers["authorization"].startswith("Basic ")
    (r,) = [r for r in jmap_app.state.ledger.receipts() if r.run_id == "notify"]
    assert r.details["status"] == "ok" and r.details["transport"] == "jmap"
    # the ids are cached: a second mail is one more POST, no session GET
    n.security("signin", jmap_app.state.accounts.by_username("ola"))
    n.flush()
    assert len(calls) == 3 and respx_mock.calls.call_count == 4


@pytest.mark.parametrize("kwargs,code", [
    ({"sent": ""}, "no_sent_mailbox"), ({"identity_email": "other@udun.eu"}, "no_identity"),
    ({"send_reply": {"methodResponses": [
        ["Email/set", {"notCreated": {"m": {"type": "forbidden"}}}, "e"],
        ["EmailSubmission/set", {"notCreated": {"s": {"type": "invalidEmail"}}}, "s"]]}},
     "email_forbidden"),
    ({"send_reply": {"methodResponses": [["error", {"type": "unknownMethod"}, "e"]]}},
     "jmap_unknownMethod")])
def test_jmap_failures_are_receipted_without_secrets(jmap_app, respx_mock, caplog, kwargs, code):
    jmap_routes(respx_mock, **kwargs)
    with caplog.at_level(logging.DEBUG):
        jmap_app.state.notifier.security("signin", jmap_app.state.accounts.by_username("ola"))
        jmap_app.state.notifier.flush()
    (r,) = [r for r in jmap_app.state.ledger.receipts() if r.run_id == "notify"]
    assert r.details["status"] == "failed" and code in r.details["error"]
    assert SMTP_PASSWORD not in caplog.text and "ola@example.org" not in caplog.text


def test_jmap_http_errors_and_timeouts_are_harmless(jmap_app, respx_mock):
    respx_mock.get("https://mail.example.test/jmap/session").respond(401)
    jmap_app.state.notifier.security("signin", jmap_app.state.accounts.by_username("ola"))
    jmap_app.state.notifier.flush()
    respx_mock.get("https://mail.example.test/jmap/session").mock(
        side_effect=httpx.ConnectTimeout("t"))
    jmap_app.state.notifier.security("signin", jmap_app.state.accounts.by_username("ola"))
    jmap_app.state.notifier.flush()
    errors = [r.details["error"] for r in jmap_app.state.ledger.receipts()
              if r.run_id == "notify"]
    assert errors == ["MailError session_http_401", "ConnectTimeout"]
