"""/account and the two-step login, end to end, plus the attack cases: wrong current
password, a reused TOTP code, a recovery code used twice, CSRF missing, a deleted
account's old cookie, a disabled account, judge deletion refused, rate limits on the
second step, session fixation."""

import html as htmllib
import json
import re
import secrets

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from studio import accounts
from studio.app import create_app
from studio.settings import Settings
from studio.ui import i18n
from tests.forms import csrf
from tests.test_app import _approve_with, _register, _sid

JUDGE = "Jd7-" + secrets.token_urlsafe(24)
PASSWORD = "Lp4-" + secrets.token_hex(12)  # hex: never contains a user name
HTML = {"accept": "text/html"}


def make_app(tmp_path, **extra):
    env = {"STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": JUDGE,
           "SESSION_SECRET": secrets.token_urlsafe(32), "STUDIO_REQUIRE_LOGIN": "1"}
    env.update(extra)
    return create_app(Settings.load(env))


@pytest.fixture
def app(tmp_path):
    a = make_app(tmp_path)
    a.state.accounts.create("ola", PASSWORD, display_name="Ola")
    return a


def client_for(app):
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


@pytest.fixture
def client(app):
    with client_for(app) as c:
        yield c


def login(c, user="ola", password=PASSWORD, nxt=""):
    return c.post("/login", data={"username": user, "password": password, "next": nxt,
                                  "csrf": csrf(c)}, headers=HTML)


def second(c, code):
    return c.post("/login/2fa", data={"code": code, "csrf": csrf(c)}, headers=HTML)


def post(c, path, **fields):
    return c.post(path, data={"csrf": csrf(c), **fields}, headers=HTML)


def enable_2fa(c):
    assert post(c, "/account/2fa/start", password=PASSWORD).status_code == 303
    page = c.get("/account").text
    secret = re.search(r'<code class="secret"[^>]*>([A-Z2-7 ]+)</code>', page).group(1)
    secret = secret.replace(" ", "")
    r = post(c, "/account/2fa/confirm", code=accounts.totp_code(secret, accounts.totp_step()))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    codes = re.findall(r"<li><code>([A-Z2-7-]{19})</code></li>", r.text)
    assert len(codes) == 10
    return secret, codes


def code_at(secret, offset):
    return accounts.totp_code(secret, accounts.totp_step() + offset)


def wrong_code(secret):
    near = {code_at(secret, k) for k in range(-2, 3)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in near)


# -- pages and the header link ------------------------------------------------------------


def test_account_needs_a_session_and_is_linked_from_the_header(client):
    r = client.get("/account", headers=HTML)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert login(client).status_code == 303
    assert 'href="/account"' in client.get("/studio").text
    page = client.get("/account")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert '<a href="/account" aria-current="page">' in page.text
    for section in ("profile", "password", "two-factor", "sessions", "export", "delete"):
        assert f'id="{section}"' in page.text and f'href="#{section}"' in page.text


def test_account_page_is_accessible_and_csp_clean(client):
    login(client)
    html = client.get("/account").text
    assert ' style="' not in html and "<style" not in html
    assert not re.search(r"\son[a-z]+=", html)
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
        assert 'type="application/json"' in attrs or ("src=" in attrs and not body.strip())
    assert html.count("<fieldset") == html.count("<legend>") >= 3
    ids = set(re.findall(r'\sid="([^"]+)"', html))
    for field_id in re.findall(r'<input id="([^"]+)"', html):
        assert f'for="{field_id}"' in html, field_id  # every visible input has a label
    for ref in re.findall(r'aria-describedby="([^"]+)"', html):
        assert set(ref.split()) <= ids, ref
    assert 'role="status" aria-live="polite"' in html
    svgs = re.findall(r"<svg[^>]*>", html)
    assert all('aria-label="' in s or 'aria-hidden="true"' in s for s in svgs)
    assert "c-blob" not in html and "r-grid" not in html


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_account_pages_in_both_languages_have_no_leftover_keys(app, lang):
    with client_for(app) as c:
        login(c)
        post(c, "/account/2fa/start", password=PASSWORD)
        for path in ("/account", "/account?done=profile"):
            page = c.get(path, params={"lang": lang}).text
            text = htmllib.unescape(re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S))
            assert f'<html lang="{lang}" ' in page
            leftovers = [k for k in i18n.EN if k.startswith(("account.", "login.2fa"))
                         and re.search(r"(?<![\w.])" + re.escape(k) + r"(?![\w])", text)]
            assert leftovers == []
            other = "pl" if lang == "en" else "en"
            assert i18n.CATALOGS[lang]["account.section.two-factor"] in text
            assert i18n.CATALOGS[other]["account.delete.receipts"] not in text
            assert i18n.CATALOGS[lang]["account.delete.receipts"] in text
            assert i18n.CATALOGS[lang]["meta.account"] in page


