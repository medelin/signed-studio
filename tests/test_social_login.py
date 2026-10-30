"""Sign in / register with Google or Facebook: the state binds the callback to the browser
that started it, a new identity gets a trial account, an e-mail never links an existing
account on its own, and a trial account on a live adapter signs only what the judge signs."""

import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import social_login
from studio.app import COOKIE, create_app
from studio.social_login import SocialLoginConfig
from tests.test_judge_live import LIVE, _pending
from tests.test_pixel_gate import _sign
from tests.test_ui_studio import PASSWORD, csrf, register, settings

SOCIAL = {"GOOGLE_LOGIN_CLIENT_ID": "gid.apps.googleusercontent.com",
          "GOOGLE_LOGIN_CLIENT_SECRET": "g-secret",
          "FACEBOOK_LOGIN_CLIENT_ID": "1120296133738519",
          "FACEBOOK_LOGIN_CLIENT_SECRET": "f" * 32,
          "STUDIO_PUBLIC_URL": "https://testserver"}


class Provider:
    """Google's and Facebook's token and profile endpoints."""

    def __init__(self, profile):
        self.profile = profile
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "tok-123"})
        return httpx.Response(200, json=self.profile)


def _app(tmp_path, profile, **extra):
    app = create_app(settings(tmp_path, **SOCIAL, **extra))
    fake = Provider(profile)
    app.state.social_http = httpx.Client(transport=httpx.MockTransport(fake))
    return app, fake


def _sign_in(c, provider="google", nxt="/studio"):
    r = c.get(f"/auth/{provider}/start", params={"next": nxt}, follow_redirects=False)
    assert r.status_code == 303
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return c.get(f"/auth/{provider}/callback", params={"code": "c0de", "state": state},
                 follow_redirects=False)


GOOGLE = {"sub": "1098", "email": "ola@example.com", "email_verified": True, "name": "Ola"}


def test_config_needs_both_halves():
    assert SocialLoginConfig.from_env({}).enabled == []
    assert SocialLoginConfig.from_env(SOCIAL).enabled == ["google", "facebook"]
    with pytest.raises(ValueError, match="go together"):
        SocialLoginConfig.from_env({"GOOGLE_LOGIN_CLIENT_ID": "x"})
    with pytest.raises(ValueError, match="numeric"):
        SocialLoginConfig.from_env({"FACEBOOK_LOGIN_CLIENT_ID": "abc",
                                    "FACEBOOK_LOGIN_CLIENT_SECRET": "s"})


