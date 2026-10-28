"""The login gate: STUDIO_REQUIRE_LOGIN, the /login page, open redirects, CSRF, lockout."""

import re
import secrets
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import pytest
from fastapi.testclient import TestClient

from studio import auth
from studio.app import create_app
from studio.settings import ConfigError, Settings
from tests.forms import csrf

PASSWORD = "Jd7-" + secrets.token_urlsafe(24)
HTML = {"accept": "text/html,application/xhtml+xml"}


def settings(tmp_path, **extra):
    env = {"STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": PASSWORD,
           "SESSION_SECRET": secrets.token_urlsafe(32), "STUDIO_REQUIRE_LOGIN": "1"}
    env.update(extra)
    return Settings.load(env)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(settings(tmp_path)), base_url="https://testserver",
                    follow_redirects=False) as c:
        yield c


def form_login(c, password=PASSWORD, nxt="", token=None):
    data = {"username": "judge", "password": password, "next": nxt,
            "csrf": csrf(c) if token is None else token}
    return c.post("/login", data=data, headers=HTML)


# -- the requirement --------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/studio", "/inbox", "/leads", "/report", "/receipts",
                                  "/help", "/pl/help", "/style/cast", "/studio?run=run_x"])
def test_pages_redirect_to_login_with_next(client, path):
    r = client.get(path, headers=HTML)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")
    assert auth.safe_next(path) in r.headers["location"].replace("%3F", "?").replace("%3D", "=")


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/receipts.jsonl"), ("GET", "/api/comments"), ("GET", "/api/leads"),
    ("GET", "/api/runs/run_x"), ("GET", "/assets/run_x/cutout.png"), ("POST", "/api/runs"),
])
def test_api_without_session_is_401_json(client, method, path):
    r = client.request(method, path, json={})
    assert r.status_code == 401 and r.json() == {"error": "login_required"}


@pytest.mark.parametrize("path", ["/login", "/healthz", "/api/ledger-key",
                                  "/api/ledger-manifest", "/api/approver-keys",
                                  "/static/tokens.css"])
def test_public_paths_stay_open(client, path):
    assert client.get(path).status_code == 200


def test_verify_cli_endpoints_are_exactly_the_public_api():
    cli = (Path(__file__).resolve().parents[1] / "studio" / "cli.py").read_text()
    fetched = set(re.findall(r'_fetch\(args\.server, "(/api/[a-z-]+)"\)', cli))
    assert fetched == {p for p in auth.PUBLIC_PATHS if p.startswith("/api/")}


def test_session_opens_everything(client):
    assert form_login(client).status_code == 303
    for path in ("/", "/studio", "/receipts", "/help", "/api/receipts.jsonl"):
        assert client.get(path).status_code == 200, path


def test_require_login_needs_a_password_and_defaults_on_in_the_sandbox(tmp_path):
    with pytest.raises(ConfigError):
        Settings.load({"STUDIO_DATA_DIR": str(tmp_path), "STUDIO_REQUIRE_LOGIN": "1"})
    s = Settings.load({"STUDIO_DATA_DIR": str(tmp_path), "STUDIO_SANDBOX": "1",
                       "JUDGE_PASSWORD": PASSWORD, "SESSION_SECRET": secrets.token_urlsafe(32)})
    assert s.require_login
    assert not Settings.load({"STUDIO_DATA_DIR": str(tmp_path)}).require_login


# -- the login page ---------------------------------------------------------------------


def test_login_page_is_a_form_with_csrf_and_people(client):
    html = client.get("/login?next=/receipts").text
    assert '<form method="post" action="/login"' in html
    assert f'name="csrf" value="{client.cookies.get("studio_csrf")}"' in html
    assert 'name="next" value="/receipts"' in html
    assert html.count('<svg class="il') == 1 and "c-blob" not in html


def test_success_redirects_to_next_and_sets_a_strict_cookie(client):
    r = form_login(client, nxt="/receipts?run=run_1")
    assert r.status_code == 303 and r.headers["location"] == "/receipts?run=run_1"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie


@pytest.mark.parametrize("nxt", ["//evil.example/", "https://evil.example/", "/\\evil.example",
                                 "\\\\evil", "javascript:alert(1)", "/%0d%0aSet-Cookie:x",
                                 "/login?next=//evil", " /studio", "http:/evil", "/\x00x"])
def test_no_open_redirect(client, nxt):
    r = form_login(client, nxt=nxt)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/") and not location.startswith(("//", "/\\"))
    assert "evil" not in location or location.startswith("/%")


