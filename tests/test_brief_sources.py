"""What the owner wants to advertise, and the links that show it: a website or page and
their Facebook, Instagram and X profiles, asked above the brief. The site is scanned
(``brand_scan``); a profile's public page is read for its meta tags only, never with a
login, and falls back to a named fact behind a login wall; everything reaches the draft
only inside the untrusted-content envelope, with receipts. The draft says what kind of
thing is advertised (``offer.subject_kind``). A full account may keep the website in its
profile; the shared judge account and trial accounts never save it."""

import json
import re

import httpx
import pytest

from studio import brand_scan, envelope
from studio.muse.client import MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY
from tests.test_account_pages import JUDGE, PASSWORD, client_for, login, make_app
from tests.test_brand_scan import SITE, Site
from tests.test_brief_flow import draft_json

TEXT = "Kubki z Krakowa, 89 zł. Budżet 40 zł dziennie przez tydzień."


# -- validation (brand_scan.owner_sources) ------------------------------------------------

@pytest.mark.parametrize("body,field,code", [
    ({"website": "http://glina.example/"}, "website", "not_https"),
    ({"website": "ftp://glina.example/"}, "website", "not_https"),
    ({"website": "https://facebook.com/glina"}, "website", "social_as_website"),
    ({"website": "https://"}, "website", "bad_url"),
    ({"facebook": "https://instagram.com/glina"}, "facebook", "wrong_site"),
    ({"facebook": "https://evilfacebook.com/glina"}, "facebook", "wrong_site"),
    ({"instagram": "http://instagram.com/glina"}, "instagram", "not_https"),
    ({"x": "https://x.com/"}, "x", "no_handle"),
    ({"x": "https://x.com.evil.example/glina"}, "x", "wrong_site"),
    ({"x": 7}, "x", "bad_url"),
])
def test_a_bad_address_is_refused_for_its_own_field(body, field, code):
    _, _, errors = brand_scan.owner_sources(body)
    assert errors == {field: code}


def test_good_addresses_are_kept_as_a_site_and_named_profiles():
    website, profiles, errors = brand_scan.owner_sources({
        "website": " glina.example ", "facebook": "https://www.facebook.com/glinaiogien/",
        "instagram": "@glina.i.ogien", "x": "https://twitter.com/GlinaOgien?s=20",
        "unknown": "ignored"})
    assert errors == {} and website == "https://glina.example/"
    assert profiles == {
        "facebook": {"handle": "glinaiogien", "url": "https://facebook.com/glinaiogien"},
        "instagram": {"handle": "glina.i.ogien", "url": "https://instagram.com/glina.i.ogien"},
        "x": {"handle": "GlinaOgien", "url": "https://x.com/GlinaOgien"}}


def test_empty_fields_are_no_sources():
    assert brand_scan.owner_sources({"website": "", "facebook": "  "}) == ("", {}, {})
    assert brand_scan.owner_sources({}) == ("", {}, {})


def test_named_profiles_enter_the_prompt_only_inside_the_envelope():
    _, profiles, _ = brand_scan.owner_sources({"instagram": "@glina"})
    text = brand_scan.as_prompt(brand_scan.with_profiles(None,
                                                         brand_scan.named_only(profiles)))
    assert text.startswith(envelope.opening("website"))
    assert "instagram" in text and "@glina" in text and "not read" in text


FB_PAGE = """<html><head><title>Glina i Ogień | Facebook</title>
<meta property="og:title" content="Glina i Ogień">
<meta property="og:description" content="Ręcznie toczone kubki. Kubek Mech 89 zł.">
<meta property="og:image" content="https://scontent.example/glina.jpg">
</head><body><p>Prywatne posty, których nie czytamy.</p></body></html>"""
LOGIN_WALL = """<html><head><title>Instagram</title>
<meta property="og:title" content="Log in • Instagram"></head><body>Log in</body></html>"""
PROFILES = {"https://facebook.com/glinaiogien": FB_PAGE,
            "https://instagram.com/glina": LOGIN_WALL}


def test_a_public_profile_gives_only_its_meta_tags():
    _, profiles, _ = brand_scan.owner_sources({"facebook": "https://facebook.com/glinaiogien"})
    fb = brand_scan.read_profiles(profiles, Site(PROFILES))["facebook"]
    assert fb["read"] is True and fb["blocked"] is False
    assert fb["title"] == "Glina i Ogień" and "Kubek Mech 89" in fb["description"]
    assert fb["image"] == "https://scontent.example/glina.jpg"
    assert "Prywatne" not in json.dumps(fb)  # the page's text is never kept


@pytest.mark.parametrize("field,value", [("instagram", "@glina"),  # a login wall
                                         ("x", "@nobody")])  # 404
