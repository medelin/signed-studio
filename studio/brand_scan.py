"""Brand scan: what the advertiser's own website says, before the brief is drafted.

A brief written in a car is short; the shop's site already says what it sells, at what
price, in which tone, and where else the brand lives. ``scan(url)`` reads the home page and
at most ``MAX_PAGES - 1`` same-site pages that look like *about*, *shop* or *contact*
pages, through ``studio.meta.pixel.Fetcher`` (https only, every address public, a size and
time cap, no cookies), and keeps per page: title, description, Open Graph tags, the visible
text (capped) and schema.org JSON-LD offers (``Product``/``Offer`` name, price, currency)
and the organisation's ``sameAs`` profiles; plus the brand's social profiles linked from
the pages (Facebook, Instagram, TikTok, YouTube, X, LinkedIn, Pinterest, Threads).

Social profiles found on the site are listed, not fetched. The profiles the owner gives on
the studio page (``owner_sources``: Facebook, Instagram, X, each checked for https and its
own host) are read as the public page only, meta tags only, never with a login
(``read_profiles``: the same ``Fetcher``, then the Open Graph title, description and image
address); a page behind a login or consent wall, or one that fails, falls back to the
handle and address as a named fact (``with_profiles``).

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
        self.image = ""  # og:image, read only for an owner's profile page
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
            elif key == "og:image" and a.get("content"):
                self.image = a["content"][:500]
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


# -- the links the owner gives on the studio page ---------------------------------------

# profile field → (the host of the address kept, the hosts it accepts, any subdomain)
PROFILE_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "facebook": ("facebook.com", ("facebook.com", "fb.com")),
    "instagram": ("instagram.com", ("instagram.com",)),
    "x": ("x.com", ("x.com", "twitter.com")),
}
MAX_SOURCE = 300  # characters per field, as the profile's website
MAX_META = 300  # characters kept of a profile page's title or description
_HANDLE = re.compile(r"[\w.\-]{1,100}")
_SCHEME = re.compile(r"[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
# a public profile page that is really a login wall or a consent page
_WALL = re.compile(r"\blog ?in\b|\bsign ?(?:up|in)\b|zaloguj|zarejestruj|before you "
                   r"continue|zanim przejdziesz", re.IGNORECASE)


def _https(value: str) -> tuple[str | None, str]:
    """(``value`` as an https URL, "") — a bare ``host/path`` gets the scheme — or
    (None, ``not_https`` for another scheme, else ``bad_url``)."""
    if _SCHEME.match(value) and not value.lower().startswith("https://"):
        return None, "not_https"
    try:
        url = normalize_url(value)
    except ValueError:  # a malformed host or port
        url = None
    if url is None or "." not in (urlsplit(url).hostname or ""):
        return None, "bad_url"
    return url, ""


def _profile(kind: str, value: str) -> tuple[dict[str, str], str]:
    """({"handle", "url"}, "") for the owner's profile on ``kind``, or ({}, error code).
    ``@name`` stands for the profile's address; the kept address is rebuilt from the
    handle, so nothing but the handle comes from the owner."""
    home, hosts = PROFILE_FIELDS[kind]
    if value.startswith("@"):
        value = f"https://{home}/{value[1:]}"
    url, error = _https(value)
    if url is None:
        return {}, error
    host = urlsplit(url).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in hosts):
        return {}, "wrong_site"
    handle = next((p for p in urlsplit(url).path.split("/") if p), "")
    if not _HANDLE.fullmatch(handle) or handle.endswith(".php"):
        return {}, "no_handle"
    return {"handle": handle, "url": f"https://{home}/{handle}"}, ""


def owner_sources(body: Any) -> tuple[str, dict[str, dict[str, str]], dict[str, str]]:
    """The website and social profiles the owner gives above the brief (``website``,
    ``facebook``, ``instagram``, ``x`` in a draft request): (the https site to scan or "",
    {kind: {"handle", "url"}}, {field: error code}). Only https; each profile field takes
    its own network's addresses (``PROFILE_FIELDS``) and must name a page or handle; the
    website may not be a social profile. Empty fields are no source."""
    website, profiles, errors = "", {}, {}
    for field in ("website", *PROFILE_FIELDS):
        raw = body.get(field) if isinstance(body, dict) else None
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if not isinstance(raw, str) or len(raw) > MAX_SOURCE or any(
                c.isspace() or ord(c) < 0x20 for c in raw.strip()):
            errors[field] = "bad_url"
        elif field == "website":
            url, error = _https(raw.strip())
            if url and social_kind(url):
                error = "social_as_website"
            if error:
                errors[field] = error
            else:
                website = str(url)
        else:
            found, error = _profile(field, raw.strip())
            if error:
                errors[field] = error
            else:
                profiles[field] = found
    return website, profiles, errors


def read_profile(kind: str, named: dict[str, str], fetcher: Any) -> dict[str, Any]:
    """The owner's public profile page, read like the website (``fetcher``: https, public
    address, size and time caps, no cookies, never a login): only its title, description
    and image address from the meta tags. ``read`` is False, with ``blocked`` True, when
    the page fails, serves no meta tags or is a login or consent wall: the profile is then
    only named."""
    out: dict[str, Any] = {**named, "kind": kind, "read": False, "blocked": True,
                           "title": "", "description": "", "image": ""}
    try:
        body = fetcher.fetch(named["url"])
    except PageError:
        return out
    page = _Page()
    try:
        page.feed(body.decode("utf-8", "replace"))
        page.close()
    except (AssertionError, ValueError):
        pass
    meta = page.meta
    title = " ".join((meta.get("og:title") or page.title).split())[:MAX_META]
    description = " ".join((meta.get("og:description") or meta.get("description") or ""
                            ).split())[:MAX_META]
    image = normalize_url(page.image) if page.image else None
    platform = PROFILE_FIELDS[kind][0].split(".")[0]
    if not (meta.get("og:title") or meta.get("og:description")) or \
            _WALL.search(title) or title.casefold() in (platform, "x", "twitter", ""):
        return out
    return {**out, "read": True, "blocked": False, "title": title,
            "description": description, "image": image or ""}


def read_profiles(profiles: dict[str, dict[str, str]], fetcher: Any | None = None
                  ) -> dict[str, dict[str, Any]]:
    """``read_profile`` for each profile the owner gave."""
    fetcher = fetcher or Fetcher()
    return {kind: read_profile(kind, named, fetcher) for kind, named in profiles.items()}


def named_only(profiles: dict[str, dict[str, str]]) -> dict[str, dict[str, Any]]:
    """The profiles as named facts, not read (the scan is off): never reported blocked."""
    return {kind: {**named, "kind": kind, "read": False, "blocked": False, "title": "",
                   "description": "", "image": ""} for kind, named in profiles.items()}


def with_profiles(result: dict[str, Any] | None, profiles: dict[str, dict[str, Any]]
                  ) -> dict[str, Any] | None:
    """A scan result (or an empty one, when there is no site) carrying the owner's
    profiles (``owner_social``, from ``read_profiles`` or ``named_only``); ``result``
    itself when there are none."""
    if not profiles:
        return result
    base = result or {"url": "", "pages": [], "social": {}, "errors": []}
    return {**base, "owner_social": profiles}


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
    for kind, p in result.get("owner_social", {}).items():
        if p["read"]:
            lines.append(f"The owner's own {kind} profile @{p['handle']} {p['url']}")
            lines += [f"  {key}: {p[key]}" for key in ("title", "description", "image")
                      if p[key]]
        else:
            lines.append(f"The owner's own {kind} profile (named by the owner, not read): "
                         f"@{p['handle']} {p['url']}")
    if not lines:
        return ""
    return envelope.wrap("\n".join(lines), "website", max_chars=12000)


def grounding_text(result: dict[str, Any]) -> str:
    """What the site states, for ``brief.ground_draft`` (numbers it names are grounded)."""
    parts = []
    for page in result.get("pages", []):
        parts += [page["title"], *page["meta"].values(), page["text"]]
        parts += [f"{o['name']} {o['price']} {o['currency']}" for o in page["offers"]]
    for p in result.get("owner_social", {}).values():
        parts += [p["title"], p["description"]]
    return "\n".join(parts)
