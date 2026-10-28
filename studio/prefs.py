"""Viewer preferences chosen in the header, server side and without JavaScript.

``?lang=en|pl`` picks the language and remembers it in a ``lang`` cookie (one year,
SameSite=Lax); without the parameter the cookie decides, else English. The choice is put in
``request.state.lang`` before any route runs, so templates render ``<html lang>`` and every
string in the right language on the first byte.
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
CHOICES: dict[str, tuple[tuple[str, ...], str]] = {"lang": (i18n.LANGS, i18n.DEFAULT)}


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
