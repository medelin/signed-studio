"""Design tokens and components: every page loads them; templates stay CSP-clean."""

import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.app import CSP, create_app
from studio.settings import Settings

UI = Path(__file__).resolve().parents[1] / "studio" / "ui"
PAGES = ("/", "/studio", "/inbox", "/leads", "/report", "/receipts", "/help", "/pl/help",
         "/style/cast")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    a = create_app(Settings.load({
        "STUDIO_DATA_DIR": str(tmp_path_factory.mktemp("t")),
        "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
        "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(a, base_url="https://testserver") as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_every_page_loads_the_tokens_and_is_csp_clean(client, path):
    r = client.get(path)
    assert r.status_code == 200
    html = r.text
    assert html.index('href="/static/tokens.css"') < html.index('href="/static/style.css"')
    assert ' style="' not in html and "<style" not in html
    assert not re.search(r"\son[a-z]+=", html)  # no inline event handlers
    for body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S):
        assert "src=" in body[0] and not body[1].strip()  # no inline scripts
    assert "fonts.googleapis" not in html  # CSP: fonts are self-hosted


@pytest.mark.parametrize("path", PAGES[:7])
def test_the_current_page_is_marked_in_the_nav(client, path):
    if path == "/":
        assert 'aria-current="page"' not in client.get(path).text
    else:
        assert f'<a href="{path}" aria-current="page">' in client.get(path).text


def test_csp_still_forbids_inline_styles_and_scripts():
    assert "style-src 'self';" in CSP and "script-src 'self';" in CSP


def test_tokens_cover_light_and_dark_and_fonts_are_bundled():
    tokens = (UI / "static" / "tokens.css").read_text()
    assert "color-scheme: light dark" in tokens
    for name in ("bg", "ink", "signal", "ok", "partial", "down", "skin-1", "skin-5"):
        assert re.search(rf"--{name}: light-dark\(", tokens), name
    for font in ("Geist-Variable.woff2", "GeistMono-Variable.woff2", "OFL-Geist.txt"):
        assert (UI / "static" / "fonts" / font).stat().st_size > 1000
        assert font in tokens or font.endswith(".txt")


def test_reduced_motion_is_honoured_in_every_stylesheet_with_motion():
    for sheet in (UI / "static").glob("*.css"):
        css = sheet.read_text()
        if "@keyframes" in css or "animation:" in css:
            assert "prefers-reduced-motion" in css, sheet.name


def test_studio_has_the_run_hud_stamp_and_counters_without_js(client):
    html = client.get("/studio").text
    steps = re.findall(r'<li data-step="([a-z_]+)">', html)
    assert steps == ["producer", "copywriter", "art_director", "reviewers", "approval"]
    assert 'id="stamp" hidden' in html and 'id="total-cost"' in html  # text total stays
    assert html.index("/static/app.js") < html.index("/static/motion.js")


def test_motion_inventory_is_guarded_by_reduced_motion():
    css = (UI / "static" / "components.css").read_text()
    guarded = css.split("@media (prefers-reduced-motion: no-preference)")
    assert "@view-transition" in "".join(guarded[1:]) and "@view-transition" not in guarded[0]
    assert "@property --whole" in css and "@counter-style pad5" in css
    js = (UI / "static" / "motion.js").read_text()
    assert "prefers-reduced-motion: reduce" in js and "still.matches" in js
    for target in ("ticker", "images", "signature", "run-hud"):
        assert f'getElementById("{target}")' in js


def test_no_ruler_motif() -> None:
    """The company style takes only the character language; no ruler, scale or tick strip."""
    root = Path(__file__).resolve().parents[1] / "studio" / "ui"
    for path in [*root.glob("static/*.css"), *root.glob("templates/*.html")]:
        text = path.read_text(encoding="utf-8")
        assert "ruler" not in text, path
        assert "repeating-linear-gradient(90deg" not in text, path