def test_second_step_page_in_polish(app):
    app.state.accounts.create("pola", PASSWORD)
    with client_for(app) as c:
        login(c)
        secret, _ = enable_2fa(c)
        c.post("/logout", data={"csrf": csrf(c)})
        c.get("/login?lang=pl")
        assert login(c).headers["location"] == "/login/2fa"
        page = c.get("/login/2fa").text
        assert i18n.PL["login.2fa_hint"] in page and i18n.EN["login.2fa_hint"] not in page
        r = second(c, wrong_code(secret))
        assert r.status_code == 401 and i18n.PL["login.2fa_failed"] in r.text


# -- profile ----------------------------------------------------------------------------


def test_profile_saves_company_details_and_applies_defaults(client):
    login(client)
    r = post(client, "/account/profile", display_name="Ola K.", email="ola@example.com",
             lang="pl", theme="dark", palette="warm", advertiser_type="company",
             legal_name="Ola Yarn sp. z o.o.", country="Poland", tax_id="PL 000 000 00 00")
    assert r.status_code == 303 and r.headers["location"] == "/account?done=profile#profile"
    cookies = " ".join(r.headers.get_list("set-cookie"))
    assert "lang=pl" in cookies and "theme=dark" in cookies and "palette=warm" in cookies
    page = client.get(r.headers["location"]).text
    assert '<html lang="pl" ' in page and 'data-theme="dark"' in page
    assert 'value="Ola Yarn sp. z o.o."' in page and 'value="PL 000 000 00 00"' in page
    assert i18n.PL["account.done.profile"] in page
    assert i18n.PL["account.profile.billing_note"] in page


def test_profile_error_points_at_the_field(client):
    login(client)
    r = post(client, "/account/profile", display_name="Ola", email="not-an-email",
             advertiser_type="person")
    assert r.status_code == 400
    assert re.search(r'id="pf-email"[^>]*aria-describedby="pf-email-hint profile-error"'
                     r'[^>]*aria-invalid="true"', r.text)
    assert 'id="profile-error" role="alert"' in r.text and 'value="not-an-email"' in r.text


def test_defaults_are_applied_at_the_next_login(app, client):
    login(client)
    post(client, "/account/profile", lang="pl", advertiser_type="person")
    with client_for(app) as fresh:
        r = login(fresh)
        assert "lang=pl" in " ".join(r.headers.get_list("set-cookie"))


# -- password ---------------------------------------------------------------------------


def test_change_password_needs_the_current_one(client):
    login(client)
    new = "New-" + secrets.token_hex(10)
    r = post(client, "/account/password", current="wrong-" + PASSWORD, new=new, confirm=new)
    assert r.status_code == 400 and i18n.EN["err.wrong_password"] in r.text
    assert re.search(r'id="pw-current"[^>]*aria-describedby="password-error"', r.text)
    r = post(client, "/account/password", current=PASSWORD, new="password1234",
             confirm="password1234")
    assert r.status_code == 400 and i18n.EN["err.common_password"] in r.text
    r = post(client, "/account/password", current=PASSWORD, new=new, confirm=new + "x")
    assert r.status_code == 400 and i18n.EN["err.password_mismatch"] in r.text


def test_change_password_signs_out_other_sessions_and_their_keys(app):
    from studio import approvals as ap

    with client_for(app) as me, client_for(app) as other:
        login(me)
        login(other)
        key = Ed25519PrivateKey.generate()
        approver = _register(other, key).json()["approver"]
        other_sid = _sid(other)
        new = "New-" + secrets.token_hex(10)
        r = post(me, "/account/password", current=PASSWORD, new=new, confirm=new)
        assert r.status_code == 303
        assert me.get("/account").status_code == 200  # this session stays
        assert other.get("/account", headers=HTML).status_code == 303  # signed out
        with pytest.raises(ap.KeyInactive):
            _approve_with(app, key, approver, other_sid)
    with client_for(app) as c:
        assert login(c).status_code == 401
        assert login(c, password=new).status_code == 303


