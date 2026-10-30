"""Brand scan: what the advertiser's own website says, before the brief is drafted.

A brief written in a car is short; the shop's site already says what it sells, at what
price, in which tone, and where else the brand lives. ``scan(url)`` reads the home page and
at most ``MAX_PAGES - 1`` same-site pages that look like *about*, *shop* or *contact*
pages, through ``studio.meta.pixel.Fetcher`` (https only, every address public, a size and
time cap, no cookies), and keeps per page: title, description, Open Graph tags, the visible
text (capped) and schema.org JSON-LD offers (``Product``/``Offer`` name, price, currency)
and the organisation's ``sameAs`` profiles; plus the brand's social profiles linked from
the pages (Facebook, Instagram, TikTok, YouTube, X, LinkedIn, Pinterest, Threads).

Social profiles are listed, never fetched: Meta's and others' profile pages need a login
or an API grant the studio does not have for third parties.

``as_prompt(result)`` is how a scan enters the draft prompt: inside the untrusted-content
envelope (source ``website``), as suggestions the owner confirms with the rest of the brief;
``grounding_text`` lets numbers the site states (a price) be drafted as facts instead of
questions. A ``brand.scan`` receipt records the URLs and the hash of the result.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from studio import envelope
from studio.meta.pixel import Fetcher, PageError

MAX_PAGES = 4
MAX_TEXT = 2500  # visible characters kept per page
MAX_OFFERS = 12
SOCIAL_HOSTS = {
    "facebook.com": "facebook", "fb.com": "facebook", "instagram.com": "instagram",
    "tiktok.com": "tiktok", "youtube.com": "youtube", "youtu.be": "youtube", "x.com": "x",
    "twitter.com": "x", "linkedin.com": "linkedin", "pinterest.com": "pinterest",
    "threads.net": "threads", "threads.com": "threads",
}
# paths worth a second look, in any of the studio's languages
INTERESTING = re.compile(r"about|o-nas|onas|o_nas|story|historia|shop|sklep|store|product|"
                         r"produkt|oferta|offer|collection|kolekcj|contact|kontakt|faq",
                         re.IGNORECASE)
SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "iframe"})
URL_IN_TEXT = re.compile(r"https://[^\s<>\"')\]]+", re.IGNORECASE)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.jsonld: list[str] = []
        self.text: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_jsonld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self.jsonld.append("")
            return
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key in ("description", "og:title", "og:description", "og:site_name",
                       "og:type", "og:locale") and a.get("content"):
                self.meta[key] = a["content"][:500]
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "html" and a.get("lang"):
            self.meta["lang"] = a["lang"][:12]

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
        elif tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self.jsonld[-1] += data
        elif self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.text.append(" ".join(data.split()))


def normalize_url(url: str) -> str | None:
    """An https URL to scan (scheme added to a bare domain), without fragment, else None."""
    url = url.strip().rstrip(".,;")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def first_site(text: str) -> str | None:
    """The first https URL in the owner's words that is not a social profile."""
    for match in URL_IN_TEXT.finditer(text):
        url = normalize_url(match.group(0))
        if url and social_kind(url) is None:
            return url
    return None


