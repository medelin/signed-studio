"""Viewer preferences chosen in the header, server side and without JavaScript.

``?lang=en|pl``, ``?theme=system|light|dark`` and ``?palette=graphite|warm|green|contrast``
are remembered in cookies of the same names (one year, SameSite=Lax); without a parameter
the cookie decides, else the default. The choices are put in ``request.state`` before any
route runs, so templates render ``<html lang data-theme data-palette>`` on the first byte:
no script decides the theme, so there is no flash of the wrong one (the CSP forbids inline
scripts anyway).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import Request

from studio.ui import i18n

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

YEAR_S = 365 * 24 * 3600
# preference -> (allowed values, default)
THEMES = ("system", "light", "dark")
PALETTES = ("graphite", "warm", "green", "contrast")
CHOICES: dict[str, tuple[tuple[str, ...], str]] = {
    "lang": (i18n.LANGS, i18n.DEFAULT),
    "theme": (THEMES, "system"),  # light/dark mode; system follows prefers-color-scheme
    "palette": (PALETTES, "graphite"),
}


class Prefs:
    """ASGI middleware: read each preference from the query or its cookie, keep it in
    ``scope["state"]`` and set the cookie when the query changed it."""

    def __init__(self, app: ASGIApp, secure: Callable[[Request], bool]) -> None:
        self.app = app
        self.secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        state = scope.setdefault("state", {})
        changed: list[tuple[str, str]] = []
        for name, (allowed, default) in CHOICES.items():
            asked = (query.get(name) or [""])[0]
            cookie = request.cookies.get(name, "")
            value = asked if asked in allowed else cookie if cookie in allowed else default
            state[name] = value
            if asked in allowed and asked != cookie:
                changed.append((name, value))
        if not changed:
            await self.app(scope, receive, send)
            return
        flags = f"; Path=/; Max-Age={YEAR_S}; SameSite=Lax" + (
            "; Secure" if self.secure(request) else "")

        async def with_cookies(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [(b"set-cookie", f"{n}={v}{flags}".encode()) for n, v in changed]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_cookies)
