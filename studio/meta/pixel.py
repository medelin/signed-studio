"""Pixel check: does the advertiser's Meta pixel work, before a person signs?

A website leads or sales campaign (every ``objectives.TABLE`` row whose ad set promotes a
``pixel_id``) optimises for events the advertiser's pixel sends; a pixel that does not
exist, that the ad account cannot use, or that never fires makes a campaign that spends
and learns nothing. The producer runs ``Adapter.pixel_check`` (read-only) when it builds
such a campaign, records a ``pixel.check`` receipt and puts the result in the
``campaign.create`` payload, so the person signs the check together with the campaign.

- Graph reads (``graph.py``): ``GET /{pixel_id}?fields=id,name,last_fired_time,
  is_unavailable``, ``GET /act_{id}/adspixels?fields=id`` (the pixels the ad account can
  use) and ``GET /{pixel_id}/stats?aggregation=event`` for the last 7 days. A stats edge
  that errors is "unknown", never a failure.
- Landing page: ``Fetcher`` gets ``landing_url`` and ``find_pixel`` looks for this pixel's
  id in the HTML (``fbq('init', '<id>')`` and the ``facebook.com/tr?id=<id>`` image).
  https on port 443 only; every host (the first request and each of at most
  ``MAX_REDIRECTS`` redirects) is resolved and refused unless every address is public —
  no private, loopback, link-local, CGNAT (100.64.0.0/10), multicast or reserved address,
  v4 or v6 (``public_ip``). The request then goes to the vetted address itself (the host
  name travels as ``Host`` and TLS SNI, so a second DNS answer cannot redirect it), with
  a ``TIMEOUT_S`` deadline, a ``MAX_BYTES`` cap, no cookies, no proxy from the
  environment and a plain user agent. An id missing from the HTML is a warning: a tag
  manager may load the pixel.
- ``status``: ``fail`` if any check failed (pixel missing, not usable by the ad account,
  unavailable, or not verifiable), else ``warn`` if any warned (no events in 7 days, the
  campaign's event not seen, stats unknown, id not in the landing HTML), else ``ok``.

``gate`` is what signing (``POST /api/approvals/{id}``) and execution
(``media_buyer.create``) check: a pixel campaign needs a check for its own pixel in the
signed payload (``signed``: the same result with ``verdict`` for ``status``), and a failed
one is refused before anything reaches Meta. The check is not
repeated at execution: the payload binds it (``content_sha256``), and the ``pixel.check``
receipt binds it by ``result_sha256``. A result never holds a token or a response body.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

import httpx

OK, WARN, FAIL = "ok", "warn", "fail"
LEVELS: Final = (OK, WARN, FAIL)
TOOL: Final = "pixel.check"
WINDOW_S: Final = 7 * 24 * 3600
TIMEOUT_S: Final = 10.0
MAX_BYTES: Final = 2 * 1024 * 1024
MAX_REDIRECTS: Final = 3
USER_AGENT: Final = "MuseStudioLite-PixelCheck/1.0"
CGNAT: Final = ipaddress.ip_network("100.64.0.0/10")
PIXEL_ID: Final = re.compile(r"\d{1,30}")
_EVENT_NAME: Final = re.compile(r"[A-Za-z0-9_]{1,40}")
# custom_event_type (ad set promoted object) → the standard event name the pixel sends
EVENT_NAMES: Final = {
    "LEAD": "Lead", "COMPLETE_REGISTRATION": "CompleteRegistration", "CONTACT": "Contact",
    "SUBMIT_APPLICATION": "SubmitApplication", "SCHEDULE": "Schedule",
    "PURCHASE": "Purchase", "ADD_TO_CART": "AddToCart",
    "INITIATED_CHECKOUT": "InitiateCheckout"}

Resolver = Callable[[str, int], list[str]]


class PageError(Exception):
    """The landing page could not be fetched safely (``code`` says why)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


# -- HTML matcher ---------------------------------------------------------------------


def find_pixel(html: str, pixel_id: str) -> bool:
    """True when ``html`` initialises this pixel: ``fbq('init', '<id>')`` (any quotes,
    spacing, or a bare number) or the noscript image ``facebook.com/tr?…id=<id>``. A
    longer id that merely starts or ends with these digits does not count."""
    if not PIXEL_ID.fullmatch(pixel_id or ""):
        return False
    pid = re.escape(pixel_id)
    init = rf"""fbq\(\s*['"]init['"]\s*,\s*['"]?{pid}(?![0-9])"""
    image = rf"""facebook\.com/tr/?\?(?:[^"'\s<>]*?(?:&amp;|&))?id={pid}(?![0-9])"""
    return re.search(init, html) is not None or re.search(image, html) is not None


# -- address guard and fetcher --------------------------------------------------------