def social_kind(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    for domain, kind in SOCIAL_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return kind
    return None


def _offers(blob: Any, out: list[dict[str, str]], same_as: list[str]) -> None:
    if isinstance(blob, list):
        for item in blob:
            _offers(item, out, same_as)
        return
    if not isinstance(blob, dict):
        return
    kind = blob.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    if any(k in ("Organization", "LocalBusiness", "Store", "OnlineStore", "Brand")
           for k in kinds):
        links = blob.get("sameAs")
        same_as.extend(x for x in (links if isinstance(links, list) else [links])
                       if isinstance(x, str))
    if "Product" in kinds and len(out) < MAX_OFFERS:
        offer = blob.get("offers")
        offer = offer[0] if isinstance(offer, list) and offer else offer
        price = offer.get("price", "") if isinstance(offer, dict) else ""
        currency = offer.get("priceCurrency", "") if isinstance(offer, dict) else ""
        name = blob.get("name")
        if isinstance(name, str) and name.strip():
            out.append({"name": name.strip()[:120], "price": str(price)[:20],
                        "currency": str(currency)[:3]})
    for key in ("@graph", "itemListElement", "item", "mainEntity"):
        if key in blob:
            _offers(blob[key], out, same_as)


def read_page(url: str, html: str) -> tuple[dict[str, Any], list[str], list[str]]:
    """One page's summary, the same-site links found and the social profiles."""
    p = _Page()
    try:
        p.feed(html)
        p.close()
    except (AssertionError, ValueError):  # malformed markup: keep what was read
        pass
    offers: list[dict[str, str]] = []
    same_as: list[str] = []
    for raw in p.jsonld:
        try:
            _offers(json.loads(raw), offers, same_as)
        except (ValueError, RecursionError):
            continue
    text = " ".join(p.text)[:MAX_TEXT]
    host = urlsplit(url).hostname
    internal, social = [], []
    for href in [*p.links, *same_as]:
        target = normalize_url(urljoin(url, href))
        if not target:
            continue
        if social_kind(target):
            social.append(target)
        elif urlsplit(target).hostname == host:
            internal.append(target)
    page = {"url": url, "title": " ".join(p.title.split())[:200], "meta": p.meta,
            "text": text, "offers": offers}
    return page, internal, social


def scan(url: str, fetcher: Any | None = None) -> dict[str, Any]:
    """Read the site (see the module docstring). Never raises for a page that fails: the
    result lists it under ``errors`` with the fetcher's code."""
    start = normalize_url(url)
    if start is None:
        return {"url": url, "pages": [], "social": {}, "errors": [{"url": url,
                                                                    "code": "bad_url"}]}
    fetcher = fetcher or Fetcher()
    pages: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    social: dict[str, str] = {}
    queue, seen = [start], {start}
    while queue and len(pages) < MAX_PAGES:
        current = queue.pop(0)
        try:
            body = fetcher.fetch(current)
        except PageError as exc:
            errors.append({"url": current, "code": exc.code})
            continue
        page, internal, found = read_page(current, body.decode("utf-8", "replace"))
        pages.append(page)
        for link in found:
            social.setdefault(social_kind(link) or "", link)
        if current == start:  # the home page picks the follow-ups
            picks = [u for u in dict.fromkeys(internal)
                     if u not in seen and INTERESTING.search(urlsplit(u).path)]
            for u in picks[:MAX_PAGES - 1]:
                seen.add(u)
                queue.append(u)
    social.pop("", None)
    return {"url": start, "pages": pages, "social": social, "errors": errors}


def as_prompt(result: dict[str, Any]) -> str:
    """The scan as the draft prompt sees it: an envelope of plain lines."""
    lines: list[str] = []
    for page in result.get("pages", []):
        lines.append(f"Page {page['url']}: {page['title']}")
        for key, value in page["meta"].items():
            lines.append(f"  {key}: {value}")
        for offer in page["offers"]:
            lines.append(f"  product: {offer['name']} {offer['price']} {offer['currency']}")
        if page["text"]:
            lines.append(f"  text: {page['text']}")
    for kind, link in result.get("social", {}).items():
        lines.append(f"Profile {kind}: {link}")
    if not lines:
        return ""
    return envelope.wrap("\n".join(lines), "website", max_chars=12000)


def grounding_text(result: dict[str, Any]) -> str:
    """What the site states, for ``brief.ground_draft`` (numbers it names are grounded)."""
    parts = []
    for page in result.get("pages", []):
        parts += [page["title"], *page["meta"].values(), page["text"]]
        parts += [f"{o['name']} {o['price']} {o['currency']}" for o in page["offers"]]
    return "\n".join(parts)