def test_start_sends_state_and_pkce_to_google(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/auth/google/start", follow_redirects=False)
        url = urlsplit(r.headers["location"])
        q = parse_qs(url.query)
        assert url.netloc == "accounts.google.com"
        assert q["redirect_uri"] == ["https://testserver/auth/google/callback"]
        assert q["code_challenge_method"] == ["S256"] and len(q["state"][0]) >= 40
        cookie = r.headers["set-cookie"]
        assert social_login.STATE_COOKIE in cookie and "samesite=lax" in cookie.lower()
        assert "path=/auth/" in cookie.lower()


def test_new_identity_registers_a_trial_account_and_signs_it_in(tmp_path):
    app, fake = _app(tmp_path, GOOGLE)
    with TestClient(app, base_url="https://testserver") as c:
        r = _sign_in(c)
        assert r.status_code == 200 and 'http-equiv="refresh"' in r.text
        assert 'url=/studio"' in r.text
        assert COOKIE in r.cookies
        account = app.state.sessions.account_of(r.cookies[COOKIE])
        assert account.tier == "trial" and not account.has_password
        assert account.email == "ola@example.com" and account.username.startswith("ola-")
        assert b"code_verifier" in fake.seen[0].content
        assert c.get("/account").status_code == 200
    with TestClient(app, base_url="https://testserver") as c:  # a later sign-in: same account
        again = app.state.sessions.account_of(_sign_in(c).cookies[COOKIE])
        assert again.id == account.id
    assert len(app.state.accounts.listing()) == 2  # judge + Ola


def test_a_callback_the_browser_did_not_start_is_refused(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    with TestClient(app, base_url="https://testserver") as c:
        c.get("/auth/google/start", follow_redirects=False)
        r = c.get("/auth/google/callback", params={"code": "c", "state": "forged"},
                  follow_redirects=False)
        assert r.headers["location"] == "/login?social_error=state"
    with TestClient(app, base_url="https://testserver") as other:  # no state cookie
        r = other.get("/auth/google/start", follow_redirects=False)
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        other.cookies.clear()
        r = other.get("/auth/google/callback", params={"code": "c", "state": state},
                      follow_redirects=False)
        assert r.headers["location"] == "/login?social_error=state"


def test_a_state_works_once_and_for_its_own_provider(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/auth/google/start", follow_redirects=False)
        state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
        r = c.get("/auth/facebook/callback", params={"code": "c", "state": state},
                  follow_redirects=False)
        assert r.headers["location"] == "/login?social_error=state"


def test_an_email_match_never_takes_over_an_existing_account(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    owner = app.state.accounts.create("ola", PASSWORD, email="ola@example.com")
    with TestClient(app, base_url="https://testserver") as c:
        account = app.state.sessions.account_of(_sign_in(c).cookies[COOKIE])
        assert account.id != owner.id


def test_a_signed_in_owner_links_a_provider(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    owner = app.state.accounts.create("ola", PASSWORD)
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/login", data={"username": "ola", "password": PASSWORD,
                                      "csrf": csrf(c)}).status_code == 200
        r = _sign_in(c, nxt="/account")
        assert "url=/account?done=linked#password" in r.text
    assert app.state.accounts.by_identity("google", "1098").id == owner.id


def test_facebook_profile_call_carries_appsecret_proof(tmp_path):
    app, fake = _app(tmp_path, {"id": "5551", "name": "Kofi", "email": "kofi@example.com"})
    with TestClient(app, base_url="https://testserver") as c:
        account = app.state.sessions.account_of(_sign_in(c, "facebook").cookies[COOKIE])
    assert account.tier == "trial"
    profile = fake.seen[1]
    q = parse_qs(profile.url.query.decode())
    want = hmac.new(b"f" * 32, b"tok-123", hashlib.sha256).hexdigest()
    assert q["appsecret_proof"] == [want] and profile.url.host == "graph.facebook.com"


def test_unverified_google_email_is_not_kept(tmp_path):
    app, _ = _app(tmp_path, {**GOOGLE, "email_verified": False})
    with TestClient(app, base_url="https://testserver") as c:
        account = app.state.sessions.account_of(_sign_in(c).cookies[COOKIE])
    assert account.email == ""


def test_first_password_only_right_after_a_provider_sign_in(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    new = "Ola-" + "x9" * 10
    with TestClient(app, base_url="https://testserver") as c:
        account = app.state.sessions.account_of(_sign_in(c).cookies[COOKIE])
        app.state.external_signins[account.id] = 0.0  # long ago
        r = c.post("/account/password", data={"csrf": csrf(c), "current": "", "new": new,
                                              "confirm": new})
        assert not app.state.accounts.get(account.id).has_password
        app.state.external_signins.pop(account.id)
        _sign_in(c)
        r = c.post("/account/password", data={"csrf": csrf(c), "current": "", "new": new,
                                              "confirm": new}, follow_redirects=False)
        assert r.status_code == 303, r.text
    assert app.state.accounts.get(account.id).has_password


def test_a_trial_account_signs_only_what_the_judge_signs_on_a_live_adapter(tmp_path):
    app, _ = _app(tmp_path, GOOGLE, **LIVE)
    with TestClient(app, base_url="https://testserver") as c:
        _sign_in(c)
        key, approver = register(c)
        aid = _pending(app, "campaign.activate")
        _, r = _sign(c, key, approver, aid)
        assert r.status_code == 403 and r.json() == {"error": "demo_live_write"}
        app.state.accounts.set_tier(app.state.accounts.listing()[-1].username, "full")
        _, r = _sign(c, key, approver, aid)
        assert r.json() != {"error": "demo_live_write"}


def test_login_page_offers_the_configured_providers(tmp_path):
    app, _ = _app(tmp_path, GOOGLE)
    with TestClient(app, base_url="https://testserver") as c:
        page = c.get("/login").text
        assert 'href="/auth/google/start?next=/"' in page
        assert "/auth/facebook/start" in page
    plain = create_app(settings(tmp_path / "plain"))
    with TestClient(plain, base_url="https://testserver") as c:
        assert "/auth/google/start" not in c.get("/login").text


def test_studio_events_reach_full_accounts_only(tmp_path):
    app = create_app(settings(tmp_path, STUDIO_MAIL_TRANSPORT="mock",
                              STUDIO_MAIL_FROM="Muse <muse@example.org>"))
    app.state.accounts.create("owner", PASSWORD, email="owner@example.com")
    app.state.accounts.create_external("google", "g9", email="trial@example.com")
    with TestClient(app, base_url="https://testserver"):
        app.state.notifier.event("lead_new")
        app.state.notifier.flush()
    assert [m.to for m in app.state.notifier.outbox] == ["owner@example.com"]
