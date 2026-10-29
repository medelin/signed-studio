"""EN/PL: one catalogue, same keys and placeholders, every key used exists, pages follow
``?lang=`` and the ``lang`` cookie."""

import html as htmllib
import json
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.settings import Settings
from studio.ui import help_texts, i18n
from tests.test_login_gate import HTML, PASSWORD, form_login

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
    assert sorted(set(i18n.EN) - set(i18n.PL)) == [], "missing in PL"
    assert sorted(set(i18n.PL) - set(i18n.EN)) == [], "missing in EN"
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
    assert '<html lang="pl" ' in html
    assert i18n.PL["nav.receipts"] in html and i18n.EN["tagline"] not in html
    data = re.search(r'<script type="application/json" id="i18n">(.*?)</script>', html, re.S)
    assert json.loads(data.group(1))["copied"] == i18n.PL["js.copied"]
    assert 'href="?lang=en"' in html  # the switch back: query-only, never another site


def test_lang_query_sets_a_cookie_that_later_pages_follow(client):
    r = client.get("/?lang=pl")
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("lang=pl") and "samesite=lax" in cookie
    assert '<html lang="pl" ' in client.get("/receipts").text
    assert '<html lang="en" ' in client.get("/receipts?lang=en").text
    assert '<html lang="en" ' in client.get("/receipts").text


def test_unknown_language_falls_back_to_english(client):
    r = client.get("/?lang=xx")
    assert '<html lang="en" ' in r.text and "set-cookie" not in {
        k for k, v in r.headers.items() if v.startswith("lang=")}


def test_polish_login_error_is_polish(client):
    client.get("/login?lang=pl")
    r = client.post("/login", data={"username": "judge", "password": "nope",
                                    "csrf": client.cookies.get("studio_csrf")},
                    headers={"accept": "text/html"})
    assert r.status_code == 401 and i18n.PL["login.failed"] in r.text


def test_every_api_error_code_has_a_sentence_in_both_languages():
    codes: set[str] = set()
    for src in (UI.parent).rglob("*.py"):
        text = src.read_text()
        codes |= set(re.findall(r'"error": "([a-z_]+)"', text))
        codes |= set(re.findall(r'\bfail\("([a-z_]+)"', text))
        codes |= set(re.findall(r'^\s+code(?:, status)? = "([a-z_]+)"', text, re.M))
        codes |= set(re.findall(r'InboxError\([^)]*?"(inbox_[a-z_]+)"', text, re.S))
    assert len(codes) > 30
    for lang in (i18n.EN, i18n.PL):
        assert sorted(c for c in codes if f"err.{c}" not in lang) == []


def test_scripts_do_not_throw_or_print_hard_coded_sentences():
    for script in (UI / "static").glob("*.js"):
        if script.name == "i18n.js":
            continue
        source = script.read_text()
        assert not re.findall(r'new Error\("', source), script.name
        assert not re.findall(r'textContent = "[A-Za-z]{3,}', source), script.name


def test_theme_label_says_auto_but_the_value_stays_system(client):
    en = client.get("/?lang=en").text
    pl = client.get("/?lang=pl").text
    assert '<option value="system" selected>auto (system)</option>' in en
    assert '<option value="system" selected>auto (jak w systemie)</option>' in pl
    assert i18n.EN["theme.system"] == "auto (system)"
    assert client.get("/?theme=system").cookies.get("theme") in (None, "system")


def test_help_entries_exist_in_both_languages():
    for entry in help_texts.ENTRIES:
        for lang in help_texts.LANGS:
            assert entry.q[lang].strip() and entry.a[lang].strip(), (entry.anchor, lang)
        assert set(entry.q) == set(entry.a) == set(help_texts.LANGS), entry.anchor


# -- every main page, rendered in both languages ------------------------------------------

RENDERED = ("/", "/studio", "/inbox", "/leads", "/report", "/receipts", "/help", "/login",
            "/style/cast")
META = {"/": "index", "/style/cast": "cast"}
# A few strings that must be Polish on the Polish render (and not on the English one).
KNOWN_PL = {
    "/": "Najnowsze pokwitowania",
    "/studio": "Przygotuj Brief v1",
    "/inbox": "Skrzynka",
    "/leads": "Leady",
    "/report": "Raport",
    "/receipts": "Pokwitowania",
    "/help": "Pomoc",
    "/login": "Nazwa użytkownika",
    "/style/cast": "Obsada",
}


