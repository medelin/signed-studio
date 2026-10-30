"""Business profile: the owner's website, the countries they sell in and their Meta pixel,
saved once in Account → Profile; every brief draft starts from them (only the fields the
draft left empty) and says which ones it filled (``from_profile``). The pixel choice lists
the ad account's pixels (read-only) and falls back to a text field."""

import html as htmllib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import accounts
from studio import brief as bv
from studio.meta.adapter import AdapterError
from studio.meta.graph import GraphAdapter
from studio.meta.mock import MockAdapter
from studio.muse.client import MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY
from tests.contract.test_graph import PAGE_TOKEN, TOKEN
from tests.contract.test_graph_pixel import ACT, Graph, _err
from tests.test_account_pages import PASSWORD, client_for, login, make_app, post
from tests.test_brand_scan import SITE, Site
from tests.test_brief import sample
from tests.test_brief_flow import draft_json

PIXEL = "1234567890123456"  # the mock ad account's first pixel


@pytest.fixture
def app(tmp_path):
    a = make_app(tmp_path)
    a.state.accounts.create("ola", PASSWORD, display_name="Ola")
    return a


@pytest.fixture
def client(app):
    with client_for(app) as c:
        login(c)
        yield c


def ola(app):
    return app.state.accounts.by_username("ola")


def save(c, **fields):
    return post(c, "/account/profile", display_name="Ola", advertiser_type="person", **fields)


# -- storage and the form ---------------------------------------------------------------

def test_website_countries_and_pixel_round_trip(app, client):
    r = save(client, website="https://glina.example/sklep", sell_countries="pl, de ,PL",
             pixel_id=PIXEL)
    assert r.status_code == 303 and r.headers["location"] == "/account?done=profile#profile"
    a = ola(app)
    assert (a.website, a.sell_countries, a.pixel_id) == (
        "https://glina.example/sklep", "PL,DE", PIXEL)
    page = client.get("/account").text
    assert 'value="https://glina.example/sklep"' in page and 'value="PL, DE"' in page
    assert f'<option value="{PIXEL}" selected>' in page
    assert app.state.accounts.export(a.id, [])["profile"]["sell_countries"] == "PL,DE"
    # emptied again: nothing is required
    assert save(client, website="", sell_countries="", pixel_id="").status_code == 303
    a = ola(app)
    assert (a.website, a.sell_countries, a.pixel_id) == ("", "", "")


@pytest.mark.parametrize(("field", "value", "code"), [
    ("website", "http://glina.example/", "invalid_website"),
    ("website", "https://", "invalid_website"),
    ("website", "glina.example", "invalid_website"),
    ("website", "https://user@glina.example/", "invalid_website"),
    ("website", "https://glina.example/" + "a" * 300, "invalid_website"),
    ("sell_countries", "Poland", "invalid_countries"),
    ("sell_countries", "PL, D3", "invalid_countries"),
    ("sell_countries", ", ".join(["PL", "DE", "CZ", "SK", "LT", "LV", "EE", "FI", "SE",
                                  "NO", "DK"]), "invalid_countries"),
    ("pixel_id", "12ab", "invalid_pixel"),
    ("pixel_id", "1" * 21, "invalid_pixel"),
])
def test_a_bad_value_names_its_field_and_saves_nothing(app, client, field, value, code):
    fields = {"website": "https://glina.example/", "sell_countries": "PL", "pixel_id": PIXEL}
    assert save(client, **fields).status_code == 303
    r = save(client, **{**fields, field: value})
    assert r.status_code == 400
    page = htmllib.unescape(r.text)
    assert accounts_text(code) in page
    ids = {"website": "pf-website", "sell_countries": "pf-sells", "pixel_id": "pf-pixel"}
    tag = r.text.split(f'id="{ids[field]}"', 1)[1].split(">", 1)[0]
    assert 'aria-invalid="true"' in tag and "profile-error" in tag
    a = ola(app)
    assert (a.website, a.sell_countries, a.pixel_id) == ("https://glina.example/", "PL", PIXEL)