def test_a_profile_behind_a_login_or_failing_is_only_named(field, value):
    _, profiles, _ = brand_scan.owner_sources({field: value})
    p = brand_scan.read_profiles(profiles, Site(PROFILES))[field]
    assert p["read"] is False and p["blocked"] is True and p["title"] == ""
    assert "not read" in brand_scan.as_prompt(brand_scan.with_profiles(None, {field: p}))


@pytest.mark.parametrize("html", [
    "<title>Facebook</title><p>x</p>",  # no meta tags at all
    '<title>Facebook</title><meta property="og:title" content="Facebook">',
    '<meta property="og:title" content="Zaloguj się do Facebooka">',
])
def test_a_wall_without_the_profile_is_not_read(html):
    named = {"handle": "glina", "url": "https://facebook.com/glina"}
    assert brand_scan.read_profile("facebook", named,
                                   Site({named["url"]: html}))["read"] is False


# -- the studio page --------------------------------------------------------------------

def test_the_page_asks_what_is_advertised_and_for_links(tmp_path):
    app, _ = _app(tmp_path)
    with _client(app) as c:
        html = c.get("/studio?lang=en").text
        pl = c.get("/studio?lang=pl").text
    assert 'id="brief-example"' not in html
    for field in ("website", "facebook", "instagram", "x"):
        m = re.search(rf'<input id="src-{field}" name="{field}" type="url"[^>]*>', html)
        assert m and 'aria-describedby="src-hint err-src-' + field in m.group(0), field
        assert f'<label for="src-{field}">' in html and f'id="err-src-{field}"' in html
    assert 'pattern="https://.+"' in html
    assert "What do you want to advertise?" in html
    assert "We read the pages you link" in html
    assert 'id="bf-offer-subject_kind" data-path="offer.subject_kind"' in html
    for kind in ("product", "service", "event", "person", "organisation", "other"):
        assert f'<option value="{kind}">' in html
    assert re.search(r'<input type="checkbox" id="src-remember"', html)
    assert re.search(r'<button type="button" id="use-example" class="text-link"', html)
    assert "<script>" not in html  # CSP: no inline script
    assert "Co chcesz zareklamować?" in pl and "Czytamy strony" in pl


# -- POST /api/briefs/draft -------------------------------------------------------------

def _app(tmp_path, draft=None, **env):
    a = make_app(tmp_path, **{"STUDIO_BRAND_SCAN": "1", **env})
    a.state.accounts.create("ola", PASSWORD, display_name="Ola")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(draft or draft_json())}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 400}})

    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(handler))
    a.state.brand_fetcher = Site(SITE)
    return a, sent


def _client(app, user="ola", password=PASSWORD):
    c = client_for(app)
    assert login(c, user, password).status_code in (200, 303)
    return c


def _tools(app, run_id, tool):
    return [r for r in app.state.ledger.receipts(run_id)
            if r.kind == "tool_call" and r.details.get("tool") == tool]


def test_bad_sources_are_a_422_naming_each_field_before_any_model_call(tmp_path):
    app, sent = _app(tmp_path)
    with _client(app) as c:
        r = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "http://glina.example/",
            "facebook": "https://instagram.com/glina"})
    assert r.status_code == 422
    assert r.json() == {"error": "invalid_sources",
                        "fields": {"website": "not_https", "facebook": "wrong_site"}}
    assert sent == [] and app.state.brand_fetcher.asked == []


def test_the_site_is_scanned_and_the_profiles_read_for_meta_tags_only(tmp_path):
    app, sent = _app(tmp_path)
    app.state.brand_fetcher.pages.update(PROFILES)
    with _client(app) as c:
        r = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "https://glina.example/",
            "facebook": "https://facebook.com/glinaiogien", "x": "@GlinaOgien"})
    assert r.status_code == 200, r.text
    d = r.json()
    asked = app.state.brand_fetcher.asked
    assert "https://facebook.com/glinaiogien" in asked and "https://x.com/GlinaOgien" in asked
    user = sent[-1]["messages"][-1]["content"]
    block = user[user.index(envelope.opening("website")):]
    assert "Kubek Mech" in block and "Ręcznie toczone kubki" in block
    assert "@GlinaOgien" in block and "not read" in block  # X: 404, only named
    assert "Prywatne" not in user
    assert d["site"]["profiles"] == [
        {"kind": "facebook", "host": "facebook.com", "read": True, "blocked": False},
        {"kind": "x", "host": "x.com", "read": False, "blocked": True}]
    assert len(_tools(app, d["draft_run_id"], "brand.scan")) == 1
    (rec,) = _tools(app, d["draft_run_id"], "brand.profiles")
    assert rec.details["side_effects"] == "read"
    assert rec.details["urls"] == ["https://facebook.com/glinaiogien",
                                   "https://x.com/GlinaOgien"]
    assert rec.details["read"] == ["facebook"] and rec.details["blocked"] == ["x"]
    assert rec.details["result_sha256"]