# -- two-step sign-in -------------------------------------------------------------------


def test_two_step_login_and_session_fixation(app, client):
    login(client)
    secret, codes = enable_2fa(client)
    page = client.get("/account").text
    assert i18n.EN["account.2fa.on"] in page and "Recovery codes left: 10." in page
    with client_for(app) as c:
        r = login(c, nxt="/receipts")
        assert r.status_code == 303 and r.headers["location"] == "/login/2fa"
        assert "studio_session" not in c.cookies  # no session before the second step
        challenge = [v for v in r.headers.get_list("set-cookie") if v.startswith("studio_2fa=")]
        assert challenge and "path=/login" in challenge[0].lower()
        assert "httponly" in challenge[0].lower() and "samesite=strict" in challenge[0].lower()
        assert c.get("/login/2fa").status_code == 200
        r = second(c, code_at(secret, 1))
        assert r.status_code == 303 and r.headers["location"] == "/receipts"
        assert c.get("/account").status_code == 200
        first = c.cookies.get("studio_session")
        # a second login from a signed-in browser replaces (and revokes) its session
        r = c.post("/login", data={"username": "ola", "password": PASSWORD,
                                   "csrf": csrf(c)}, headers=HTML)
        assert r.headers["location"] == "/login/2fa"
        assert second(c, codes[0]).status_code == 303
        assert c.cookies.get("studio_session") != first
    with client_for(app) as stale:
        stale.cookies.set("studio_session", first)
        assert stale.get("/account", headers=HTML).status_code == 303


def test_reused_totp_and_recovery_codes_are_refused(app, client):
    login(client)
    secret, codes = enable_2fa(client)
    with client_for(app) as c:
        login(c)
        code = code_at(secret, 1)
        assert second(c, code).status_code == 303
        c.post("/logout", data={"csrf": csrf(c)})
        login(c)
        r = second(c, code)  # the same step again
        assert r.status_code == 401 and i18n.EN["login.2fa_failed"] in r.text
        assert second(c, codes[5]).status_code == 303
        c.post("/logout", data={"csrf": csrf(c)})
        app.state.limiter.clear("testclient")
        login(c)
        assert second(c, codes[5]).status_code == 401  # a recovery code twice


def test_second_step_without_a_challenge_or_with_a_forged_one(app, client):
    assert client.get("/login/2fa", headers=HTML).headers["location"] == "/login"
    client.cookies.set("studio_2fa", "forged-token")
    assert second(client, "123456").status_code == 401


def test_second_step_is_rate_limited_and_the_password_does_not_reset_it(app, client):
    login(client)
    secret, _ = enable_2fa(client)
    app.state.limiter.clear("testclient")
    with client_for(app) as c:
        for _ in range(3):  # three wrong passwords
            assert login(c, password="wrong-" + PASSWORD).status_code == 401
        assert login(c).headers["location"] == "/login/2fa"  # 4th attempt, still counted
        assert second(c, wrong_code(secret)).status_code == 401  # 5th
        r = second(c, code_at(secret, 1))  # right code, but the prefix is out of attempts
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert i18n.EN["login.2fa_failed"] in r.text


def test_three_wrong_codes_end_the_challenge(app, client):
    login(client)
    secret, _ = enable_2fa(client)
    app.state.limiter.max_attempts = 100
    with client_for(app) as c:
        login(c)
        wrong = wrong_code(secret)
        for _ in range(accounts.Challenges.MAX_FAILURES):
            assert second(c, wrong).status_code == 401
        assert second(c, code_at(secret, 1)).status_code == 401  # back to the password
        assert c.get("/login/2fa", headers=HTML).headers["location"] == "/login"


