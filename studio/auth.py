"""The gate in front of the whole app: login requirement, CSRF and fetch-metadata checks.

- ``STUDIO_REQUIRE_LOGIN=1`` (default with ``STUDIO_SANDBOX=1``): every page and API needs a
  judge session except ``/login``, ``/healthz``, static assets and the public verification
  material ``studio verify --server`` fetches (``/api/ledger-key``, ``/api/ledger-manifest``,
  ``/api/approver-keys``) and the recorded tour (``/try``, ``/pl/try``, ``/try/data.json``,
  ``/try/img/``) and the WhatsApp webhook (``/webhooks/whatsapp``, HMAC-signed, the
  one CSRF-exempt path). Unauthenticated page requests are redirected to
  ``/login?next=<same-site path>``; API and asset requests get 401 JSON.
- State-changing requests from a browser must be same-origin by Fetch Metadata
  (``Sec-Fetch-Site``); the existing ``Origin`` check in the routes stays.
- CSRF: a random token in an HttpOnly, SameSite=Strict cookie (double submit). Requests whose
  body a cross-site HTML form could send (urlencoded, multipart, text/plain) must echo it:
  as the ``csrf`` form field on ``/login``, ``/login/2fa``, ``/logout`` and the ``/account/``
  forms (checked there), as the ``X-CSRF-Token`` header everywhere else. JSON and raw uploads
  cannot be sent cross-site without a CORS preflight, which this app never grants.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

CSRF_COOKIE = "studio_csrf"
CSRF_FIELD = "csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORM_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data",
                        "text/plain"})
FORM_CHECKED_IN_ROUTE = frozenset({"/login", "/logout", "/login/2fa"})
FORM_CHECKED_PREFIXES = ("/account/",)  # studio.routes_account checks the csrf field
PUBLIC_PATHS = frozenset({"/login", "/logout", "/login/2fa", "/healthz", "/favicon.ico",
                          "/privacy", "/data-deletion",
                          "/api/ledger-key", "/api/ledger-manifest", "/api/approver-keys",
                          "/try", "/pl/try", "/try/data.json",  # the recorded tour
                          "/start", "/pl/start",  # onboarding
                          "/webhooks/whatsapp"})  # Meta's servers; authenticated by HMAC
PUBLIC_PREFIXES = ("/static/", "/try/img/")
# Posted by Meta's servers, never by a browser: the X-Hub-Signature-256 HMAC of the raw body
# (checked in studio.routes_whatsapp) is the authentication, so no CSRF token is asked for.
CSRF_EXEMPT_PATHS = frozenset({"/webhooks/whatsapp"})
JSON_PREFIXES = ("/api/", "/assets/")


def safe_next(value: str | None) -> str:
    """A same-site relative path to return to after login, or ``/``. Refuses absolute and
    scheme-relative URLs, backslashes, control characters and the login page itself."""
    if not value or len(value) > 512 or not value.startswith("/"):
        return "/"
    if value.startswith(("//", "/\\")) or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or parts.path.startswith("/login"):
        return "/"
    return value


def page_path(path: str) -> str:
    """The request path made safe to echo into a link: one leading ``/``, no ``//``, no
    backslash or control characters (else ``/``). Never a protocol-relative URL."""
    if (not path.startswith("/") or path.startswith("//") or "\\" in path
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path)):
        return "/"
    return path


def query_with(query: str, **changes: str) -> str:
    """``?...`` with ``changes`` applied to the current query string; a query-only href
    resolves against the current page, so it can never point at another site."""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in changes]
    return "?" + urlencode(pairs + list(changes.items()))


def query_pairs(query: str, *drop: str) -> list[tuple[str, str]]:
    """The current query parameters without ``drop`` (hidden fields of a GET form)."""
    return [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in drop]


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def tokens_match(cookie: str | None, sent: str | None) -> bool:
    return bool(cookie and sent) and hmac.compare_digest(str(cookie).encode(),
                                                         str(sent).encode())


class Gate:
    """ASGI middleware (pure, so SSE streams pass through untouched)."""

    def __init__(self, app: ASGIApp, *, require_login: Callable[[], bool],
                 session_of: Callable[[Request], str | None], secure: Callable[[Request], bool]
                 ) -> None:
        self.app = app
        self.require_login = require_login
        self.session_of = session_of
        self.secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        cookie = request.cookies.get(CSRF_COOKIE)
        token = cookie if cookie and 20 <= len(cookie) <= 100 else secrets.token_urlsafe(32)
        scope.setdefault("state", {})["csrf"] = token

        if method not in SAFE_METHODS:
            site = request.headers.get("sec-fetch-site")
            if site is not None and site not in ("same-origin", "none"):
                await JSONResponse({"error": "cross_site"}, 403)(scope, receive, send)
                return
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if (ctype in FORM_TYPES and path not in FORM_CHECKED_IN_ROUTE
                    and path not in CSRF_EXEMPT_PATHS
                    and not path.startswith(FORM_CHECKED_PREFIXES)
                    and not tokens_match(cookie, request.headers.get(CSRF_HEADER))):
                await JSONResponse({"error": "csrf"}, 403)(scope, receive, send)
                return

        if self.require_login() and not is_public(path) and self.session_of(request) is None:
            if path.startswith(JSON_PREFIXES) or method not in SAFE_METHODS:
                response: JSONResponse | RedirectResponse = JSONResponse(
                    {"error": "login_required"}, 401)
            else:
                target = path + ("?" + scope["query_string"].decode("latin-1")
                                 if scope.get("query_string") else "")
                response = RedirectResponse("/login?next=" + quote(safe_next(target), safe="/"),
                                            303)
            await response(scope, receive, send)
            return

        if cookie == token:
            await self.app(scope, receive, send)
            return
        secure = self.secure(request)

        async def with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                flags = "; Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", f"{CSRF_COOKIE}={token}{flags}".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_cookie)