def test_profiles_without_a_website_still_reach_the_draft(tmp_path):
    no_site = draft_json()
    no_site["advertiser"]["landing_url"] = ""
    app, sent = _app(tmp_path, no_site)
    with _client(app) as c:
        r = c.post("/api/briefs/draft", json={"text": TEXT, "instagram": "@glina"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert app.state.brand_fetcher.asked == ["https://instagram.com/glina"]
    user = sent[-1]["messages"][-1]["content"]
    assert "@glina" in user[user.index(envelope.opening("website")):]
    assert d["destination"]["website"] is False  # no website: messages or an instant form
    assert _tools(app, d["draft_run_id"], "brand.scan") == []


def test_profiles_are_named_even_when_the_scan_is_off(tmp_path):
    app, sent = _app(tmp_path, STUDIO_BRAND_SCAN="0")
    with _client(app) as c:
        r = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "https://glina.example/", "instagram": "@glina"})
    assert r.status_code == 200
    assert app.state.brand_fetcher.asked == [] and "@glina" in json.dumps(sent[-1])
    assert r.json()["site"]["profiles"][0]["blocked"] is False  # not tried: no "login" note


def test_no_sources_at_all_drafts_as_before(tmp_path):
    app, sent = _app(tmp_path)
    with _client(app) as c:
        r = c.post("/api/briefs/draft", json={"text": TEXT, "website": "", "facebook": ""})
    assert r.status_code == 200 and r.json()["site"] is None
    assert "UNTRUSTED" not in json.dumps(sent[-1])


# -- remembering the website ------------------------------------------------------------

def test_a_full_account_keeps_the_website_when_asked(tmp_path):
    app, _ = _app(tmp_path)
    with _client(app) as c:
        d = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "glina.example", "instagram": "@glina",
            "remember": True}).json()
        assert d["remembered"] == ["website"]
        assert app.state.accounts.by_username("ola").website == "https://glina.example/"
        src = c.get("/api/briefs/sources").json()
    assert src == {"website": "https://glina.example/", "can_remember": True}


def test_nothing_is_kept_without_the_tick(tmp_path):
    app, _ = _app(tmp_path)
    with _client(app) as c:
        d = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "https://glina.example/"}).json()
    assert d["remembered"] == [] and app.state.accounts.by_username("ola").website == ""


def test_a_bad_website_is_never_kept(tmp_path):
    app, _ = _app(tmp_path)
    with _client(app) as c:
        r = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "http://glina.example/", "remember": True})
    assert r.status_code == 422 and app.state.accounts.by_username("ola").website == ""


def test_the_judge_account_never_keeps_a_website(tmp_path):
    app, _ = _app(tmp_path)
    with _client(app, "judge", JUDGE) as c:
        assert c.get("/api/briefs/sources").json() == {"website": "", "can_remember": False}
        d = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "https://glina.example/", "remember": True}).json()
    assert d["remembered"] == []
    assert app.state.accounts.by_username("judge").website == ""


def test_a_trial_account_never_keeps_a_website(tmp_path):
    app, _ = _app(tmp_path)
    app.state.accounts.set_tier("ola", "trial")
    with _client(app) as c:
        assert c.get("/api/briefs/sources").json()["can_remember"] is False
        d = c.post("/api/briefs/draft", json={
            "text": TEXT, "website": "https://glina.example/", "remember": True}).json()
    assert d["remembered"] == [] and app.state.accounts.by_username("ola").website == ""


def test_the_sources_need_a_session(tmp_path):
    app, _ = _app(tmp_path)
    with client_for(app) as c:
        assert c.get("/api/briefs/sources").status_code == 401


# -- what is advertised -----------------------------------------------------------------

def test_the_draft_says_what_kind_of_thing_is_advertised(tmp_path):
    event = draft_json()
    event["offer"]["subject_kind"] = "event"
    app, sent = _app(tmp_path, event)
    with _client(app) as c:
        d = c.post("/api/briefs/draft", json={"text": TEXT}).json()
    assert d["brief"]["offer"]["subject_kind"] == "event"
    schema = json.dumps(sent[-1]["response_format"])
    assert "subject_kind" in schema and "organisation" in schema
    assert "offer.subject_kind" in sent[-1]["messages"][0]["content"]


def test_an_older_draft_without_a_kind_reads_as_other():
    from studio import brief as bv
    raw = draft_json()
    raw["offer"].pop("subject_kind", None)
    assert bv.BriefV1.model_validate(raw).offer.subject_kind == "other"