def test_disable_2fa_needs_password_and_code(client):
    login(client)
    secret, codes = enable_2fa(client)
    r = post(client, "/account/2fa/disable", password="nope-" + PASSWORD, code=codes[0])
    assert r.status_code == 400 and i18n.EN["err.wrong_password"] in r.text
    r = post(client, "/account/2fa/disable", password=PASSWORD, code="12345678")
    assert r.status_code == 400 and i18n.EN["err.wrong_code"] in r.text
    r = post(client, "/account/2fa/disable", password=PASSWORD, code=code_at(secret, 1))
    assert r.status_code == 303
    assert i18n.EN["account.2fa.off"] in client.get("/account").text


def test_enrolment_can_be_cancelled_and_shows_a_server_side_qr(client):
    login(client)
    assert post(client, "/account/2fa/start", password="wrong-" + PASSWORD).status_code == 400
    post(client, "/account/2fa/start", password=PASSWORD)
    page = client.get("/account").text
    qr = re.search(r'<svg role="img" aria-label="[^"]+"[^>]*class="qr"', page)
    assert qr and "<script" not in page.split('class="qr"')[1].split("</svg>")[0]
    assert post(client, "/account/2fa/cancel").status_code == 303
    assert 'class="qr"' not in client.get("/account").text


# -- the judge demo account -------------------------------------------------------------


def test_judge_still_signs_in_and_cannot_lock_others_out(app, client):
    assert login(client, "judge", JUDGE).status_code == 303
    page = client.get("/account").text
    assert i18n.EN["account.demo_note"] in page and i18n.EN["account.2fa.demo"] in page
    assert 'action="/account/2fa/start"' not in page and 'action="/account/delete"' not in page
    assert 'action="/account/password"' not in page
    r = post(client, "/account/2fa/start", password=JUDGE)
    assert r.status_code == 400 and i18n.EN["err.demo_account"] in r.text
    r = post(client, "/account/delete", confirm="judge", password=JUDGE)
    assert r.status_code == 400 and i18n.EN["err.demo_account"] in r.text
    r = post(client, "/account/password", current=JUDGE, new="Abc-" + JUDGE, confirm="Abc-" + JUDGE)
    assert r.status_code == 400
    assert client.get("/account").status_code == 200
    with client_for(app) as c:
        assert login(c, "judge", JUDGE).status_code == 303


# -- sessions ---------------------------------------------------------------------------


def test_sessions_list_and_revoke(app):
    with client_for(app) as a, client_for(app) as b, client_for(app) as d:
        for c in (a, b, d):
            login(c)
        page = a.get("/account").text
        refs = re.findall(r'name="ref" value="([0-9a-f]{24})"', page)
        assert len(refs) == 2 and "this session" in page
        assert "test client" in page
        assert post(a, "/account/sessions/revoke", ref="0" * 24).status_code == 404
        assert post(a, "/account/sessions/revoke", ref=refs[0]).status_code == 303
        alive = [c.get("/account", headers=HTML).status_code for c in (b, d)]
        assert sorted(alive) == [200, 303]
        assert post(a, "/account/sessions/revoke-others").status_code == 303
        assert [c.get("/account", headers=HTML).status_code for c in (b, d)] == [303, 303]
        assert a.get("/account").status_code == 200


# -- export -----------------------------------------------------------------------------