def accounts_text(code):
    from studio.ui import i18n
    return i18n.EN["err." + code]


def test_the_store_raises_the_field_errors(app):
    store, aid = app.state.accounts, ola(app).id
    for fields, exc in (({"website": "ftp://x.example/"}, accounts.InvalidWebsite),
                        ({"sell_countries": "XYZ"}, accounts.InvalidCountries),
                        ({"pixel_id": "-1"}, accounts.InvalidPixel)):
        with pytest.raises(exc):
            store.update_profile(aid, fields)
    assert issubclass(accounts.InvalidPixel, accounts.InvalidProfile)


def test_deletion_erases_the_business_profile(app):
    store, a = app.state.accounts, ola(app)
    store.update_profile(a.id, {"website": "https://glina.example/", "sell_countries": "PL",
                                "pixel_id": PIXEL})
    store.delete(a.id, confirm="ola", password=PASSWORD)
    row = app.state.conn.execute("SELECT website, sell_countries, pixel_id FROM accounts "
                                 "WHERE id = ?", (a.id,)).fetchone()
    assert tuple(row) == ("", "", "")


def test_the_mock_pixels_are_options_with_no_pixel_first(client):
    page = client.get("/account").text
    select = page.split('<select id="pf-pixel" name="pixel_id"', 1)[1].split("</select>", 1)[0]
    options = [o.split(">", 1)[0] for o in select.split("<option")[1:]]
    assert options[0].startswith(' value=""')
    assert [o.split('"')[1] for o in options[1:]] == [p["id"] for p in MockAdapter.PIXELS]
    assert "Clay Workshop website · 1234567890123456" in select
    assert 'aria-describedby="pf-pixel-hint"' in page.split('id="pf-pixel"', 1)[1][:200]


def test_a_saved_pixel_missing_from_the_list_stays_selected(app, client):
    assert save(client, pixel_id="999").status_code == 303
    assert '<option value="999" selected>' in client.get("/account").text


@pytest.mark.parametrize("failure", ["empty", "error", "none"])
def test_without_a_pixel_list_the_pixel_is_a_text_field(app, client, failure):
    if failure == "none":
        app.state.adapter = None
    else:
        def listing():
            if failure == "error":
                raise AdapterError("down", code="graph_error")
            return []
        app.state.adapter.list_pixels = listing
    assert save(client, pixel_id="4242").status_code == 303
    page = client.get("/account").text
    assert '<select id="pf-pixel"' not in page
    tag = page.split('<input id="pf-pixel"', 1)[1].split(">", 1)[0]
    assert 'name="pixel_id"' in tag and 'value="4242"' in tag and 'inputmode="numeric"' in tag
    assert "facebook.com/business/help/" in page  # Meta's own help: the studio makes none


def test_the_fieldset_has_labels_and_hints(client):
    page = client.get("/account").text
    for fid, hint in (("pf-website", "pf-website-hint"), ("pf-sells", "pf-sells-hint")):
        assert f'<label for="{fid}">' in page
        assert f'aria-describedby="{hint}"' in page.split(f'id="{fid}"', 1)[1][:400]
    assert "Your business online" in page
    assert "The studio reads this site before drafting, to suggest facts you confirm." in page


# -- graph: the ad account's pixels -----------------------------------------------------

def _graph(routes, clock):
    graph = Graph(routes)
    g = GraphAdapter(TOKEN, "act_100000000000001", "600000000000001", PAGE_TOKEN,
                     transport=httpx.MockTransport(graph), sleep=lambda s: None,
                     clock=lambda: clock[0])
    return g, graph


