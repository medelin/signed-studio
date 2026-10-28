"""The "two views" cast and the Help/FAQ page.

Rules: every illustration under 6 KB, colours only through classes defined in
illustrations.css (tokens), no style/id/defs/gradients/filters/images, forced-colors
fallback, role="img"+label or aria-hidden, no words in drawings; /help works without JS,
anchors unique, PL and EN carry the same questions.
"""

import math
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.settings import Settings
from studio.ui import cast, help_texts

STATIC = Path(__file__).resolve().parents[1] / "studio" / "ui" / "static"
CSS = (STATIC / "illustrations.css").read_text()
TOKENS = (STATIC / "tokens.css").read_text()

ALL = [(p.key, kind) for p in cast.CAST for kind in cast.SCENES]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    a = create_app(Settings.load({
        "STUDIO_DATA_DIR": str(tmp_path_factory.mktemp("v")),
        "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
        "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(a, base_url="https://testserver") as c:
        yield c


@pytest.mark.parametrize("key,kind", ALL)
def test_illustration_under_budget_and_csp_clean(key, kind):
    svg = str(cast.scene(key, kind))
    assert len(svg.encode()) <= cast.MAX_BYTES, (key, kind, len(svg.encode()))
    for bad in (" style=", "<style", " id=", "<defs", "Gradient", "<filter", "<image",
                "href=", "<script"):
        assert bad not in svg, (key, kind, bad)
    assert not re.search(r"\son\w+=", svg)  # no event handlers
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(|oklch\(", svg)
    assert not re.search(r'\b(fill|stroke)="(?!none)', svg)  # colour only via classes


@pytest.mark.parametrize("key,kind", ALL)
def test_drawings_hold_no_words(key, kind):
    for text in re.findall(r"<text[^>]*>([^<]*)</text>", str(cast.scene(key, kind))):
        assert not any(ch.isalpha() for ch in text), text


def test_every_class_in_the_drawings_is_styled():
    used = set()
    for key, kind in ALL:
        for group in re.findall(r'class="([^"]+)"', str(cast.scene(key, kind))):
            used |= set(group.split())
    for keys in GROUPS:
        for group in re.findall(r'class="([^"]+)"', str(cast.group(keys))):
            used |= set(group.split())
    used -= {"il", "animate", "rec", "chair", "on", "sig", "node"} | {
        f"n{i}" for i in range(1, 6)}
    missing = sorted(c for c in used if f".{c}" not in CSS)
    assert not missing, missing


def test_forced_colors_and_reduced_motion_blocks():
    forced = CSS[CSS.index("@media (forced-colors: active)"):]
    for system in ("CanvasText", "Canvas", "GrayText", "LinkText"):
        assert system in forced
    for n in range(1, 6):
        assert f".il .skin-{n}" in forced
    assert "@media (prefers-reduced-motion: no-preference)" in CSS
    assert CSS.index("animation:") > CSS.index("prefers-reduced-motion: no-preference")


def test_accessible_name_or_hidden():
    labelled = str(cast.scene("mei", "solo", label="Mei, ceramics"))
    assert 'role="img"' in labelled and 'aria-label="Mei, ceramics"' in labelled
    decorative = str(cast.scene("mei", "signed"))
    assert 'aria-hidden="true"' in decorative and "role=" not in decorative
    assert 'aria-label="a &amp; b"' in str(cast.scene("ola", "solo", label="a & b"))


def test_the_cast_is_diverse():
    people = cast.CAST
    assert len(people) >= 8
    ages = sorted(p.age for p in people)
    assert ages[0] < 30 and ages[-1] >= 70
    assert {p.pronoun for p in people} == {"she", "he"}
    assert {p.skin for p in people} == {1, 2, 3, 4, 5}
    assert {"afro", "locs", "braid", "straight", "curly", "bun", "bald", "hijab", "turban",
            "wrap"} <= {p.hair for p in people}
    assert any(p.wheelchair for p in people)
    assert any(p.hearing_aid for p in people) and any(p.glasses for p in people)
    assert any(p.beard for p in people) and any(p.wrinkles for p in people)
    assert len({p.business for p in people}) == len(people)
    assert len({p.prop for p in people}) == len(people)


GROUPS = [["amara", "harjit", "mei", "kofi"], ["grace", "diego", "leila", "tomasz"],
          ["ola", "kofi", "mei"], ["priya", "amara", "tomasz"],
          ["leila", "harjit", "ola", "diego", "grace"]]


@pytest.mark.parametrize("keys", [[p.key] for p in cast.CAST] + GROUPS)
@pytest.mark.parametrize("kind", [k for k in cast.SCENES if k != "solo"])
def test_people_never_overlap_each_other_or_the_trace_and_record(keys, kind):
    lay = cast.layout(keys, kind)
    boxes = lay.boxes
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not cast.overlaps(a, b), (keys, a, b)
    right = max(b[2] for b in boxes)
    sx, _ = lay.start
    assert sx > right and min(x for x, _ in lay.nodes) > right
    assert lay.rec[0] > right
    assert max(b[2] for b in boxes) <= lay.width and all(b[0] >= 0 for b in boxes)
    svg = str(cast.group(keys, kind))
    top = float(re.search(r'viewBox="0 ([\d.]+) ', svg).group(1))
    assert all(b[1] >= top for b in boxes)  # nobody is cut off at the top
    assert len(svg.encode()) <= cast.MAX_BYTES * len(keys) + 2048  # 6 KB per person drawn


def test_person_boxes_hold_their_silhouette():
    for p in cast.CAST:
        x0, y0, x1, y1 = cast.person_box(p)
        for x, y in p.silhouette():
            assert x0 <= x <= x1 and y0 <= y <= y1, p.key


def test_no_grid_no_blob_no_silhouette_copy():
    for key, kind in ALL:
        svg = str(cast.scene(key, kind))
        assert "r-grid" not in svg and "c-blob" not in svg
        assert 'class="r"' not in svg  # no dashed copy of a person inside a record
    css = "".join((STATIC / f).read_text() for f in ("tokens.css", "style.css",
                                                      "components.css", "illustrations.css"))
    assert "--grid" not in css and ".r-grid" not in css and "c-blob" not in css
    style = (STATIC / "style.css").read_text()
    assert "linear-gradient" not in style  # the page background has no grid


def test_signature_makes_the_record_correct():
    signed, unsigned = str(cast.scene("amara", "signed")), str(cast.scene("amara", "unsigned"))
    assert "r-signed" in signed and "node sig on" in signed and "warn-field" not in signed
    assert "warn-field" in unsigned and "node sig on" not in unsigned and ">!<" in unsigned


# -- contrast of the skin tokens -------------------------------------------------------


def _oklch_y(L: float, C: float, h: float) -> float:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    r, g, bl = (min(max(v, 0.0), 1.0) for v in (r, g, bl))
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl


def _token(name: str) -> list[tuple[float, float, float]]:
    value = re.search(rf"--{name}:\s*([^;]+);", TOKENS)
    assert value, name
    return [tuple(map(float, m)) for m in re.findall(r"oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)",
                                                     value.group(1))]  # type: ignore[misc]


def _ratio(y1: float, y2: float) -> float:
    hi, lo = max(y1, y2), min(y1, y2)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("n", range(1, 6))
def test_face_lines_reach_3_to_1_on_every_skin_tone(n):
    face = _oklch_y(*_token("face-ink")[0])
    tones = _token(f"skin-{n}")
    assert len(tones) == 2  # light + dark
    for tone in tones:
        assert _ratio(_oklch_y(*tone), face) >= 3.0, (n, tone)


def test_outline_and_trace_reach_3_to_1_on_paper():
    for fg in ("ink", "signal", "rule", "ok"):
        for (fl, bgl) in zip(_token(fg), _token("bg"), strict=True):
            assert _ratio(_oklch_y(*fl), _oklch_y(*bgl)) >= 3.0, fg


# -- the help page ---------------------------------------------------------------------


def test_help_renders_without_js_with_unique_anchors(client):
    r = client.get("/help")
    assert r.status_code == 200
    html = r.text
    anchors = re.findall(r'<details class="faq-item" id="([^"]+)"', html)
    assert len(anchors) == len(help_texts.ENTRIES) == len(set(anchors))
    for e in help_texts.ENTRIES:
        assert f'href="#{e.anchor}"' in html
        assert f"<summary><h2>{e.q['en']}</h2></summary>" in html.replace("&#39;", "'")
    assert html.count('<svg class="il') == len(help_texts.ENTRIES) + 1
    assert "c-blob" not in html
    assert "<noscript" not in html and "onclick" not in html


def test_help_in_polish_has_the_same_questions(client):
    en, pl = client.get("/help").text, client.get("/pl/help").text
    assert '<html lang="pl">' in pl and '<html lang="en">' in en
    ids = re.compile(r'<details class="faq-item" id="([^"]+)"')
    assert ids.findall(en) == ids.findall(pl)
    for e in help_texts.ENTRIES:
        assert set(e.q) == set(e.a) == set(help_texts.LANGS)
        assert e.person in cast.BY_KEY and e.scene in cast.SCENES
    assert '<html lang="pl">' in client.get("/help?lang=pl").text


def test_help_is_linked_from_every_page_and_the_404(client):
    for path in ("/", "/studio", "/inbox", "/leads", "/report", "/receipts", "/help"):
        assert 'href="/help"' in client.get(path).text, path
    r = client.get("/no-such-page", headers={"accept": "text/html"})
    assert r.status_code == 404 and 'href="/help"' in r.text and "<svg" in r.text


def test_api_404_stays_json(client):
    r = client.get("/api/no-such", headers={"accept": "text/html"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert client.get("/no-such-page").headers["content-type"].startswith("application/json")


def test_cast_gallery_is_noindex_and_shows_both_themes(client):
    r = client.get("/style/cast")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"
    assert '<meta name="robots" content="noindex">' in r.text
    assert 'data-theme="light"' in r.text and 'data-theme="dark"' in r.text
    for p in cast.CAST:
        assert f'aria-label="{p.name}, {p.business}"' in r.text


def test_pages_load_tokens_and_illustration_css(client):
    html = client.get("/help").text
    for sheet in ("tokens.css", "components.css", "illustrations.css"):
        assert f'href="/static/{sheet}"' in html