def public_ip(value: str) -> bool:
    """True only for a globally routable unicast address. Private, loopback, link-local,
    CGNAT, multicast, reserved and unspecified addresses are refused, and so is an IPv6
    address that embeds a refused IPv4 one (mapped, 6to4, Teredo)."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and not public_ip(str(embedded)):
            return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT:
        return False
    return (ip.is_global and not ip.is_multicast and not ip.is_reserved
            and not ip.is_loopback and not ip.is_link_local and not ip.is_unspecified)


def system_resolver(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to (A and AAAA)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _target(url: str) -> tuple[str, str]:
    """``(host, path?query)`` of a fetchable URL, ``PageError("bad_url")`` otherwise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise PageError("not a URL", "bad_url") from None
    if (parts.scheme != "https" or not parts.hostname or "@" in parts.netloc
            or port not in (None, 443) or len(url) > 2000
            or any(c.isspace() or ord(c) < 0x20 for c in url)):
        raise PageError("only https URLs on port 443 are fetched", "bad_url")
    try:  # an internationalised name travels as its ASCII (IDNA) form
        host = parts.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise PageError("not a valid host name", "bad_url") from None
    return host, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


class Fetcher:
    """The SSRF-safe landing-page fetch (see the module docstring). ``resolver`` and
    ``transport`` are injectable so tests and replay never touch the network."""

    def __init__(self, *, resolver: Resolver = system_resolver,
                 transport: httpx.BaseTransport | None = None, timeout: float = TIMEOUT_S,
                 max_bytes: int = MAX_BYTES, max_redirects: int = MAX_REDIRECTS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._resolve = resolver
        self._transport = transport
        self._timeout = timeout
        self._max = max_bytes
        self._redirects = max_redirects
        self._clock = clock

    def _address(self, host: str) -> str:
        try:
            ipaddress.ip_address(host)
            addrs = [host]
        except ValueError:
            try:
                addrs = self._resolve(host, 443)
            except (OSError, UnicodeError):
                raise PageError("the landing host does not resolve", "dns_failed") from None
        if not addrs:
            raise PageError("the landing host does not resolve", "dns_failed")
        if not all(public_ip(a) for a in addrs):
            raise PageError("the landing host resolves to a non-public address",
                            "private_address")
        return addrs[0]

    def fetch(self, url: str) -> bytes:
        deadline = self._clock() + self._timeout
        for _ in range(self._redirects + 1):
            host, path = _target(url)
            ip = self._address(host)
            literal = host == ip
            netloc = f"[{ip}]" if ":" in ip else ip
            headers = {"Host": host, "User-Agent": USER_AGENT,
                       "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"}
            # a fresh client per hop: no cookie jar carries over, no proxy from the env
            with httpx.Client(transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False) as client:
                try:
                    with client.stream("GET", f"https://{netloc}{path}", headers=headers,
                                       extensions={} if literal else {
                                           "sni_hostname": host}) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location", "")
                            if not location:
                                raise PageError("a redirect without a location", "bad_redirect")
                            url = urljoin(url, location)
                            continue
                        if resp.status_code >= 400:
                            raise PageError("the landing page answered with an error",
                                            f"http_{resp.status_code}")
                        return self._read(resp, deadline)
                except httpx.HTTPError:
                    raise PageError("the landing page is unreachable", "unreachable") from None
        raise PageError(f"more than {self._redirects} redirects", "too_many_redirects")

    def _read(self, resp: httpx.Response, deadline: float) -> bytes:
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max:
            raise PageError("the landing page is too large", "too_large")
        body = bytearray()
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > self._max:
                raise PageError("the landing page is too large", "too_large")
            if self._clock() > deadline:
                raise PageError("the landing page took too long", "unreachable")
        return bytes(body)


class Offline:
    """A fetcher that never resolves or connects (an injected Graph transport: tests and
    replay); the landing check reports it as a warning."""

    def fetch(self, url: str) -> bytes:
        raise PageError("the landing page is not fetched offline", "offline")


# -- result ---------------------------------------------------------------------------


def entry(code: str, level: str, detail: str) -> dict[str, str]:
    assert level in LEVELS
    return {"code": code, "level": level, "detail": detail[:200]}


def status_of(checks: Sequence[Mapping[str, Any]]) -> str:
    levels = {c.get("level") for c in checks}
    return FAIL if FAIL in levels else WARN if WARN in levels else OK


def result(pixel_id: str, checks: list[dict[str, str]], *, last_fired_time: str = "",
           events: dict[str, Any] | None = None, landing_url: str = "") -> dict[str, Any]:
    """The structured result the payload carries (no token, no response body)."""
    try:
        host = urlsplit(landing_url).hostname or ""
    except ValueError:
        host = ""
    return {"pixel_id": pixel_id, "status": status_of(checks), "checks": checks,
            "last_fired_time": last_fired_time[:40], "events_7d": events,
            "landing_host": host[:253]}


def _page_problem(code: str) -> dict[str, str]:
    if code in ("bad_url", "private_address"):
        return entry("landing_refused", WARN, f"the landing page was not fetched ({code})")
    return entry("landing_unreachable", WARN, f"the landing page could not be read ({code})")


def landing_check(fetcher: Any, pixel_id: str, landing_url: str) -> dict[str, str]:
    """One check entry for the landing page (never ``fail``: see the module docstring)."""
    try:
        html = fetcher.fetch(landing_url).decode("utf-8", errors="replace")
    except PageError as exc:
        return _page_problem(exc.code)
    except Exception:  # noqa: BLE001 - the landing page never fails the whole check
        return _page_problem("unreachable")
    if find_pixel(html, pixel_id):
        return entry("landing_found", OK, "the pixel id is in the landing page's HTML")
    return entry("landing_not_found", WARN, "the pixel id is not in the landing page's HTML "
                                            "(it may be loaded by a tag manager)")


def events_of(body: Mapping[str, Any]) -> dict[str, Any]:
    """``{"total", "by_event"}`` from ``GET /{pixel_id}/stats?aggregation=event``: a list
    of time buckets each holding ``data: [{value, count}]``, or the pairs directly."""
    by_event: dict[str, int] = {}

    def add(item: Any) -> None:
        if not isinstance(item, Mapping):
            return
        name, count = item.get("value"), item.get("count")
        if isinstance(count, str) and count.isdigit():
            count = int(count)
        if isinstance(name, str) and _EVENT_NAME.fullmatch(name) and isinstance(
                count, int) and not isinstance(count, bool) and count >= 0:
            by_event[name] = by_event.get(name, 0) + count

    data = body.get("data")
    for bucket in data if isinstance(data, list) else []:
        inner = bucket.get("data") if isinstance(bucket, Mapping) else None
        if isinstance(inner, list):
            for item in inner:
                add(item)
        else:
            add(bucket)
    top = dict(sorted(by_event.items(), key=lambda kv: (-kv[1], kv[0]))[:12])
    return {"total": sum(by_event.values()), "by_event": top}


def activity_checks(last_fired_time: str, events: dict[str, Any] | None, event_type: str,
                    now: float) -> list[dict[str, str]]:
    """``last_fired_time`` within 7 days, events in 7 days, the campaign's event seen."""
    from datetime import datetime

    out = []
    if not last_fired_time:
        out.append(entry("never_fired", WARN, "the pixel has never sent an event"))
    else:
        try:
            ts = datetime.strptime(last_fired_time, "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            ts = None
        if ts is None:
            out.append(entry("fired_unknown", WARN, "the pixel's last event time is unreadable"))
        elif now - ts > WINDOW_S:
            out.append(entry("no_recent_events", WARN, "no event in the last 7 days"))
        else:
            out.append(entry("fired_recently", OK, "the pixel sent an event in the last 7 days"))
    if events is None:
        out.append(entry("stats_unknown", WARN, "event counts for the last 7 days are unknown"))
        return out
    if events["total"] == 0:
        out.append(entry("no_events_7d", WARN, "no events counted in the last 7 days"))
    else:
        out.append(entry("events_7d", OK, f"{events['total']} events in the last 7 days"))
    name = EVENT_NAMES.get(event_type, "")
    if name:
        if events["by_event"].get(name, 0) > 0:
            out.append(entry("event_seen", OK, f"the campaign's event ({name}) was seen"))
        else:
            out.append(entry("event_not_seen", WARN,
                             f"the campaign's event ({name}) was not seen in 7 days"))
    return out


def unverified(pixel_id: str, reason: str, landing_url: str = "") -> dict[str, Any]:
    """A failed result when the pixel itself could not be read."""
    return result(pixel_id, [entry("pixel_unverified", FAIL,
                                   f"the pixel could not be checked ({reason[:60]})")],
                  landing_url=landing_url)


# -- gate -----------------------------------------------------------------------------


def signed(check: Mapping[str, Any]) -> dict[str, Any]:
    """The check as the ``campaign.create`` payload carries it: ``status`` becomes
    ``verdict`` (``check_paused`` refuses any ``status`` but PAUSED anywhere in a creation
    payload, and the walker stays strict)."""
    out = {k: v for k, v in check.items() if k != "status"}
    out["verdict"] = check.get("status")
    return out


def pixel_of(spec: Any) -> str:
    """The pixel a delivery block's ad set promotes ("" for objectives without one)."""
    if not isinstance(spec, Mapping):
        return ""
    promoted = spec.get("promoted_object")
    return str(promoted.get("pixel_id") or "") if isinstance(promoted, Mapping) else ""


def gate(payload: Mapping[str, Any]) -> str:
    """"" when a ``campaign.create`` payload may be signed and executed; else the refusal
    code: ``pixel_check_missing`` (a pixel campaign without a check),
    ``pixel_check_mismatch`` (a check of another pixel) or ``pixel_check_failed``."""
    pid = pixel_of(payload.get("delivery"))
    if not pid:
        return ""
    check = payload.get("pixel_check")
    if not isinstance(check, Mapping):
        return "pixel_check_missing"
    if check.get("pixel_id") != pid:
        return "pixel_check_mismatch"
    if check.get("verdict") not in (OK, WARN):
        return "pixel_check_failed"
    return ""