def test_export_lists_profile_sessions_and_signed_approvals(app, client):
    login(client)
    key = Ed25519PrivateKey.generate()
    approver = _register(client, key).json()["approver"]
    _approve_with(app, key, approver, _sid(client))
    r = client.get("/account/export.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "attachment" in r.headers["content-disposition"]
    data = json.loads(r.text)
    assert data["account"]["username"] == "ola" and data["profile"]["display_name"] == "Ola"
    assert len(data["sessions"]) == 1 and data["sessions"][0]["current"]
    [signed] = data["approvals"]
    assert signed["approver"] == approver and signed["action"] == "reply.post"
    receipt = next(x for x in app.state.ledger.receipts() if x.id == signed["receipt_id"])
    assert receipt.kind == "approval"
    assert "password_hash" not in r.text and "$argon2" not in r.text and "totp_" not in r.text


# -- deletion ---------------------------------------------------------------------------


def test_delete_account_and_the_old_cookie(app, client):
    login(client)
    key = Ed25519PrivateKey.generate()
    approver = _register(client, key).json()["approver"]
    _approve_with(app, key, approver, _sid(client))
    old = client.cookies.get("studio_session")
    r = post(client, "/account/delete", confirm="someone", password=PASSWORD)
    assert r.status_code == 400 and i18n.EN["err.confirmation_mismatch"] in r.text
    r = post(client, "/account/delete", confirm="ola", password="wrong-" + PASSWORD)
    assert r.status_code == 400
    r = post(client, "/account/delete", confirm="ola", password=PASSWORD)
    assert r.status_code == 200 and i18n.EN["account.deleted.title"] in r.text
    with client_for(app) as c:
        c.cookies.set("studio_session", old)
        assert c.get("/account", headers=HTML).status_code == 303
        assert c.get("/api/receipts.jsonl").status_code == 401
        assert login(c).status_code == 401
    # the receipt survives and still verifies; its approver key now leads to a tombstone
    assert any(x.kind == "approval" for x in app.state.ledger.receipts())
    assert approver in client.get("/api/approver-keys").json()
    tomb = app.state.conn.execute(
        "SELECT a.id, a.username, a.status FROM accounts a JOIN account_sessions s "
        "ON s.account_id = a.id JOIN keys k ON k.session_sha256 = s.sid_sha256 "
        "WHERE k.approver = ?", (approver,)).fetchone()
    assert tomb[0].startswith("acct_") and tomb[1] is None and tomb[2] == "deleted"


def test_delete_with_two_factor_needs_the_code(client):
    login(client)
    _, codes = enable_2fa(client)
    page = client.get("/account").text
    assert 'id="del-code"' in page
    r = post(client, "/account/delete", confirm="ola", password=PASSWORD, code="000000")
    assert r.status_code == 400
    r = post(client, "/account/delete", confirm="ola", password=PASSWORD, code=codes[2])
    assert r.status_code == 200


# -- disabled account, CSRF, origin, read-only, enumeration ----------------------------


def test_disabled_account_loses_its_session_and_cannot_sign_in(app, client):
    login(client)
    assert client.get("/account").status_code == 200
    app.state.accounts.set_status("ola", "disabled")  # as `studio account disable ola`
    assert client.get("/account", headers=HTML).status_code == 303
    r = login(client)
    assert r.status_code == 401
    assert i18n.EN["login.failed"] in r.text


def account_posts(app):
    return sorted(r.path for r in app.routes
                  if getattr(r, "methods", None) and "POST" in r.methods
                  and (r.path.startswith("/account/") or r.path == "/login/2fa"))


def test_every_account_form_needs_the_csrf_field(app, client):
    login(client)
    paths = account_posts(app)
    assert len(paths) == 11
    for path in paths:
        assert client.post(path, data={"password": PASSWORD}).status_code == 403, path
        r = client.post(path, data={"password": PASSWORD, "csrf": "x" * 43})
        assert r.status_code == 403, path
    assert client.get("/account").status_code == 200  # nothing was changed


def test_every_account_form_is_same_origin_only(app, client):
    login(client)
    for path in account_posts(app):
        r = client.post(path, data={"csrf": csrf(client)},
                        headers={"origin": "https://evil.example"})
        assert r.status_code == 403, path
        r = client.post(path, data={"csrf": csrf(client)},
                        headers={"sec-fetch-site": "cross-site"})
        assert r.status_code == 403, path


def test_account_forms_need_a_session(app):
    with client_for(app) as c:
        for path in account_posts(app):
            if path == "/login/2fa":
                continue
            assert c.post(path, data={"csrf": csrf(c)}).status_code == 401, path


def test_read_only_refuses_account_writes(tmp_path):
    app = make_app(tmp_path, STUDIO_READ_ONLY="1")
    app.state.accounts.create("ola", PASSWORD)
    with client_for(app) as c:
        login(c)
        assert post(c, "/account/profile", display_name="x").status_code == 503


def test_login_failure_is_the_same_for_unknown_disabled_and_wrong(app):
    app.state.accounts.create("tomasz", PASSWORD)
    app.state.accounts.set_status("tomasz", "disabled")
    bodies = []
    for user, password in (("nobody", PASSWORD), ("tomasz", PASSWORD), ("ola", "nope")):
        app.state.limiter.clear("testclient")  # every test client shares one address
        with client_for(app) as c:
            r = login(c, user, password)
            assert r.status_code == 401
            bodies.append(re.search(r'<p class="status error"[^>]*>(.*?)</p>', r.text).group(1))
            j = c.post("/login", json={"username": user, "password": password})
            assert j.json() == {"error": "invalid_credentials"}
    assert len(set(bodies)) == 1


def test_password_forms_count_against_the_login_limiter(client, app):
    login(client)
    for _ in range(app.state.limiter.max_attempts):
        post(client, "/account/password", current="wrong-" + PASSWORD, new="x", confirm="x")
    r = post(client, "/account/password", current=PASSWORD, new="Abc-" + PASSWORD,
             confirm="Abc-" + PASSWORD)
    assert r.status_code == 429 and "retry-after" in r.headers


def test_login_works_without_judge_password_when_an_account_exists(tmp_path):
    app = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v")}))
    with client_for(app) as c:
        assert login(c).status_code == 403  # no account yet: sign-in stays disabled
    app.state.accounts.create("ola", PASSWORD)
    with client_for(app) as c:
        assert login(c).status_code == 303
        assert c.get("/account").status_code == 200


def test_sessions_do_not_survive_a_restart(tmp_path):
    secret = secrets.token_urlsafe(32)  # the same signing secret: only the revocation acts
    first = make_app(tmp_path, SESSION_SECRET=secret)
    first.state.accounts.create("ola", PASSWORD)
    with client_for(first) as c:
        login(c)
        cookie = c.cookies.get("studio_session")
    second_app = make_app(tmp_path, SESSION_SECRET=secret)
    with client_for(second_app) as c:
        c.cookies.set("studio_session", cookie)
        assert c.get("/account", headers=HTML).status_code == 303


def test_two_step_login_and_account_forms_work_in_the_sandbox(tmp_path, monkeypatch):
    from studio import sandbox

    cfg = sandbox.SandboxConfig(enabled=True, reset_thread=False)
    monkeypatch.setattr(sandbox.SandboxConfig, "load", classmethod(lambda cls, env=None: cfg))
    app = make_app(tmp_path, STUDIO_SANDBOX="1")
    app.state.accounts.create("ola", PASSWORD)
    with client_for(app) as c:
        login(c)
        secret, _ = enable_2fa(c)
        assert post(c, "/account/profile", display_name="Ola", advertiser_type="person"
                    ).status_code == 303
        c.post("/logout", data={"csrf": csrf(c)})
        assert login(c).headers["location"] == "/login/2fa"
        assert second(c, code_at(secret, 1)).status_code == 303  # not "login_required"
    with client_for(app) as anon:
        assert anon.post("/account/profile", data={"csrf": csrf(anon)}).status_code == 401


def test_a_password_change_ends_pending_second_steps(app, client):
    login(client)
    secret, _ = enable_2fa(client)
    with client_for(app) as c:
        assert login(c).headers["location"] == "/login/2fa"
        new = "New-" + secrets.token_hex(10)
        assert post(client, "/account/password", current=PASSWORD, new=new,
                    confirm=new).status_code == 303
        assert second(c, code_at(secret, 1)).status_code == 401


def test_an_operator_password_reset_kills_an_open_challenge(app, client):
    login(client)
    secret, _ = enable_2fa(client)
    with client_for(app) as c:
        assert login(c).headers["location"] == "/login/2fa"
        # `studio account reset-password` runs in another process: no in-memory drop here
        app.state.accounts.reset_password("ola", "Fresh-" + secrets.token_hex(10))
        assert second(c, code_at(secret, 1)).status_code == 401


def test_new_challenges_after_too_many_wrong_codes_are_still_refused(app, client):
    login(client)
    secret, _ = enable_2fa(client)
    app.state.limiter.max_attempts = 1000  # the per-account count, not the per-IP one
    bad = wrong_code(secret)
    for _ in range(accounts.FREE_CODE_FAILURES + 1):
        with client_for(app) as c:  # a fresh challenge every time
            assert login(c).headers["location"] == "/login/2fa"
            assert second(c, bad).status_code == 401
    assert app.state.accounts.code_lock_remaining(app.state.accounts.by_username("ola").id) > 0
    with client_for(app) as c:
        assert login(c).headers["location"] == "/login/2fa"
        r = second(c, code_at(secret, 1))
        assert r.status_code == 401 and i18n.EN["login.2fa_failed"] in r.text  # generic
