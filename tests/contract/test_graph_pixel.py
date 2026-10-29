"""Graph adapter ``pixel_check`` against hand-written v25.0 responses (not a recording):
read-only GETs only, the token never in a URL, defensive parsing of the stats edge, and
Graph errors turned into check entries instead of exceptions."""

import httpx
import pytest

from studio.meta import pixel
from studio.meta.graph import GraphAdapter
from tests.contract.test_graph import PAGE_TOKEN, TOKEN

PID = "700000000000001"
ACT = "/v25.0/act_100000000000001/adspixels"
NOW = 1793264400.0  # 2026-10-29 09:00 UTC
RECENT = "2026-10-28T09:00:00+0000"
STALE = "2026-09-01T09:00:00+0000"
PUBLIC = "93.184.216.34"
HTML = f"<script>fbq('init', '{PID}');</script>".encode()


def _err(code, status=400):
    return httpx.Response(status, json={"error": {"message": "secret body", "code": code,
                                                  "type": "OAuthException"}})


class Graph:
    """``routes`` maps a path to a response (or a list of responses, served in order)."""

    def __init__(self, routes):
        self.routes = {k: list(v) if isinstance(v, list) else [v] for k, v in routes.items()}
        self.sent = []

    def __call__(self, request):
        self.sent.append(request)
        queue = self.routes.get(request.url.path)
        if not queue:
            return _err(100)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


def _pixel(**kw):
    return {"id": PID, "name": "Shop pixel", "last_fired_time": RECENT,
            "is_unavailable": False, **kw}


STATS = {"data": [
    {"start_time": "2026-10-27T00:00:00+0000", "aggregation": "event",
     "data": [{"value": "PageView", "count": 900}, {"value": "Lead", "count": 40}]},
    {"start_time": "2026-10-28T00:00:00+0000", "aggregation": "event",
     "data": [{"value": "PageView", "count": 300}, {"value": "Lead", "count": "20"}]}]}


def _healthy(**over):
    routes = {f"/v25.0/{PID}": _pixel(), ACT: {"data": [{"id": PID}]},
              f"/v25.0/{PID}/stats": STATS}
    routes.update(over)
    return routes


def _page(html=HTML):
    def handle(request):
        return httpx.Response(200, content=html)
    return pixel.Fetcher(resolver=lambda host, port: [PUBLIC],
                         transport=httpx.MockTransport(handle))


def make(routes, fetcher=None):
    graph = Graph(routes)
    g = GraphAdapter(TOKEN, "act_100000000000001", "600000000000001", PAGE_TOKEN,
                     transport=httpx.MockTransport(graph), sleep=lambda s: None,
                     fetcher=fetcher if fetcher is not None else _page(), clock=lambda: NOW)
    return g, graph


def codes(result):
    return {c["code"]: c["level"] for c in result["checks"]}


def test_a_healthy_pixel_is_ok_with_reads_only():
    g, graph = make(_healthy())
    r = g.pixel_check(PID, "https://shop.example/", event_type="LEAD")
    assert r["status"] == "ok", r
    assert codes(r) == {"pixel_found": "ok", "account_ok": "ok", "fired_recently": "ok",
                        "events_7d": "ok", "event_seen": "ok", "landing_found": "ok"}
    assert r["events_7d"] == {"total": 1260, "by_event": {"PageView": 1200, "Lead": 60}}
    assert r["last_fired_time"] == RECENT and r["landing_host"] == "shop.example"
    assert {req.method for req in graph.sent} == {"GET"}
    paths = [req.url.path for req in graph.sent]
    assert paths == [f"/v25.0/{PID}", ACT, f"/v25.0/{PID}/stats"]
    first, _, stats = graph.sent
    assert first.url.params["fields"] == "id,name,last_fired_time,is_unavailable"
    assert stats.url.params["aggregation"] == "event"
    assert int(stats.url.params["start_time"]) == int(NOW) - 7 * 24 * 3600
    for req in graph.sent:
        assert TOKEN not in str(req.url) and req.headers["authorization"] == f"Bearer {TOKEN}"


def test_the_result_never_holds_a_token_or_a_body():
    g, _ = make(_healthy(**{f"/v25.0/{PID}": _err(190, 401)}))
    r = g.pixel_check(PID, "https://shop.example/")
    text = repr(r)
    assert TOKEN not in text and "secret body" not in text and r["status"] == "fail"


@pytest.mark.parametrize("code", [100, 803])
def test_a_missing_or_invisible_pixel_fails(code):
    g, graph = make(_healthy(**{f"/v25.0/{PID}": _err(code)}))
    r = g.pixel_check(PID, "https://shop.example/")
    assert r["status"] == "fail" and codes(r) == {"pixel_not_found": "fail"}
    assert len(graph.sent) == 1  # nothing else is read for a pixel that is not there


