"""Brand scan: the advertiser's own site, read safely, enters the draft as enveloped
suggestions; social profiles are listed, never fetched."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import brand_scan, envelope
from studio.app import create_app
from studio.meta.pixel import PageError
from studio.muse.client import MuseClient
from studio.settings import Budget
from tests.cassettes import FAKE_KEY
from tests.test_brief_flow import draft_json, drafted
from tests.test_ui_studio import login, settings

HOME = """<!doctype html><html lang="pl"><head><title>Glina i Ogień — kubki</title>
<meta name="description" content="Ręcznie toczone kubki z Krakowa">
<meta property="og:site_name" content="Glina i Ogień">
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
 {"@type": "Organization", "name": "Glina i Ogień",
  "sameAs": ["https://www.instagram.com/glinaiogien"]},
 {"@type": "Product", "name": "Kubek Mech", "offers": {"price": "89", "priceCurrency": "PLN"}}
]}</script><script>var tracking = "ignore me";</script></head>
<body><nav><a href="/o-nas">O nas</a><a href="/sklep">Sklep</a><a href="/blog/x">Blog</a>
<a href="https://facebook.com/glinaiogien">FB</a><a href="https://evil.example/o-nas">x</a>
</nav><h1>Kubki, które pamiętają dłonie</h1><p>Wypalane w 1240 °C.</p></body></html>"""


class Site:
    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def fetch(self, url):
        self.asked.append(url)
        if url not in self.pages:
            raise PageError("missing", "http_404")
        return self.pages[url].encode()


SITE = {"https://glina.example/": HOME,
        "https://glina.example/o-nas": "<title>O nas</title><p>Ola, ceramiczka.</p>"}


def test_scan_reads_home_and_the_pages_worth_reading_on_the_same_host():
    site = Site(SITE)
    result = brand_scan.scan("glina.example", site)
    assert site.asked == ["https://glina.example/", "https://glina.example/o-nas",
                          "https://glina.example/sklep"]
    home = result["pages"][0]
    assert home["title"] == "Glina i Ogień — kubki" and home["meta"]["lang"] == "pl"
    assert home["offers"] == [{"name": "Kubek Mech", "price": "89", "currency": "PLN"}]
    assert "1240" in home["text"] and "ignore me" not in home["text"]
    assert result["social"] == {"facebook": "https://facebook.com/glinaiogien",
                                "instagram": "https://www.instagram.com/glinaiogien"}
    assert result["errors"] == [{"url": "https://glina.example/sklep", "code": "http_404"}]


@pytest.mark.parametrize("url,ok", [("http://glina.example/", False),
                                    ("https://user@glina.example/", False),
                                    ("glina.example", True), ("ftp://x", False)])
def test_only_https_sites_are_scanned(url, ok):
    assert (brand_scan.normalize_url(url) is not None) is ok


def test_the_site_is_the_first_url_that_is_not_a_social_profile():
    words = "Nasz IG https://instagram.com/glina i sklep https://glina.example/sklep."
    assert brand_scan.first_site(words) == "https://glina.example/sklep"
    assert brand_scan.first_site("bez linku") is None


def test_the_prompt_sees_the_site_only_inside_the_envelope():
    text = brand_scan.as_prompt(brand_scan.scan("glina.example", Site(SITE)))
    assert text.startswith(envelope.opening("website")) and "Kubek Mech 89 PLN" in text


def _app(tmp_path, **env):
    app = create_app(settings(tmp_path, **env))
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "c1", "model": "muse-spark-1.3",
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(draft_json())}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 400}})

    app.state.muse = MuseClient(FAKE_KEY, ledger=app.state.ledger, budget=Budget(1.0),
                                transport=httpx.MockTransport(handler))
    app.state.brand_fetcher = Site(SITE)
    return app, sent


def test_a_brief_naming_the_site_is_drafted_with_it(tmp_path):
    app, sent = _app(tmp_path, STUDIO_BRAND_SCAN="1")
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c, website="https://glina.example/")
    assert d["site"]["offers"] == [{"name": "Kubek Mech", "price": "89", "currency": "PLN"}]
    prompt = json.dumps(sent[-1])
    assert "Kubek Mech" in prompt and "UNTRUSTED EXTERNAL CONTENT" in prompt
    scans = [r for r in app.state.ledger.receipts(d["draft_run_id"])
             if r.kind == "tool_call" and r.details.get("tool") == "brand.scan"]
    assert len(scans) == 1 and scans[0].details["pages"][0] == "https://glina.example/"


def test_no_scan_unless_the_operator_turned_it_on(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c, website="https://glina.example/")
    assert d["site"] is None and app.state.brand_fetcher.asked == []
