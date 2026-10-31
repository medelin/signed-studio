"""Where the ad can send people (``brief.destination``): the Meta Pixel only matters when
the owner has a website and the ad sends traffic there.

- No website: the objectives that send people to one are disabled in the picker with a
  short reason, the assistant never asks for a pixel or a landing page, and a draft that
  picked one anyway offers a switch to messages or an instant form.
- A website but no pixel, with a website conversion: the assistant offers traffic (link
  clicks, never landing-page views) or an instant form, or the pixel's id. The server
  still refuses the conversion objective without ``pixel_id``.
"""

import re

import pytest

from studio import brief as bv
from studio.app import UI
from studio.meta import objectives
from studio.ui import i18n
from tests.test_brief_summary import client  # noqa: F401  (fixture)
from tests.test_business_profile import (
    PIXEL,
    Profile,
    _api_client,
    _drafting_app,
    _empty,
    _gap_draft,
    ola,
)

DEFAULT = "https://example.com/"
WEBSITE_ROWS = {("traffic", "link_clicks"), ("traffic", "landing_page_views"),
                ("leads", "website"), ("sales", "website"), ("sales", "catalog")}


# -- objectives: which rows need a website, which a pixel ------------------------------

def test_the_website_rows_are_traffic_and_the_website_conversions():
    assert {k for k in objectives.TABLE if objectives.needs_website(*k)} == WEBSITE_ROWS
    assert {k for k in objectives.TABLE if objectives.needs_pixel(*k)} == \
        WEBSITE_ROWS - {("traffic", "link_clicks")}
    assert objectives.needs_website("traffic") and not objectives.needs_pixel("traffic")


def test_traffic_without_a_pixel_optimises_for_link_clicks():
    goal, sub = objectives.NO_PIXEL_SWITCHES[0]
    assert (goal, sub) == ("traffic", "link_clicks")
    spec = objectives.delivery(goal, sub, page_id="600000000000001", promotion={})
    assert spec["optimization_goal"] == "LINK_CLICKS"
    assert spec["promoted_object"] == {} and "attribution_spec" not in spec
    assert objectives.check(spec) == spec
    # the goal's default is link clicks too; landing-page views count through a pixel
    assert objectives.row("traffic").optimization_goal == "LINK_CLICKS"
    assert objectives.needs_pixel("traffic", "landing_page_views")
    assert all(objectives.row(g, s).optimization_goal != "LANDING_PAGE_VIEWS"
               for g, s in objectives.NO_PIXEL_SWITCHES + objectives.NO_WEBSITE_SWITCHES)


def test_the_no_website_switches_keep_people_on_meta_and_need_no_pixel():
    subs = {s for _, s in objectives.NO_WEBSITE_SWITCHES}
    assert {"messenger", "whatsapp", "instagram_direct", "instant_form"} <= subs
    for goal, sub in objectives.NO_WEBSITE_SWITCHES:
        assert not objectives.needs_website(goal, sub) and not objectives.needs_pixel(goal, sub)
    goal, sub = objectives.NO_PIXEL_SWITCHES[1]
    assert (goal, sub) == ("leads", "instant_form")


# -- brief.destination ------------------------------------------------------------------

@pytest.mark.parametrize(("goal", "sub", "issue"), [
    ("sales", "website", "no_website"), ("leads", "website", "no_website"),
    ("sales", "catalog", "no_website"), ("traffic", "", "no_website"),
    ("traffic", "link_clicks", "no_website"), ("leads", "instant_form", ""),
    ("engagement", "messenger", ""), ("awareness", "", "")])
def test_without_a_website_the_website_objectives_are_an_issue(goal, sub, issue):
    draft, _ = bv.profile_defaults(_empty(goal, sub, landing=DEFAULT),
                                   Profile(website="", pixel_id=""), default_landing=DEFAULT)
    d = bv.destination(draft, Profile(website="", pixel_id=""), default_landing=DEFAULT)
    assert (d["website"], d["pixel"], d["issue"]) == (False, False, issue)
    assert d["switches"]["no_website"][0] == ["engagement", "messenger"]


@pytest.mark.parametrize(("goal", "sub", "issue"), [
    ("sales", "website", "no_pixel"), ("leads", "website", "no_pixel"),
    ("sales", "catalog", "no_pixel"), ("traffic", "landing_page_views", "no_pixel"),
    ("traffic", "link_clicks", ""), ("leads", "instant_form", "")])
def test_a_website_without_a_pixel_offers_switches(goal, sub, issue):
    me = Profile(website="https://glina.example/", pixel_id="")
    draft, _ = bv.profile_defaults(_empty(goal, sub), me, default_landing=DEFAULT)
    d = bv.destination(draft, me, default_landing=DEFAULT)
    assert (d["website"], d["pixel"], d["issue"]) == (True, False, issue)
    assert d["switches"]["no_pixel"] == [["traffic", "link_clicks"], ["leads", "instant_form"]]


def test_a_pixel_in_the_profile_or_the_brief_settles_it():
    me = Profile(website="https://glina.example/", pixel_id=PIXEL)
    draft, _ = bv.profile_defaults(_empty("sales", "website"), me, default_landing=DEFAULT)
    assert bv.destination(draft, me, default_landing=DEFAULT)["issue"] == ""
    typed = _empty("sales", "website", landing="https://clay.example/", pixel_id="555")
    d = bv.destination(typed, Profile(website="", pixel_id=""), default_landing=DEFAULT)
    assert (d["website"], d["pixel"], d["issue"]) == (True, True, "")


