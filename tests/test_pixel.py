"""Pixel check before signing (``studio.meta.pixel``): the landing-page matcher, the
SSRF-safe fetcher, the result's status and the gate in front of ``campaign.create``."""

import httpx
import pytest

from studio.meta import objectives, pixel
from studio.meta.adapter import MOCK_PAGE
from studio.meta.mock import MockAdapter

PID = "700000000000001"
PUBLIC = "93.184.216.34"


# -- HTML matcher ---------------------------------------------------------------------


@pytest.mark.parametrize("html", [
    f"<script>fbq('init', '{PID}');fbq('track','PageView');</script>",
    f'<script>fbq("init","{PID}")</script>',
    f"<script>fbq( 'init' , {PID} , {{em: 'x'}});</script>",
    f'<noscript><img src="https://www.facebook.com/tr?id={PID}&ev=PageView&noscript=1"/>',
    f'<img src="https://www.facebook.com/tr/?id={PID}&amp;ev=PageView">',
    f'<img src="https://www.facebook.com/tr?ev=PageView&amp;id={PID}&noscript=1">',
])
def test_the_pixel_id_is_found_in_its_usual_snippets(html):
    assert pixel.find_pixel(html, PID)


@pytest.mark.parametrize("html", [
    "<html><body>no pixel here</body></html>",
    f"<script>fbq('init', '{PID}9');</script>",  # a longer id is another pixel
    f"<script>fbq('init', '1{PID}');</script>",
    f'<img src="https://www.facebook.com/tr?id={PID}0&ev=PageView">',
    f'<img src="https://example.com/tr?id={PID}">',  # not Meta's endpoint
    f"<p>{PID}</p>",  # the bare number is not a pixel snippet
])
def test_other_ids_and_bare_numbers_do_not_count(html):
    assert not pixel.find_pixel(html, PID)


def test_a_non_numeric_id_never_matches():
    assert not pixel.find_pixel("fbq('init', '.*')", ".*")


# -- address guard --------------------------------------------------------------------


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
    "100.127.255.254", "0.0.0.0",  # noqa: S104
    "224.0.0.1", "240.0.0.1", "255.255.255.255",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1", "ff0e::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1", "2001:db8::1",
])
def test_private_loopback_link_local_cgnat_multicast_and_reserved_are_refused(ip):
    assert not pixel.public_ip(ip)


@pytest.mark.parametrize("ip", [PUBLIC, "1.1.1.1", "100.128.0.1", "2606:4700::1111"])
def test_public_addresses_pass(ip):
    assert pixel.public_ip(ip)


# -- fetcher --------------------------------------------------------------------------


class Web(httpx.BaseTransport):
    """A fake internet: ``routes`` maps (connected IP, Host header, path) to a response."""

    def __init__(self, routes):
        self.routes = routes
        self.sent = []

    def handle_request(self, request):
        self.sent.append(request)
        key = (request.url.host, request.headers["host"], request.url.path)
        make = self.routes.get(key)
        if make is None:
            return httpx.Response(404, text="nope")
        return make(request)


def dns(table):
    def resolve(host, port):
        if host not in table:
            raise OSError("no such host")
        return list(table[host])
    return resolve


def fetcher(routes, table, **kw):
    web = Web(routes)
    return pixel.Fetcher(resolver=dns(table), transport=web, **kw), web


