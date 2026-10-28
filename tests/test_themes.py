"""Display preferences: mode × palette, contrast measured for every combination, rendered on
the server (cookie + attributes), usable without JavaScript."""

import re
import secrets

import pytest
from fastapi.testclient import TestClient

from studio import prefs
from studio.app import create_app
from studio.settings import Settings
from tests.color import TOKENS, palette, ratio

TEXT = [  # (foreground, background) pairs used for text in the CSS: >= 4.5:1
    ("ink", "bg"), ("ink", "bg-raised"), ("ink-2", "bg"), ("ink-2", "bg-raised"),
    ("muted", "bg"), ("muted", "bg-raised"), ("link", "bg"), ("link", "bg-raised"),
    ("signal-ink", "signal"), ("ink", "ok-soft"), ("ink", "partial-soft"),
    ("ink", "signal-soft"), ("bg", "ink-2"), ("ink", "soft"), ("ok", "ok-soft"),
]
STROKES = [  # meaning-carrying lines and marks: >= 3:1
    ("rule", "bg"), ("rule", "bg-raised"), ("signal", "bg"), ("signal", "bg-raised"),
    ("ok", "bg"), ("ok", "bg-raised"), ("partial", "bg"), ("partial", "partial-soft"),
    ("warm", "bg"),
]
COMBOS = [(p, m) for p in prefs.PALETTES for m in ("light", "dark")]


@pytest.mark.parametrize("name,mode", COMBOS)
def test_text_pairs_reach_4_5_to_1(name, mode):
    t = palette(name)[mode]
    low = [(a, b, round(ratio(t[a], t[b]), 2)) for a, b in TEXT if ratio(t[a], t[b]) < 4.5]
    assert not low, low


@pytest.mark.parametrize("name,mode", COMBOS)
def test_meaning_strokes_reach_3_to_1(name, mode):
    t = palette(name)[mode]
    low = [(a, b, round(ratio(t[a], t[b]), 2)) for a, b in STROKES if ratio(t[a], t[b]) < 3]
    assert not low, low


@pytest.mark.parametrize("name,mode", COMBOS)
def test_skin_tones_and_face_lines_are_the_same_in_every_palette(name, mode):
    base, t = palette("graphite")[mode], palette(name)[mode]
    for n in range(1, 6):
        assert t[f"skin-{n}"] == base[f"skin-{n}"]
        assert ratio(t[f"skin-{n}"], t["face-ink"]) >= 3.0
    assert ratio(t["ink"], t["bg"]) >= 3.0  # the people's outline on the page


def test_every_palette_overrides_the_same_tokens_in_both_modes():
    css = TOKENS.read_text()
    for name in prefs.PALETTES[1:]:
        block = css[css.index(f':root[data-palette="{name}"] {{'):]
        block = block[:block.index("\n}")]
        assert block.count("light-dark(") == block.count("--"), name


@pytest.fixture
def client(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v"),
                                  "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
                                  "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(a, base_url="https://testserver") as c:
        yield c


def test_defaults_follow_the_system(client):
    html = client.get("/").text
    assert '<html lang="en" data-palette="graphite">' in html  # no data-theme: system


@pytest.mark.parametrize("name", prefs.PALETTES)
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_choice_is_rendered_and_remembered(client, name, mode):
    r = client.get(f"/help?theme={mode}&palette={name}")
    assert f'data-palette="{name}" data-theme="{mode}"' in r.text
    cookies = [v for k, v in r.headers.multi_items() if k == "set-cookie"]
    assert any(c.startswith(f"theme={mode}") for c in cookies) or mode == "system"
    assert f'data-palette="{name}" data-theme="{mode}"' in client.get("/receipts").text


def test_invalid_values_are_ignored(client):
    html = client.get('/?theme="><script>&palette=neon').text
    assert '<html lang="en" data-palette="graphite">' in html and "<script>&" not in html


def test_the_form_works_without_javascript_and_keeps_the_page_query(client):
    html = client.get("/receipts?run=run_1&kind=review").text
    form = re.search(r'<form class="theme-form" method="get" action="/receipts".*?</form>',
                     html, re.S).group(0)
    assert '<input type="hidden" name="run" value="run_1">' in form
    assert '<input type="hidden" name="kind" value="review">' in form
    assert 'name="theme"' in form and 'name="palette"' in form and 'type="submit"' in form
    for name in prefs.PALETTES:
        assert f'<option value="{name}"' in form