def test_an_unreadable_pixel_fails_as_unverified():
    g, _ = make(_healthy(**{f"/v25.0/{PID}": _err(190, 401)}))
    assert codes(g.pixel_check(PID, "https://shop.example/")) == {"pixel_unverified": "fail"}
    g, graph = make(_healthy())
    assert codes(g.pixel_check("12ab", "https://shop.example/")) == {"pixel_unverified": "fail"}
    assert graph.sent == []


def test_an_unavailable_pixel_fails():
    g, _ = make(_healthy(**{f"/v25.0/{PID}": _pixel(is_unavailable=True)}))
    r = g.pixel_check(PID, "https://shop.example/")
    assert r["status"] == "fail" and codes(r)["pixel_unavailable"] == "fail"


def test_a_pixel_the_ad_account_cannot_use_fails():
    g, _ = make(_healthy(**{ACT: {"data": [{"id": "700000000000009"}]}}))
    r = g.pixel_check(PID, "https://shop.example/")
    assert r["status"] == "fail" and codes(r)["pixel_not_in_account"] == "fail"


def test_the_account_listing_follows_the_after_cursor():
    pages = [{"data": [{"id": "1"}], "paging": {"cursors": {"after": "QVFI"},
                                                "next": "https://graph.facebook.com/x"}},
             {"data": [{"id": PID}], "paging": {"cursors": {"after": "QVFJ"}}}]
    g, graph = make(_healthy(**{ACT: pages}))
    assert codes(g.pixel_check(PID, "https://shop.example/"))["account_ok"] == "ok"
    listed = [r for r in graph.sent if r.url.path == ACT]
    assert [r.url.params.get("after") for r in listed] == [None, "QVFI"]


def test_an_account_listing_error_is_a_warning():
    g, _ = make(_healthy(**{ACT: _err(200, 403)}))
    r = g.pixel_check(PID, "https://shop.example/")
    assert r["status"] == "warn" and codes(r)["account_unknown"] == "warn"


def test_stats_unavailable_are_unknown_not_a_failure():
    g, _ = make(_healthy(**{f"/v25.0/{PID}/stats": _err(100)}))
    r = g.pixel_check(PID, "https://shop.example/", event_type="LEAD")
    assert r["status"] == "warn" and r["events_7d"] is None
    assert codes(r)["stats_unknown"] == "warn" and "event_seen" not in codes(r)


@pytest.mark.parametrize("body", [{}, {"data": "x"}, {"data": [None, 3, {"data": "x"}]},
                                  {"data": [{"value": "Lead<script>", "count": 5},
                                            {"value": "Lead", "count": -1},
                                            {"value": "Lead", "count": True}]}])
def test_odd_stats_shapes_count_as_no_events(body):
    g, _ = make(_healthy(**{f"/v25.0/{PID}/stats": body}))
    r = g.pixel_check(PID, "https://shop.example/")
    assert r["events_7d"] == {"total": 0, "by_event": {}}
    assert codes(r)["no_events_7d"] == "warn"


def test_flat_stats_pairs_are_read_too():
    flat = {"data": [{"value": "Purchase", "count": 7}]}
    g, _ = make(_healthy(**{f"/v25.0/{PID}/stats": flat}))
    r = g.pixel_check(PID, "https://shop.example/", event_type="PURCHASE")
    assert r["events_7d"]["total"] == 7 and codes(r)["event_seen"] == "ok"


def test_a_stale_or_silent_pixel_warns():
    g, _ = make(_healthy(**{f"/v25.0/{PID}": _pixel(last_fired_time=STALE)}))
    r = g.pixel_check(PID, "https://shop.example/", event_type="PURCHASE")
    assert r["status"] == "warn"
    assert codes(r)["no_recent_events"] == "warn" and codes(r)["event_not_seen"] == "warn"
    never = _pixel()
    del never["last_fired_time"]
    g, _ = make(_healthy(**{f"/v25.0/{PID}": never}))
    assert codes(g.pixel_check(PID, "https://shop.example/"))["never_fired"] == "warn"


def test_a_pixel_missing_from_the_landing_html_only_warns():
    g, _ = make(_healthy(), fetcher=_page(b"<html>tag manager only</html>"))
    r = g.pixel_check(PID, "https://shop.example/")
    assert r["status"] == "warn" and codes(r)["landing_not_found"] == "warn"


def test_an_injected_transport_without_a_fetcher_stays_offline():
    graph = Graph(_healthy())
    g = GraphAdapter(TOKEN, "act_100000000000001", "600000000000001",
                     transport=httpx.MockTransport(graph), sleep=lambda s: None,
                     clock=lambda: NOW)
    r = g.pixel_check(PID, "https://shop.example/")
    assert codes(r)["landing_unreachable"] == "warn" and "offline" in r["checks"][-1]["detail"]
    assert all(req.url.host == "graph.facebook.com" for req in graph.sent)