@pytest.fixture
def judge(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "j"),
                                  "JUDGE_PASSWORD": PASSWORD,
                                  "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(a, base_url="https://testserver", follow_redirects=False) as c:
        assert form_login(c).status_code == 303
        c.post("/api/comments/simulate", json={"text": "ile kosztuje?"})
        yield c


def visible(markup: str) -> str:
    """The page's text and attribute values, without scripts, styles and tags' names."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S)
    return htmllib.unescape(body)


def mentions_key(markup: str, key: str) -> bool:
    return re.search(r"(?<![\w.])" + re.escape(key) + r"(?![\w])", visible(markup)) is not None


def comparable(key: str) -> bool:
    return (not key.startswith(("js.", "enum.", "err.", "meta.")) and not key.endswith("_html")
            and "{" not in i18n.EN[key] and len(i18n.EN[key]) >= 12
            and i18n.EN[key] != i18n.PL[key])


@pytest.mark.parametrize("path", RENDERED)
@pytest.mark.parametrize("lang", i18n.LANGS)
@pytest.mark.parametrize("signed_in", [False, True])
def test_main_pages_have_no_leftover_keys_or_foreign_text(client, judge, path, lang, signed_in):
    c = judge if signed_in else client
    if signed_in and path == "/login":
        pytest.skip("a signed-in session is redirected away from the login page")
    r = c.get(path, params={"lang": lang}, headers=HTML)
    assert r.status_code == 200, path
    page = r.text
    assert f'<html lang="{lang}" ' in page
    meta = i18n.CATALOGS[lang]["meta." + META.get(path, path.strip("/"))]
    assert f'<meta name="description" content="{htmllib.escape(meta)}">' in page
    text = visible(page)
    leftovers = [k for k in i18n.EN if "." in k and mentions_key(page, k)]
    assert leftovers == [], leftovers
    assert "{{" not in text and "{%" not in text
    other = "pl" if lang == "en" else "en"
    foreign = [k for k in i18n.EN if comparable(k)
               and i18n.CATALOGS[other][k] in text and i18n.CATALOGS[lang][k] not in text]
    assert foreign == [], foreign
    data = json.loads(re.search(r'id="i18n">(.*?)</script>', page, re.S).group(1))
    assert data["copied"] == i18n.CATALOGS[lang]["js.copied"]
    assert data["enum.run.done"] == i18n.CATALOGS[lang]["enum.run.done"]
    assert (KNOWN_PL[path] in text) is (lang == "pl")


def test_polish_help_page_and_prefix_route_are_polish(client):
    for url in ("/pl/help", "/help?lang=pl"):
        text = visible(client.get(url).text)
        assert "Co Muse Studio Lite dla mnie robi?" in text and "What does Muse Studio" not in text
    assert "What does Muse Studio Lite do for me?" in visible(client.get("/help?lang=en").text)


def test_polish_404_and_labels_around_demo_data(client, judge):
    nf = client.get("/no-such-page?lang=pl", headers=HTML)
    assert nf.status_code == 404 and "Nie ma takiej strony" in nf.text
    assert i18n.PL["meta.nf"] in nf.text
    inbox = visible(judge.get("/inbox?lang=pl").text)
    assert "ile kosztuje?" in inbox  # demo data stays as it is
    assert "· nowy" in inbox and "· new" not in inbox


def test_every_pixel_check_code_has_a_line_in_both_languages():
    src = "".join((UI.parent / "meta" / name).read_text()
                  for name in ("pixel.py", "graph.py", "mock.py"))
    codes = set(re.findall(r"""entry\(\s*["']([a-z0-9_]+)["']""", src))
    assert {"pixel_not_found", "pixel_not_in_account", "landing_not_found"} <= codes
    for code in codes:
        assert "js.pixel_c_" + code in i18n.EN and "js.pixel_c_" + code in i18n.PL, code
    for code in ("pixel_check_failed", "pixel_check_missing", "pixel_check_mismatch"):
        assert "err." + code in i18n.EN, code


def test_the_approval_view_has_the_pixel_check(client):
    html = client.get("/studio?lang=pl").text
    assert 'id="ap-pixel"' in html and 'id="ap-pixel-badge"' in html
    assert i18n.PL["studio.ap.pixel"] in html
    script = (UI / "static" / "app.js").read_text()
    assert "showPixel(px)" in script and 'T("pixel_head"' in script