def test_graph_lists_pixels_across_pages_and_keeps_them_five_minutes():
    clock = [1000.0]
    page1 = {"data": [{"id": "700000000000001", "name": "Shop"}, {"id": "nope"}],
             "paging": {"cursors": {"after": "c1"}, "next": "https://graph.facebook.com/x"}}
    page2 = {"data": [{"id": "700000000000002"}]}
    g, graph = _graph({ACT: [page1, page2]}, clock)
    want = [{"id": "700000000000001", "name": "Shop"}, {"id": "700000000000002", "name": ""}]
    assert g.list_pixels() == want
    assert [r.url.params["fields"] for r in graph.sent] == ["id,name", "id,name"]
    assert graph.sent[1].url.params["after"] == "c1"
    assert all(r.method == "GET" and TOKEN not in str(r.url) for r in graph.sent)
    clock[0] += 299
    assert g.list_pixels() == want and len(graph.sent) == 2  # cached
    clock[0] += 2
    g.list_pixels()
    assert len(graph.sent) == 3  # five minutes later: read again


def test_graph_errors_are_an_empty_list_and_not_kept():
    clock = [1000.0]
    g, graph = _graph({ACT: _err(100)}, clock)
    assert g.list_pixels() == []
    g.list_pixels()
    assert len(graph.sent) == 2


def test_the_pixel_check_still_reads_ids_only():
    clock = [1000.0]
    g, graph = _graph({ACT: {"data": [{"id": "700000000000001"}]}}, clock)
    assert g._account_pixels() == {"700000000000001"}
    assert graph.sent[0].url.params["fields"] == "id"


# -- profile_defaults -------------------------------------------------------------------

class Profile:
    def __init__(self, website="https://glina.example/", sell_countries="PL,DE",
                 pixel_id=PIXEL):
        self.website, self.sell_countries, self.pixel_id = website, sell_countries, pixel_id


def _empty(goal="sales", sub="website", landing="https://example.com/", **promotion):
    b = sample().model_dump(mode="json")
    b["advertiser"].update(countries=[], landing_url=landing)
    b["audience"]["locations"] = []
    b["objective"] = {"goal": goal, "kpi": "orders", "sub": sub}
    b["promotion"] = promotion
    return bv.BriefV1.model_validate(b)


def test_profile_defaults_fill_only_the_empty_fields():
    draft, paths = bv.profile_defaults(_empty(), Profile(),
                                       default_landing="https://example.com/")
    assert paths == ["advertiser.landing_url", "advertiser.countries", "audience.locations",
                     "promotion.pixel_id"]
    assert draft.advertiser.landing_url == "https://glina.example/"
    assert draft.advertiser.countries == ["PL", "DE"]
    assert draft.audience.locations == ["PL", "DE"]
    assert draft.promotion.pixel_id == PIXEL
    assert not {"advertiser.countries", "advertiser.landing_url", "audience.locations",
                "promotion.pixel_id"} & set(bv.problems(draft, confirmed=False))


def test_profile_defaults_never_replace_what_the_draft_has():
    full = sample(objective={"goal": "sales", "kpi": "orders", "sub": "website"},
                  promotion={"pixel_id": "555"})
    draft, paths = bv.profile_defaults(full, Profile(), default_landing="https://example.com/")
    assert paths == [] and draft == full
    # a landing page that is not https counts as empty; a real one is kept
    bad = _empty(landing="http://clay.example/")
    assert bv.profile_defaults(bad, Profile())[0].advertiser.landing_url == \
        "https://glina.example/"
    own = _empty(landing="https://clay.example/")
    d, paths = bv.profile_defaults(own, Profile(), default_landing="https://example.com/")
    assert d.advertiser.landing_url == "https://clay.example/" and \
        "advertiser.landing_url" not in paths


@pytest.mark.parametrize(("goal", "sub", "filled"), [
    ("sales", "website", True), ("leads", "website", True), ("sales", "catalog", True),
    ("leads", "instant_form", False), ("traffic", "link_clicks", False),
    ("awareness", "", False), ("engagement", "messenger", False)])
def test_the_pixel_only_for_objectives_that_need_one(goal, sub, filled):
    draft, paths = bv.profile_defaults(_empty(goal, sub), Profile())
    assert ("promotion.pixel_id" in paths) is filled
    assert draft.promotion.pixel_id == (PIXEL if filled else "")


