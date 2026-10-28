"""EN/PL: one catalogue, same keys and placeholders, every key used exists, pages follow
``?lang=`` and the ``lang`` cookie."""

import json
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.settings import Settings
from studio.ui import i18n

UI = Path(__file__).resolve().parents[1] / "studio" / "ui"
PAGES = ("/", "/studio", "/inbox", "/leads", "/report", "/receipts", "/help", "/login",
         "/style/cast")


@pytest.fixture
def client(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v"),
                                  "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
                                  "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(a, base_url="https://testserver") as c:
        yield c


def test_both_catalogues_have_the_same_keys_and_placeholders():
    assert set(i18n.EN) == set(i18n.PL)
    for key in i18n.EN:
        assert i18n.placeholders(i18n.EN[key]) == i18n.placeholders(i18n.PL[key]), key
        assert i18n.PL[key].strip(), key


def test_every_key_used_in_templates_and_scripts_exists():
    used = set()
    for tpl in (UI / "templates").glob("*.html"):
        used |= set(re.findall(r"""\bt\(\s*["']([a-z0-9_.]+)["']\s*[,)]""", tpl.read_text()))
    assert used and used <= set(i18n.EN), sorted(used - set(i18n.EN))
    js_keys = {k[3:] for k in i18n.EN if k.startswith("js.")}
    for script in (UI / "static").glob("*.js"):
        if script.name == "i18n.js":  # the helper itself (its comment shows the syntax)
            continue
        for key in re.findall(r"""\bT\(\s*["']([a-z0-9_]+)["']""", script.read_text()):
            assert key in js_keys, (script.name, key)


def test_no_english_ui_sentences_left_in_scripts():
    for script in (UI / "static").glob("*.js"):
        for literal in re.findall(r'"([A-Z][a-z]+ [a-z][^"]{6,})"', script.read_text()):
            pytest.fail(f"{script.name}: untranslated {literal!r}")


@pytest.mark.parametrize("path", PAGES)
def test_pages_render_in_polish_with_html_lang(client, path):
    html = client.get(path + "?lang=pl").text
    assert '<html lang="pl">' in html
    assert i18n.PL["nav.receipts"] in html and i18n.EN["tagline"] not in html
    data = re.search(r'<script type="application/json" id="i18n">(.*?)</script>', html, re.S)
    assert json.loads(data.group(1))["copied"] == i18n.PL["js.copied"]
    assert 'href="?lang=en"' in html  # the switch back: query-only, never another site


def test_lang_query_sets_a_cookie_that_later_pages_follow(client):
    r = client.get("/?lang=pl")
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("lang=pl") and "samesite=lax" in cookie
    assert '<html lang="pl">' in client.get("/receipts").text
    assert '<html lang="en">' in client.get("/receipts?lang=en").text
    assert '<html lang="en">' in client.get("/receipts").text


def test_unknown_language_falls_back_to_english(client):
    r = client.get("/?lang=xx")
    assert '<html lang="en">' in r.text and "set-cookie" not in {
        k for k, v in r.headers.items() if v.startswith("lang=")}


def test_polish_login_error_is_polish(client):
    client.get("/login?lang=pl")
    r = client.post("/login", data={"username": "judge", "password": "nope",
                                    "csrf": client.cookies.get("studio_csrf")},
                    headers={"accept": "text/html"})
    assert r.status_code == 401 and i18n.PL["login.failed"] in r.text
