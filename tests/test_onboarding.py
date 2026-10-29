"""Onboarding (``/start``, ``/pl/start``): public before sign-in, six illustrated steps in
both languages, CSP-clean, accessible basics, entry points on ``/``, ``/login``, the nav and
a one-time banner on ``/studio``; the drawings follow the cast rules."""

import html as htmllib
import json
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import auth
from studio.app import create_app
from studio.settings import Settings
from studio.ui import i18n, onboarding_art
from studio.ui.i18n_onboarding import EN_ONBOARDING, PL_ONBOARDING, STEPS
from tests.test_login_gate import HTML, PASSWORD, form_login

STATIC = Path(__file__).resolve().parents[1] / "studio" / "ui" / "static"
PAGES = ("/start", "/pl/start", "/start?lang=pl")


def _app(tmp_path, require_login: bool):
    env = {"STUDIO_DATA_DIR": str(tmp_path / "v"), "JUDGE_PASSWORD": PASSWORD,
           "SESSION_SECRET": secrets.token_urlsafe(32)}
    if require_login:
        env["STUDIO_REQUIRE_LOGIN"] = "1"
    return create_app(Settings.load(env))


@pytest.fixture
def gated(tmp_path):
    with TestClient(_app(tmp_path, True), base_url="https://testserver",
                    follow_redirects=False) as c:
        yield c


@pytest.fixture
def client(tmp_path):
    with TestClient(_app(tmp_path, False), base_url="https://testserver",
                    follow_redirects=False) as c:
        yield c


def _signed_in(c):
    r = form_login(c, nxt="/studio")
    assert r.status_code == 303, r.text
    return c


# -- public, narrowly -------------------------------------------------------------------


def test_start_is_public_while_the_studio_needs_a_login(gated):
    for path in PAGES:
        r = gated.get(path, follow_redirects=False)
        assert r.status_code == 200, path
        assert "text/html" in r.headers["content-type"]
    assert gated.get("/studio", follow_redirects=False).status_code == 303
    for path in ("/start/", "/startx", "/pl/start/x", "/start/1"):
        assert gated.get(path, follow_redirects=False).status_code in (303, 401, 404), path
    assert {"/start", "/pl/start"} <= auth.PUBLIC_PATHS
    assert not any(p.startswith("/start") for p in auth.PUBLIC_PREFIXES)


def test_static_assets_load_without_a_login(gated):
    for name in ("onboarding.css", "onboarding.js"):
        assert gated.get(f"/static/{name}", follow_redirects=False).status_code == 200


# -- content and languages --------------------------------------------------------------


def test_six_steps_each_with_heading_drawing_text_and_hint(client):
    html = client.get("/start?lang=en").text
    steps = re.findall(r'<li class="ob-step" id="ob-step-(\d)"(.*?)</li>', html, re.S)
    assert [int(n) for n, _ in steps] == list(range(1, STEPS + 1)) and STEPS == 6
    for n, raw in steps:
        body = htmllib.unescape(raw)
        assert f'<h2 id="ob-step-{n}-h">{i18n.EN[f"start.s{n}.h"]}</h2>' in body
        svg = re.search(r"<svg[^>]*>", body).group(0)
        assert 'role="img"' in svg and f'aria-label="{i18n.EN[f"start.s{n}.art"]}"' in svg
        assert i18n.EN[f"start.s{n}.p"] in body
        assert 'class="ob-see"' in body and i18n.EN["start.see"] in body


def test_the_steps_name_the_real_flow():
    words = " ".join(EN_ONBOARDING[f"start.s{n}.{k}"] for n in range(1, 7) for k in ("h", "p"))
    for needle in ("[FACT]", "Muse Spark", "Muse Image", "SAM 3.1", "Muse Spark Open",
                   "BLOCK", "PAUSED", "your own Meta ad account", "landing page",
                   "lead form", "inbox", "receipt"):
        assert needle in words, needle


def test_texts_stay_short():
    for n in range(1, 7):
        for cat in (EN_ONBOARDING, PL_ONBOARDING):
            assert len(cat[f"start.s{n}.h"]) <= 40, (n, cat[f"start.s{n}.h"])
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", cat[f"start.s{n}.p"]) if s]
            assert len(sentences) <= 2, (n, sentences)
    assert "hash chain" not in " ".join(EN_ONBOARDING.values()).lower()


def test_english_and_polish_have_the_same_keys():
    assert set(EN_ONBOARDING) == set(PL_ONBOARDING)
    for key, value in EN_ONBOARDING.items():
        assert i18n.placeholders(value) == i18n.placeholders(PL_ONBOARDING[key]), key
        assert i18n.EN[key] == value and i18n.PL[key] == PL_ONBOARDING[key]


@pytest.mark.parametrize("path", ("/pl/start", "/start?lang=pl"))
def test_polish_page_is_polish(client, path):
    html = client.get(path).text
    assert '<html lang="pl"' in html
    assert PL_ONBOARDING["start.title"] in html and PL_ONBOARDING["start.s5.h"] in html
    assert EN_ONBOARDING["start.s5.h"] not in html
    assert 'href="/pl/try"' in html and 'hreflang="en"' in html


def test_ctas_at_the_end(client):
    html = client.get("/start?lang=en").text
    end = html[html.index('class="ob-end'):]
    assert 'href="/try"' in end and EN_ONBOARDING["start.cta_try"] in end
    assert 'href="/login?next=/studio"' in end and EN_ONBOARDING["start.cta_sign_in"] in end
    _signed_in(client)
    end = client.get("/start?lang=en").text.split('class="ob-end', 1)[1]
    assert 'href="/studio"' in end and EN_ONBOARDING["start.cta_studio"] in end
    assert "/login?next" not in end