def test_an_empty_profile_or_no_account_fills_nothing():
    d = _empty()
    assert bv.profile_defaults(d, None) == (d, [])
    assert bv.profile_defaults(d, Profile("", "", "")) == (d, [])


# -- POST /api/briefs/draft -------------------------------------------------------------

def _drafting_app(tmp_path, draft, **env):
    a = make_app(tmp_path, **env)
    a.state.accounts.create("ola", PASSWORD, display_name="Ola")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 400}})

    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(handler))
    a.state.brand_fetcher = Site(SITE)
    return a, sent


def _gap_draft():
    d = draft_json()
    d["advertiser"].update(countries=[], landing_url="https://example.com/")
    d["audience"]["locations"] = []
    d["objective"] = {"goal": "sales", "kpi": "orders", "sub": "website"}
    d["promotion"] = {}
    return d


def _api_client(app):
    c = TestClient(app, base_url="https://testserver")
    from tests.forms import csrf
    r = c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})
    assert r.status_code == 200, r.text
    return c


TEXT = "Kubki z Krakowa, 89 zł, chcę sprzedawać przez stronę. Budżet 40 zł dziennie."


def test_the_draft_says_which_fields_came_from_the_profile(tmp_path):
    app, _ = _drafting_app(tmp_path, _gap_draft())
    app.state.accounts.update_profile(ola(app).id, {
        "website": "https://glina.example/", "sell_countries": "PL,DE", "pixel_id": PIXEL})
    with _api_client(app) as c:
        r = c.post("/api/briefs/draft", json={"text": TEXT, "lang": "pl"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["from_profile"] == ["advertiser.landing_url", "advertiser.countries",
                                     "audience.locations", "promotion.pixel_id"]
        b = d["brief"]
        assert b["advertiser"]["landing_url"] == "https://glina.example/"
        assert b["advertiser"]["countries"] == ["PL", "DE"]
        assert b["audience"]["locations"] == ["PL", "DE"]
        assert b["promotion"]["pixel_id"] == PIXEL
        assert "advertiser.countries" not in d["problems"]
        # the stored draft is the filled one
        row = app.state.conn.execute("SELECT draft FROM briefs WHERE id = ?",
                                     (d["brief_id"],)).fetchone()
        assert json.loads(row[0])["advertiser"]["countries"] == ["PL", "DE"]


def test_without_a_profile_nothing_is_filled(tmp_path):
    app, _ = _drafting_app(tmp_path, draft_json())
    with _api_client(app) as c:
        r = c.post("/api/briefs/draft", json={"text": TEXT})
        assert r.status_code == 200 and r.json()["from_profile"] == []


def test_the_brand_scan_reads_the_profile_site_when_the_brief_names_none(tmp_path):
    app, sent = _drafting_app(tmp_path, _gap_draft(), STUDIO_BRAND_SCAN="1")
    app.state.accounts.update_profile(ola(app).id, {"website": "https://glina.example/"})
    with _api_client(app) as c:
        d = c.post("/api/briefs/draft", json={"text": TEXT}).json()
    assert d["site"]["url"] == "https://glina.example/"
    assert "Kubek Mech" in json.dumps(sent[-1])
    assert app.state.brand_fetcher.asked[0] == "https://glina.example/"


def test_a_site_named_in_the_brief_wins_over_the_profile(tmp_path):
    app, _ = _drafting_app(tmp_path, _gap_draft(), STUDIO_BRAND_SCAN="1")
    app.state.accounts.update_profile(ola(app).id, {"website": "https://other.example/"})
    with _api_client(app) as c:
        d = c.post("/api/briefs/draft",
                   json={"text": TEXT + " Strona: https://glina.example/"}).json()
    assert d["site"]["url"] == "https://glina.example/"
    assert "https://other.example/" not in app.state.brand_fetcher.asked
