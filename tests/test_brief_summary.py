"""Step 2 of the brief as a short summary and an assistant (brief_summary.js): the page
carries the summary card, the assistant and the folded full form, in both languages, with
the ids the flow and the screen recorder rely on; the script stays CSP-clean."""

import re
import secrets

import pytest
from fastapi.testclient import TestClient

from studio.app import UI, create_app
from studio.settings import Settings
from studio.ui import i18n

NEW_KEYS = ("brief.sum_title", "brief.sum_hint", "brief.ask_title", "brief.ask_hint",
            "brief.ask_back", "brief.ask_skip", "brief.ask_next", "brief.details_summary")


@pytest.fixture
def client(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v"),
                                  "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
                                  "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(a, base_url="https://testserver") as c:
        yield c


def section(html, start, end):
    i = html.index(start)
    return html[i:html.index(end, i)]


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_studio_page_has_summary_assistant_and_folded_details(client, lang):
    html = client.get(f"/studio?lang={lang}").text
    for node in ('id="bv-summary"', 'id="bv-summary-list"', 'id="bv-assistant"',
                 'id="bv-ask-h"', 'id="bv-ask-list"', 'id="bv-ask-back"', 'id="bv-ask-skip"',
                 'id="bv-ask-next"', 'id="bv-details"'):
        assert node in html, node
    for key in NEW_KEYS:
        assert i18n.CATALOGS[lang][key] in html, key
    # the open [FACT] questions are the assistant's (reused, not duplicated) ...
    assistant = section(html, 'id="bv-assistant"', 'id="bv-details"')
    assert 'id="bv-questions"' in assistant and html.count('id="bv-question-list"') == 1
    # ... and every other fieldset is folded under "All details"
    details = section(html, 'id="bv-details"', "</details>")
    for legend in ("brief.s.facts", "brief.s.advertiser", "brief.s.offer", "brief.s.budget",
                   "brief.s.assets"):
        assert i18n.CATALOGS[lang][legend] in details, legend
    # the summary comes first, then the assistant, then the details, then confirm
    order = [html.index(x) for x in ('id="bv-summary"', 'id="bv-assistant"',
                                     'id="bv-details"', 'id="confirm-button"')]
    assert order == sorted(order)


def test_ids_the_flow_and_the_recorder_rely_on_stay(client):
    html = client.get("/studio?lang=en").text
    for node in ('id="use-example"', 'id="src-website"', 'id="run-button"', 'id="brief-v1-form"',
                 'id="confirm-button"', 'id="confirm-status"', 'id="bv-question-list"',
                 'name="advertiser.countries"', 'id="bf-advertiser-countries"'):
        assert node in html, node
    # every form field is addressable by its path
    assert re.search(r'<textarea id="bf-advertiser-countries" name="advertiser.countries" '
                     r'data-path="advertiser.countries"', html)


def test_summary_script_loads_after_brief_js_without_inline_code(client):
    html = client.get("/studio").text
    assert '<script src="/static/brief_summary.js" defer></script>' in html
    assert html.index("/static/brief.js") < html.index("/static/brief_summary.js")
    js = (UI / "static" / "brief_summary.js").read_text()
    assert "innerHTML" not in js and "eval(" not in js
    # it uses the form's API instead of duplicating it, and falls back to the full form
    assert "window.BriefForm" in js and "fallback(" in js
    brief = (UI / "static" / "brief.js").read_text()
    assert "window.BriefForm = {" in brief
    assert 'new CustomEvent("brief:draft"' in brief


def test_assistant_handles_the_quick_answers_and_the_profile_badge():
    js = (UI / "static" / "brief_summary.js").read_text()
    for path in ("advertiser.countries", "advertiser.landing_url", "budget.amount",
                 "offer.product", "advertiser.display_name"):
        assert f'"{path}"' in js, path
    assert "from_profile" in js and "budget_advice" in js
    # EU 27 + UK, NO, CH, US, CA, AU for the free-text countries
    eu = re.search(r"const EU = \[([^\]]+)\]", js)
    assert eu and len(re.findall(r'"[A-Z]{2}"', eu.group(1))) == 27
    for code in ("GB", "NO", "CH", "US", "CA", "AU"):
        assert f'"{code}"' in js


def test_new_strings_exist_in_both_languages():
    js_keys = {k[3:] for k in i18n.EN if k.startswith("js.")}
    js = (UI / "static" / "brief_summary.js").read_text()
    used = set(re.findall(r"""\bT\(\s*["']([a-z0-9_]+)["']""", js))
    assert used and used <= js_keys, sorted(used - js_keys)
    for key in [k for k in i18n.EN if k.startswith(("js.brief_sum_", "js.brief_ask_"))] + \
            list(NEW_KEYS):
        assert i18n.EN[key].strip() and i18n.PL[key].strip(), key
        assert i18n.placeholders(i18n.EN[key]) == i18n.placeholders(i18n.PL[key]), key