def test_a_page_is_fetched_from_the_vetted_address_with_its_host_name():
    f, web = fetcher({(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(200, text="hi")},
                     {"shop.example": [PUBLIC]})
    assert f.fetch("https://shop.example/") == b"hi"
    (req,) = web.sent
    assert req.url.host == PUBLIC and req.headers["host"] == "shop.example"
    assert req.extensions["sni_hostname"] == "shop.example"
    assert "cookie" not in req.headers and "authorization" not in req.headers
    assert req.headers["user-agent"] == pixel.USER_AGENT


@pytest.mark.parametrize("url", ["http://shop.example/", "ftp://shop.example/",
                                 "https://user@shop.example/", "https://shop.example:8443/",
                                 "javascript:alert(1)", "https:///nohost"])
def test_only_plain_https_on_443_is_fetched(url):
    f, web = fetcher({}, {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch(url)
    assert exc.value.code == "bad_url"
    assert web.sent == []


@pytest.mark.parametrize("addrs", [["127.0.0.1"], ["10.0.0.5"], [PUBLIC, "192.168.0.1"],
                                   ["100.64.1.1"], ["fd00::1"]])
def test_a_host_resolving_to_a_private_address_is_never_contacted(addrs):
    f, web = fetcher({}, {"shop.example": addrs})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "private_address"
    assert web.sent == []


def test_an_ip_literal_is_checked_too():
    f, web = fetcher({}, {})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://169.254.169.254/latest/meta-data/")
    assert exc.value.code == "private_address" and web.sent == []


def test_a_redirect_to_a_private_address_is_refused_before_it_is_followed():
    routes = {(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(
        302, headers={"location": "https://internal.example/admin"})}
    f, web = fetcher(routes, {"shop.example": [PUBLIC], "internal.example": ["10.0.0.7"]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "private_address"
    assert [r.headers["host"] for r in web.sent] == ["shop.example"]


def test_a_redirect_to_http_is_refused():
    routes = {(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(
        301, headers={"location": "http://shop.example/"})}
    f, web = fetcher(routes, {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "bad_url" and len(web.sent) == 1


def test_relative_redirects_are_followed_up_to_three_times():
    def hop(n):
        return lambda r: httpx.Response(302, headers={"location": f"/{n}"})
    routes = {(PUBLIC, "shop.example", "/"): hop(1), (PUBLIC, "shop.example", "/1"): hop(2),
              (PUBLIC, "shop.example", "/2"): hop(3),
              (PUBLIC, "shop.example", "/3"): lambda r: httpx.Response(200, text="ok")}
    f, web = fetcher(routes, {"shop.example": [PUBLIC]})
    assert f.fetch("https://shop.example/") == b"ok"
    assert len(web.sent) == 4
    routes[(PUBLIC, "shop.example", "/3")] = hop(4)
    f, _ = fetcher(routes, {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "too_many_redirects"


def test_cookies_set_by_a_hop_are_not_sent_to_the_next():
    routes = {(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(
        302, headers={"location": "/next", "set-cookie": "sid=abc; Path=/"}),
        (PUBLIC, "shop.example", "/next"): lambda r: httpx.Response(200, text="ok")}
    f, web = fetcher(routes, {"shop.example": [PUBLIC]})
    assert f.fetch("https://shop.example/") == b"ok"
    assert "cookie" not in web.sent[1].headers


def test_an_oversize_page_is_cut_off():
    big = b"x" * (pixel.MAX_BYTES + 1)
    routes = {(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(200, content=big)}
    f, _ = fetcher(routes, {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "too_large"
    declared = {(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(
        200, headers={"content-length": str(10 * pixel.MAX_BYTES)}, content=b"x")}
    f, _ = fetcher(declared, {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "too_large"


def test_an_error_status_and_an_unknown_host_are_page_errors():
    f, _ = fetcher({(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(500)},
                   {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "http_500"
    f, _ = fetcher({}, {})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://gone.example/")
    assert exc.value.code == "dns_failed"


def test_a_network_failure_is_a_page_error():
    def boom(request):
        raise httpx.ConnectTimeout("slow")
    f, _ = fetcher({(PUBLIC, "shop.example", "/"): boom}, {"shop.example": [PUBLIC]})
    with pytest.raises(pixel.PageError) as exc:
        f.fetch("https://shop.example/")
    assert exc.value.code == "unreachable"


def test_the_offline_fetcher_never_resolves_or_connects():
    with pytest.raises(pixel.PageError) as exc:
        pixel.Offline().fetch("https://shop.example/")
    assert exc.value.code == "offline"


# -- landing check and status ---------------------------------------------------------


def test_landing_check_found_missing_and_refused():
    page = f"<script>fbq('init','{PID}')</script>".encode()
    f, _ = fetcher({(PUBLIC, "shop.example", "/"): lambda r: httpx.Response(200, content=page),
                    (PUBLIC, "shop.example", "/bare"): lambda r: httpx.Response(200, text="x")},
                   {"shop.example": [PUBLIC], "lan.example": ["192.168.0.2"]})
    assert pixel.landing_check(f, PID, "https://shop.example/")["code"] == "landing_found"
    missing = pixel.landing_check(f, PID, "https://shop.example/bare")
    assert (missing["code"], missing["level"]) == ("landing_not_found", "warn")
    assert "tag manager" in missing["detail"]
    refused = pixel.landing_check(f, PID, "https://lan.example/")
    assert (refused["code"], refused["level"]) == ("landing_refused", "warn")


def test_status_is_the_worst_check():
    ok, warn, fail = (pixel.entry("a", "ok", ""), pixel.entry("b", "warn", ""),
                      pixel.entry("c", "fail", ""))
    assert pixel.status_of([ok]) == "ok"
    assert pixel.status_of([ok, warn]) == "warn"
    assert pixel.status_of([warn, fail, ok]) == "fail"


def test_the_mock_adapter_returns_a_healthy_deterministic_result():
    m = MockAdapter()
    a = m.pixel_check(PID, "https://shop.example/")
    assert a == m.pixel_check(PID, "https://shop.example/")
    assert a["status"] == "ok" and a["pixel_id"] == PID
    assert {c["level"] for c in a["checks"]} == {"ok"}
    assert a["events_7d"]["total"] > 0 and a["last_fired_time"]
    assert ("pixel_check", PID) in m.calls


# -- gate -----------------------------------------------------------------------------


def _delivery(goal, sub=""):
    return objectives.delivery(goal, sub, page_id=MOCK_PAGE, promotion={
        "pixel_id": PID, "privacy_policy_url": "https://shop.example/privacy"})


def test_pixel_objectives_are_the_rows_promoting_a_pixel():
    assert pixel.pixel_of(_delivery("sales", "website")) == PID
    assert pixel.pixel_of(_delivery("leads", "website")) == PID
    assert pixel.pixel_of(_delivery("leads", "instant_form")) == ""
    assert pixel.pixel_of(_delivery("traffic")) == ""
    assert pixel.pixel_of(None) == ""


def test_gate_fail_blocks_warn_passes_and_no_pixel_needs_nothing():
    spec = _delivery("sales", "website")
    result = MockAdapter().pixel_check(PID, "https://shop.example/")
    ok = pixel.signed(result)
    assert ok["verdict"] == "ok" and "status" not in ok
    assert pixel.gate({"delivery": spec, "pixel_check": ok}) == ""
    assert pixel.gate({"delivery": spec, "pixel_check": {**ok, "verdict": "warn"}}) == ""
    failed = {**ok, "verdict": "fail"}
    assert pixel.gate({"delivery": spec, "pixel_check": failed}) == "pixel_check_failed"
    assert pixel.gate({"delivery": spec}) == "pixel_check_missing"
    other = {**ok, "pixel_id": "700000000000002"}
    assert pixel.gate({"delivery": spec, "pixel_check": other}) == "pixel_check_mismatch"
    for odd in ("?", None, "OK"):
        assert pixel.gate({"delivery": spec, "pixel_check": {**ok, "verdict": odd}}) == \
            "pixel_check_failed"
    # the unsigned shape (``status``) is not a verdict
    assert pixel.gate({"delivery": spec, "pixel_check": result}) == "pixel_check_failed"
    assert pixel.gate({"delivery": _delivery("leads", "instant_form")}) == ""
    assert pixel.gate({"delivery": _delivery("traffic")}) == ""


def test_an_internationalised_host_is_resolved_by_its_ascii_name():
    seen = []

    def resolve(host, port):
        seen.append(host)
        return [PUBLIC]
    web = Web({(PUBLIC, "xn--w-uga1v8h.example", "/"): lambda r: httpx.Response(200, text="ok")})
    f = pixel.Fetcher(resolver=resolve, transport=web)
    assert f.fetch("https://żółw.example/") == b"ok" and seen == ["xn--w-uga1v8h.example"]


def test_an_unexpected_fetch_error_is_only_a_warning():
    class Broken:
        def fetch(self, url):
            raise RuntimeError("bug")
    c = pixel.landing_check(Broken(), PID, "https://shop.example/")
    assert (c["code"], c["level"]) == ("landing_unreachable", "warn")