def test_without_an_own_account_the_configured_page_is_the_website():
    """No account, or the judge and trial accounts: they advertise on the operator's page,
    so nothing is disabled for them."""
    draft = _empty("traffic", "link_clicks", landing=DEFAULT)
    for account in (None, Profile(website="", sees_personal_data=False)):
        assert bv.destination(draft, account, default_landing=DEFAULT)["website"] is True


def test_no_website_never_asks_for_a_landing_page():
    """A draft without a page and an owner without a website: the link holds the default
    (as the draft prompt says) instead of a question the owner cannot answer."""
    me = Profile(website="", pixel_id="")
    draft, filled = bv.profile_defaults(_empty("engagement", "messenger", landing=""), me,
                                        default_landing=DEFAULT)
    assert draft.advertiser.landing_url == DEFAULT and "advertiser.landing_url" not in filled
    assert "advertiser.landing_url" not in bv.problems(draft, confirmed=False)
    # an address the owner typed is never replaced by the default
    typed, _ = bv.profile_defaults(_empty(landing="http://clay.example/"), me,
                                   default_landing=DEFAULT)
    assert typed.advertiser.landing_url == "http://clay.example/"


def test_the_server_still_refuses_a_conversion_without_a_pixel():
    for goal, sub in (("leads", "website"), ("sales", "website"), ("sales", "catalog")):
        draft = _empty(goal, sub, landing="https://clay.example/")
        assert bv.problems(draft, confirmed=False)["promotion.pixel_id"] == \
            "required_for_objective"


# -- the draft API and the page ----------------------------------------------------------

@pytest.mark.parametrize(("website", "pixel", "issue"), [
    ("", "", "no_website"), ("https://glina.example/", "", "no_pixel"),
    ("https://glina.example/", PIXEL, "")])
def test_the_draft_says_where_the_ad_can_send_people(tmp_path, website, pixel, issue):
    app, _ = _drafting_app(tmp_path, _gap_draft())  # sales on the website
    app.state.accounts.update_profile(ola(app).id, {"website": website, "pixel_id": pixel})
    with _api_client(app) as c:
        r = c.post("/api/briefs/draft", json={"text": "Kubki z Krakowa, 89 zł."})
        assert r.status_code == 200, r.text
        d = r.json()["destination"]
        assert (d["website"], d["pixel"], d["issue"]) == (bool(website), bool(pixel), issue)
        # safety unchanged: the conversion still needs the pixel on the server
        assert ("promotion.pixel_id" in r.json()["problems"]) is (not pixel)


def _option(html, select, value, goal=None):
    sel = html.split(f'<select id="{select}"', 1)[1].split("</select>", 1)[0]
    goal_attr = f' data-goal="{goal}"' if goal else ""
    m = re.search(rf'<option value="{value}"{goal_attr}([^>]*)>', sel)
    assert m, (select, value, goal)
    return m.group(1)


def test_the_picker_marks_the_rows_that_need_a_website_or_a_pixel(client):  # noqa: F811
    html = client.get("/studio?lang=en").text
    for goal in ("traffic", "sales"):
        assert "data-website" in _option(html, "bf-objective-goal", goal)
    for goal in ("awareness", "engagement", "leads", "app_promotion"):
        assert "data-website" not in _option(html, "bf-objective-goal", goal)
    for goal, sub in objectives.TABLE:
        attrs = _option(html, "bf-objective-sub", sub, goal)
        assert ("data-website" in attrs) is objectives.needs_website(goal, sub), (goal, sub)
        assert ("data-pixel" in attrs) is objectives.needs_pixel(goal, sub), (goal, sub)


def test_the_scripts_disable_explain_and_switch():
    brief = (UI / "static" / "brief.js").read_text()
    for needle in ('T("brief_needs_website"', "opt.disabled = opt.dataset.goal !== goal || off",
                   '"website" in opt.dataset', '"pixel" in opt.dataset', "siteIssue",
                   "data.destination"):
        assert needle in brief, needle
    js = (UI / "static" / "brief_summary.js").read_text()
    # no website: no landing-page or pixel question; one switch card instead
    assert 'path === "advertiser.landing_url" && !F.hasWebsite()' in js
    assert 'issue === "no_website"' in js and "switchCard(issue)" in js
    # the switches are buttons in a labelled group, keyboard-reachable
    assert js.count('setAttribute("role", "group")') >= 3
    assert '"traffic:link_clicks": () => T("brief_switch_traffic")' in js
    assert "landing_page_views" not in js.split("const SWITCH_LABELS", 1)[1].split("};", 1)[0]
    assert "innerHTML" not in js and "innerHTML" not in brief


@pytest.mark.parametrize("key", [
    "brief_needs_website", "brief_sum_leads_nosite", "brief_sum_goal_no_website",
    "brief_sum_goal_no_pixel", "brief_ask_no_website", "brief_ask_no_website_why",
    "brief_ask_no_pixel", "brief_ask_no_pixel_why", "brief_ask_pixel_label",
    "brief_switch_traffic", "brief_switch_form", "brief_switch_messenger",
    "brief_switch_whatsapp", "brief_switch_instagram", "brief_switch_engagement",
    "brief_switched"])
def test_the_new_strings_exist_in_both_languages(key):
    en, pl = i18n.EN["js." + key], i18n.PL["js." + key]
    assert en.strip() and pl.strip() and i18n.placeholders(en) == i18n.placeholders(pl)


def test_the_traffic_switch_says_no_pixel_is_needed():
    assert "no pixel needed" in i18n.EN["js.brief_switch_traffic"]
    assert "bez pixela" in i18n.PL["js.brief_switch_traffic"]
    assert "needs your website" in i18n.EN["js.brief_needs_website"]