def test_safe_next_unit():
    assert auth.safe_next("/studio?run=a") == "/studio?run=a"
    for bad in (None, "", "studio", "//x", "/\\x", "https://x/", "/login", "/a\nb", "/" * 600):
        assert auth.safe_next(bad) == "/"


def test_wrong_password_gives_one_generic_message(client):
    r = form_login(client, password="nope")
    assert r.status_code == 401
    assert "Sign-in failed. Check the user name and password" in r.text
    alert = re.search(r'<p class="status error"[^>]*>(.*?)</p>', r.text, re.S).group(1)
    assert "invalid_credentials" not in alert and PASSWORD not in r.text


def test_lockout_after_repeated_failures_with_retry_after(client):
    codes = [form_login(client, password="nope").status_code for _ in range(5)]
    assert codes == [401] * 5
    r = form_login(client)  # even the right password waits
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "Sign-in failed" in r.text


# -- CSRF and fetch metadata ------------------------------------------------------------


def test_login_form_without_or_with_a_wrong_token_is_refused(client):
    client.get("/login")
    assert form_login(client, token="").status_code == 403
    assert form_login(client, token="x" * 43).status_code == 403
    assert form_login(client).status_code == 303


def test_origin_null_from_a_same_origin_form_signs_in(client):
    """Chrome sends ``Origin: null`` on a form POST under a strict referrer policy."""
    data = {"username": "judge", "password": "nope", "next": "", "csrf": csrf(client)}
    null = {**HTML, "origin": "null", "sec-fetch-site": "same-origin"}
    assert client.post("/login", data=data, headers=null).status_code == 401  # retry allowed
    assert client.post("/login", data={**data, "password": PASSWORD},
                       headers=null).status_code == 303
    assert client.post("/login", data=data, headers={**HTML, "origin": "null"}).status_code == 403


def test_referrer_policy_keeps_origin_on_same_site_posts(client):
    assert client.get("/login").headers["referrer-policy"] == "same-origin"


def test_csrf_cookie_is_httponly_and_strict(client):
    r = client.get("/login")
    cookie = [v for k, v in r.headers.multi_items() if k == "set-cookie"
              and v.startswith("studio_csrf=")][0].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_form_typed_api_posts_need_the_header_token(client):
    form_login(client)
    body = {"content-type": "text/plain"}
    assert client.post("/api/runs", content=b'{"brief": "x"}', headers=body).status_code == 403
    r = client.post("/api/runs", content=b'{"brief": "x"}',
                    headers={**body, "x-csrf-token": csrf(client)})
    assert r.status_code != 403


def test_cross_site_fetch_metadata_is_refused(client):
    form_login(client)
    r = client.post("/api/runs", json={"brief": "x"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and r.json() == {"error": "cross_site"}


def test_logout_form_revokes_and_returns_to_login(client):
    form_login(client)
    html = client.get("/studio").text
    assert 'action="/logout"' in html
    r = client.post("/logout", data={"csrf": csrf(client)}, headers=HTML)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/studio", headers=HTML).status_code == 303
    assert client.post("/logout", data={"csrf": "forged"}).status_code == 403


@pytest.mark.parametrize("path", ["//evil.example", "//evil.example/x", "/\\evil.example",
                                  "/%2F%2Fevil.example", "/%5Cevil.example", "/\\/evil.example"])
@pytest.mark.parametrize("logged_in", [False, True])
def test_request_path_is_never_echoed_as_an_offsite_link(tmp_path, path, logged_in):
    s = settings(tmp_path, STUDIO_REQUIRE_LOGIN="0")
    with TestClient(create_app(s), base_url="https://testserver", follow_redirects=False) as c:
        if logged_in:
            form_login(c)
        r = c.get("https://testserver" + path, headers=HTML)
        hrefs = re.findall(r'(?:href|action)="([^"]*)"', r.text)
        for href in hrefs:
            assert not href.startswith(("//", "/\\", "\\")), href
            assert urlsplit(urljoin("https://testserver/a/b", href)).netloc == "testserver"
            if "?next=" in href:  # where a login would send the browser
                target = auth.safe_next(unquote(href.split("?next=", 1)[1]))
                assert urlsplit(urljoin("https://testserver/", target)).netloc == "testserver"
        assert "evil.example" not in r.headers.get("location", "")


def test_query_only_language_links_keep_other_parameters():
    assert auth.query_with("run=a&lang=en", lang="pl") == "?run=a&lang=pl"
    assert auth.query_with("x=%2F%2Fevil", lang="pl") == "?x=%2F%2Fevil&lang=pl"
    assert auth.page_path("//evil") == "/" and auth.page_path("/a\\b") == "/"