# -- CSP and accessibility ---------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
def test_csp_clean_no_inline_script(client, path):
    html = client.get(path).text
    assert ' style="' not in html and "<style" not in html
    assert not re.search(r"\son[a-z]+=", html)
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
        if 'type="application/json"' in attrs:
            json.loads(body)
            continue
        assert "src=" in attrs and not body.strip()
    assert '<script src="/static/onboarding.js" defer></script>' in html
    assert 'href="/static/onboarding.css"' in html


def test_controls_are_labelled_buttons(client):
    html = client.get("/start?lang=en").text
    controls = html[html.index('class="ob-controls"'):html.index('class="ob-end')]
    assert 'role="group"' in controls and 'aria-label="Animation controls"' in controls
    buttons = re.findall(r"<button([^>]*)>(.*?)</button>", controls, re.S)
    assert len(buttons) == 3 + STEPS
    for attrs, inner in buttons:
        assert 'type="button"' in attrs
        visible = re.sub(r"<[^>]+>", "", inner).strip()
        assert "aria-label=" in attrs or (visible and not visible.isspace()), attrs
        if 'aria-hidden="true"' in inner:  # arrow glyph only: the label carries the name
            assert "aria-label=" in attrs
    assert 'aria-label="Step 3: Agents plan, write and draw"' in controls
    assert 'aria-live="polite"' in controls


def test_without_js_every_step_is_shown_and_controls_hidden(client):
    html = client.get("/start").text
    assert re.search(r'<div class="ob-controls"[^>]* hidden>', html)
    assert "is-active" not in html and 'class="ob live' not in html  # the script adds these


def test_reduced_motion_and_keyboard_swipe_in_the_script():
    js = (STATIC / "onboarding.js").read_text()
    assert "prefers-reduced-motion: reduce" in js
    for needle in ("ArrowLeft", "ArrowRight", "touchstart", "touchend", "animationend",
                   "localStorage", "visibilitychange"):
        assert needle in js, needle
    assert js.count("try {") >= 2  # every storage access is guarded
    for key in re.findall(r"""\bT\(\s*(?:on \? )?["']([a-z_]+)["']""", js):
        assert "js." + key in i18n.EN
    css = (STATIC / "onboarding.css").read_text()
    motion = css.split("@media (prefers-reduced-motion: no-preference)")
    assert len(motion) == 2 and "animation:" not in motion[0]
    assert "@media (forced-colors: active)" in css


# -- the drawings --------------------------------------------------------------------------


@pytest.mark.parametrize("n", range(1, STEPS + 1))
def test_drawings_follow_the_cast_rules(n):
    svg = str(onboarding_art.art(n, "label"))
    assert len(svg.encode()) <= 8 * 1024
    for bad in (" style=", "<style", " id=", "<defs", "Gradient", "<filter", "<image",
                "href=", "<script"):
        assert bad not in svg, (n, bad)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(|oklch\(", svg)
    assert not re.search(r'\b(fill|stroke)="(?!none)', svg)
    for text in re.findall(r"<text[^>]*>([^<]*)</text>", svg):
        assert not any(ch.isalpha() for ch in text), text
    assert 'role="img"' in svg and 'aria-label="label"' in svg


def test_every_class_in_the_drawings_is_styled():
    css = (STATIC / "illustrations.css").read_text() + (STATIC / "onboarding.css").read_text()
    used = set()
    for n in range(1, STEPS + 1):
        for group in re.findall(r'class="([^"]+)"', str(onboarding_art.art(n, "x"))):
            used |= set(group.split())
    used -= {"il", "rec", "on", "node"}
    missing = sorted(c for c in used if f".{c}" not in css)
    assert not missing, missing


# -- entry points ----------------------------------------------------------------------------


def test_links_on_the_landing_page_and_login(client, gated):
    for c, path in ((client, "/"), (gated, "/login")):
        html = c.get(path + "?lang=en").text
        assert f'<a class="ob-button" href="/start">{EN_ONBOARDING["start.link"]}</a>' in html
    html = client.get("/?lang=pl").text
    assert f'<a class="ob-button" href="/pl/start">{PL_ONBOARDING["start.link"]}</a>' in html


def test_nav_link_for_signed_in_users_only(client):
    nav = re.compile(r'<nav class="nav".*?</nav>', re.S)
    assert 'href="/start"' not in nav.search(client.get("/?lang=en").text).group(0)
    _signed_in(client)
    assert 'href="/start"' in nav.search(client.get("/?lang=en").text).group(0)
    assert '<a href="/start" aria-current="page">' in client.get("/start?lang=en").text


def test_studio_banner_for_signed_in_accounts(client):
    assert 'id="ob-banner"' not in client.get("/studio", headers=HTML).text  # signed out
    _signed_in(client)
    html = client.get("/studio?lang=en").text
    m = re.search(r'<aside class="ob-banner" id="ob-banner" data-key="([0-9a-f]{16})"[^>]*hidden>',
                  html)
    assert m, "banner rendered hidden, the script shows it once"
    assert 'href="/start"' in html[m.start():] and 'id="ob-banner-close"' in html
    assert '<script src="/static/onboarding.js" defer></script>' in html
    # the same account gets the same key; other pages carry no banner
    assert m.group(1) in client.get("/studio").text
    assert 'id="ob-banner"' not in client.get("/inbox").text
