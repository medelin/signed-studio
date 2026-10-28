"""FastAPI app: public read-only views, judge login, approver keys and receipt export.

Security notes:
- the login attempt is reserved in the per-prefix limiter *before* the body is awaited, so
  concurrent requests cannot all slip past the check; the body must arrive within
  ``LOGIN_BODY_TIMEOUT_S``; only completed attempts count towards the short, capped global
  pause (see ``LoginLimiter``); success clears the client's bucket;
- credentials are compared as bytes in constant time (non-ASCII input is a 401, not a 500);
- sessions are HMAC-signed with the dedicated ``SESSION_SECRET`` and also tracked server
  side, so ``POST /logout`` revokes them; cookies are HttpOnly, SameSite=Strict, Secure;
- ``X-Forwarded-For`` is used only when the direct peer is a ``TRUSTED_PROXY``, and then
  only its rightmost element (the address that proxy saw);
- request bodies are capped (413), state-changing requests must be same-origin, and the
  ``STUDIO_READ_ONLY`` kill switch turns every write into a 503.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from studio import (
    auth,
    db,
    routes_help,
    routes_inbox,
    routes_receipts,
    routes_report,
    routes_studio,
    sandbox,
)
from studio.agents import community, media_buyer
from studio.approvals import KeyConflict, Keys
from studio.meta import adapter as meta_adapter
from studio.receipts import public_key_raw, sha256_hex
from studio.settings import ReadOnlyMode, Settings

UI = Path(__file__).resolve().parent / "ui"
LOG = logging.getLogger("studio.login")
COOKIE = "studio_session"
SESSION_TTL_S = 8 * 3600
MAX_BODY = 64 * 1024
UPLOAD_LIMITS = {"/api/briefs/voice": 32 * 1024 * 1024, "/api/reports": 6 * 1024 * 1024}
JUDGE_USER = "judge"
MAX_KEYS_PER_SESSION = 5
LOGIN_BODY_TIMEOUT_S = 5.0  # a login body slower than this is dropped (408)
LOGIN_FAILED = "Sign-in failed. Check the user name and password, or try again in a minute."
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-src https://www.facebook.com; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


# -- client address and login limiter ---------------------------------------------------


def client_ip(request: Any, trusted_proxies: frozenset[str]) -> str:
    """Direct peer, or the rightmost valid ``X-Forwarded-For`` entry behind a trusted proxy."""
    peer = request.client.host if request.client else "unknown"
    if peer not in trusted_proxies:
        return str(peer)
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
    for candidate in reversed([p for p in parts if p]):
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            return str(peer)  # the proxy appended garbage: do not trust anything further
    return str(peer)


def limiter_key(ip: str) -> str:
    """Bucket for an address: IPv4 /32, IPv6 /64 (one subscriber's prefix)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((addr, 64), strict=False))
    return str(addr)


class LoginLimiter:
    """Per-prefix attempt limit plus a short, capped global pause for the judge account.

    - ``reserve`` counts the attempt atomically for the client's prefix *before* the body
      is read (concurrent requests cannot all pass) and refuses while the account is paused;
    - ``settle`` is called after the body was read and the password compared, with no
      ``await`` in between: only completed attempts count towards the global budget, so
      requests in flight (or slow bodies, which time out) never use it up;
    - ``GLOBAL_MAX_FAILURES`` failures from any addresses within ``GLOBAL_WINDOW_S`` pause
      the account for ``BASE_LOCK_S * 2**(strikes-1)`` seconds, never more than
      ``MAX_LOCK_S``; strikes are forgotten after ``STRIKE_DECAY_S`` without a pause. So the
      global limit is a slow rate limit (at most 20 guesses per pause), not a hard lockout:
      however long a failure storm lasts, a correct login gets through at most
      ``MAX_LOCK_S`` after it stops;
    - under table pressure, expired then low-count unblocked entries are evicted; blocked
      prefixes are never evicted, and when only blocked entries remain, new clients are
      refused (fail closed).
    """

    GLOBAL_MAX_FAILURES = 20
    GLOBAL_WINDOW_S = 300.0
    BASE_LOCK_S = 5.0
    MAX_LOCK_S = 60.0
    STRIKE_DECAY_S = 900.0
    DAILY_WARN_FAILURES = 500  # failures per 24 h before a loud warning (never a lock)

    def __init__(self, max_attempts: int = 5, window_s: float = 900, max_clients: int = 10_000):
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.max_clients = max_clients
        self._hits: dict[str, list[float]] = {}
        self._failures: list[float] = []
        self._strikes = 0
        self._last_pause = 0.0
        self.locked_until = 0.0
        self._hourly: dict[int, int] = {}  # failures per hour bucket, last 24 h
        self._warned = False
        self._lock = threading.Lock()

    def _daily(self, t: float) -> int:
        hour = int(t // 3600)
        for h in [h for h in self._hourly if hour - h >= 24]:
            del self._hourly[h]
        return sum(self._hourly.values())

    def failures_24h(self, now: float | None = None) -> int:
        """Failed logins in the last ~24 h (hour buckets): a metric, not a limit."""
        with self._lock:
            return self._daily(time.time() if now is None else now)

    def __len__(self) -> int:
        return len(self._hits)

    def _recent(self, hits: list[float], t: float) -> list[float]:
        return [h for h in hits if t - h < self.window_s]

    def _make_room(self, t: float) -> bool:
        for k in [k for k, v in self._hits.items() if not self._recent(v, t)]:
            del self._hits[k]
        if len(self._hits) < self.max_clients:
            return True
        unblocked = [(len(self._recent(v, t)), k) for k, v in self._hits.items()
                     if len(self._recent(v, t)) < self.max_attempts]
        if not unblocked:
            return False
        del self._hits[min(unblocked)[1]]
        return True

    def reserve(self, ip: str, now: float | None = None) -> bool:
        """Atomically count one attempt for the prefix; False (429) when over any limit."""
        t = time.time() if now is None else now
        key = limiter_key(ip)
        with self._lock:
            if t < self.locked_until:
                return False
            if key not in self._hits and not self._make_room(t):
                return False
            hits = self._recent(self._hits.get(key, []), t)
            if len(hits) >= self.max_attempts:
                self._hits[key] = hits
                return False
            hits.append(t)
            self._hits[key] = hits
            return True

    def retry_after(self, ip: str, now: float | None = None) -> int:
        """Seconds until this client may try again (global pause or its own window)."""
        t = time.time() if now is None else now
        with self._lock:
            waits = [self.locked_until - t]
            hits = self._recent(self._hits.get(limiter_key(ip), []), t)
            if len(hits) >= self.max_attempts:
                waits.append(hits[0] + self.window_s - t)
        return max(1, math.ceil(max(waits)))

    def settle(self, ip: str, ok: bool, now: float | None = None) -> bool:
        """Record a completed attempt. False (429, nothing recorded) while paused."""
        t = time.time() if now is None else now
        with self._lock:
            if t < self.locked_until:
                return False
            if ok:
                self._hits.pop(limiter_key(ip), None)
                return True
            self._failures = [f for f in self._failures if t - f < self.GLOBAL_WINDOW_S]
            self._failures.append(t)
            hour = int(t // 3600)
            self._hourly[hour] = self._hourly.get(hour, 0) + 1
            daily = self._daily(t)
            if daily > self.DAILY_WARN_FAILURES and not self._warned:
                self._warned = True
                LOG.warning("LOGIN ATTACK? %d failed judge logins in 24 h (threshold %d); "
                            "no lock applied, check the password strength and the logs",
                            daily, self.DAILY_WARN_FAILURES)
            elif daily <= self.DAILY_WARN_FAILURES:
                self._warned = False
            if len(self._failures) >= self.GLOBAL_MAX_FAILURES:
                if t - self._last_pause > self.STRIKE_DECAY_S:
                    self._strikes = 0
                self._strikes = min(self._strikes + 1, 16)
                pause = min(self.BASE_LOCK_S * 2 ** (self._strikes - 1), self.MAX_LOCK_S)
                self.locked_until = t + pause
                self._last_pause = t
                self._failures = []
            return True

    def clear(self, ip: str) -> None:
        with self._lock:
            self._hits.pop(limiter_key(ip), None)


# -- sessions ---------------------------------------------------------------------------


class Sessions:
    """HMAC-signed session cookies (``sid.exp.mac``) plus a server-side revocation set."""

    def __init__(self, secret: bytes, max_sessions: int = 1000) -> None:
        if len(secret) < 32:
            raise ValueError("session secret too short")
        self._secret = secret
        self._active: OrderedDict[str, float] = OrderedDict()
        self._max = max_sessions
        self._lock = threading.Lock()

    def _mac(self, sid: str, exp: int) -> str:
        return hmac.new(self._secret, f"v1|{sid}|{exp}".encode(), hashlib.sha256).hexdigest()

    def issue(self, now: float | None = None) -> str:
        exp = int((time.time() if now is None else now) + SESSION_TTL_S)
        sid = secrets.token_urlsafe(24)
        with self._lock:
            self._active[sid] = exp
            while len(self._active) > self._max:
                self._active.popitem(last=False)
        return f"{sid}.{exp}.{self._mac(sid, exp)}"

    def check(self, value: str | None, now: float | None = None) -> str | None:
        """Session id for a valid, unexpired, unrevoked cookie; otherwise None."""
        if not value or value.count(".") != 2:
            return None
        sid, exp_s, mac = value.split(".")
        if not exp_s.isdigit():
            return None
        exp = int(exp_s)
        if not hmac.compare_digest(mac.encode(), self._mac(sid, exp).encode()):
            return None
        if exp <= (time.time() if now is None else now):
            return None
        with self._lock:
            return sid if self._active.get(sid) == exp else None

    @staticmethod
    def expiry(value: str) -> int:
        return int(value.split(".")[1])

    def revoke(self, value: str | None) -> None:
        if value and value.count(".") == 2:
            with self._lock:
                self._active.pop(value.split(".")[0], None)


# -- middleware -------------------------------------------------------------------------


class BodyLimit:
    """413 for bodies over ``limit`` bytes, by header or while streaming (chunked)."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY,
                 paths: dict[str, int] | None = None) -> None:
        self.app = app
        self.limit = limit
        self.paths = paths or {}  # exact path -> larger limit (uploads)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.paths.get(scope.get("path", ""), self.limit)
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            await PlainTextResponse("request body too large", 413)(scope, receive, send)
            return
        seen = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await PlainTextResponse("request body too large", 413)(scope, receive, send)


class _TooLarge(Exception):
    pass


def _cookie_secure(request: Request, settings: Settings) -> bool:
    return settings.cookie_secure or request.url.scheme == "https"


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # non-browser client; SameSite=Strict + Sec-Fetch-Site guard browsers
    host = request.headers.get("host", "")
    return origin in {f"https://{host}", f"http://{host}"}


async def _read_credentials(request: Request) -> tuple[str, str, dict[str, str]]:
    """User name, password and the other fields (``csrf``, ``next``; ``_form`` is set for
    bodies a cross-site HTML form could send, which must carry the CSRF token)."""
    raw = await request.body()
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if ctype == "application/json":
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                return "", "", {}
            return str(data.get("username", "")), str(data.get("password", "")), {}
        form = parse_qs(raw.decode("utf-8"), max_num_fields=10)
    except (ValueError, UnicodeDecodeError):
        return "", "", {"_form": "1"}
    extra = {k: v[0] for k, v in form.items() if k in ("csrf", "next") and v}
    if ctype in auth.FORM_TYPES:
        extra["_form"] = "1"
    return (form.get("username") or [""])[0], (form.get("password") or [""])[0], extra


# -- application ------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load(env_file=Path(".env"))
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = db.connect(data_dir / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, data_dir / "ledger.key")
    if conn.execute("SELECT count(*) FROM comments").fetchone()[0] == 0:
        db.seed(conn)  # synthetic demo inbox and leads on a fresh database
    with db.transaction(conn):
        Keys.revoke_all(conn)  # sessions live in process memory: earlier keys are dead

    app = FastAPI(title="Muse Studio Lite", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.conn = conn
    app.state.ledger = ledger
    app.state.limiter = LoginLimiter()
    app.state.sessions = Sessions(settings.session_secret)
    templates = Jinja2Templates(directory=str(UI / "templates"))
    templates.env.globals["replay"] = settings.replay
    app.mount("/static", StaticFiles(directory=str(UI / "static")), name="static")
    app.add_middleware(BodyLimit, paths=UPLOAD_LIMITS)
    app.add_middleware(
        auth.Gate, require_login=lambda: settings.require_login,
        session_of=lambda request: app.state.sessions.check(request.cookies.get(COOKIE)),
        secure=lambda request: _cookie_secure(request, settings))

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def session_of(request: Request) -> str | None:
        return app.state.sessions.check(request.cookies.get(COOKIE))  # type: ignore[no-any-return]

    def write_guard(request: Request) -> JSONResponse | str:
        """Same-origin + session + kill switch; returns the session id or an error."""
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        sid = session_of(request)
        if sid is None:
            return JSONResponse({"error": "login_required"}, 401)
        try:
            settings.require_writable()
        except ReadOnlyMode:
            return JSONResponse({"error": "read_only"}, 503)
        return sid

    routes_studio.register(app, settings, ledger, conn, write_guard)
    routes_inbox.register(app, settings, ledger, conn, write_guard, templates, session_of)
    routes_inbox.register_leads(app, settings, ledger, conn, write_guard, templates, session_of)
    routes_report.register(app, settings, ledger, conn, write_guard, templates, session_of)
    routes_receipts.register(app, settings, ledger, templates, session_of)
    routes_help.register(app, settings, ledger, templates, session_of)

    # The Meta adapter (mock unless META_ADAPTER=graph) and the executors that use it:
    # each runs only for a consumed, signed approval (studio.executor.authorize).
    app.state.adapter = meta_adapter.make(settings, conn=conn)

    def insights() -> dict[str, Any]:
        """Insights of the newest campaign made by this adapter; the synthetic fixture
        (marked ``_synthetic``) when there is none."""
        adapter = app.state.adapter
        with conn.lock:
            row = conn.execute("SELECT external_id FROM campaigns WHERE adapter = ? AND "
                               "external_id != '' ORDER BY created_ts DESC LIMIT 1",
                               (adapter.name,)).fetchone()
        if row is None:
            from studio.meta.mock import MockAdapter
            return MockAdapter().insights("")
        return adapter.insights(row[0])  # type: ignore[no-any-return]

    app.state.insights = insights
    app.state.executors["campaign.create"] = (
        lambda verified, payload: media_buyer.create(conn, ledger, app.state.adapter, verified,
                                                     payload, settings.data_dir))
    app.state.executors["campaign.activate"] = (
        lambda verified, payload: media_buyer.activate(conn, ledger, app.state.adapter,
                                                       verified, payload,
                                                       settings.max_daily_budget_pln))

    @app.get("/api/campaigns/{item_id}")
    def get_campaign(item_id: str, request: Request) -> Response:
        c = media_buyer.campaign(conn, item_id) if routes_inbox.COMMENT_ID.fullmatch(
            item_id) else None
        if c is None:
            return JSONResponse({"error": "not_found"}, 404)
        created = c.pop("created")
        preview = created.get("preview")
        if session_of(request) is None:
            # A visitor gets no Meta object ids and no Meta preview URL (its query string
            # is a short-lived access token); the mock's inline preview carries neither.
            for key in ("external_id", "adset_external_id", "ad_external_id"):
                if c.get(key):
                    c[key] = "sha256:" + sha256_hex(str(c[key]))[:32]
            if not (isinstance(preview, dict) and preview.get("kind") == "inline"):
                preview = None
        return JSONResponse({**c, "preview": preview,
                             "preview_error": created.get("preview_error", ""),
                             "ceiling_pln": settings.max_daily_budget_pln})

    @app.post("/api/campaigns/{item_id}/activate")
    def request_activate(item_id: str, request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        if not routes_inbox.COMMENT_ID.fullmatch(item_id):
            return JSONResponse({"error": "not_found"}, 404)
        try:
            pending, payload = media_buyer.request_activation(
                conn, ledger, item_id, settings.max_daily_budget_pln)
        except media_buyer.BuyError as exc:
            return JSONResponse({"error": exc.code}, 404 if exc.code == "not_found" else 409)
        return JSONResponse({"approval_id": pending.id, "content": payload,
                             "content_sha256": pending.content_sha256}, 201)
    for action in community.SIGNED_ACTIONS:
        app.state.executors[action] = (
            lambda verified, payload: community.send(conn, ledger, app.state.adapter,
                                                     verified, payload))

    @app.get("/studio", response_class=HTMLResponse)
    def studio_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "studio.html",
            {
                "seq": ledger.seq(),
                "epoch": ledger.epoch,
                "logged_in": session_of(request) is not None,
                "login_enabled": settings.judge_login_enabled,
                "read_only": settings.read_only,
                "product_concept": settings.product_concept,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        receipts = ledger.receipts()[-50:][::-1]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "receipts": receipts,
                "seq": ledger.seq(),
                "epoch": ledger.epoch,
                "logged_in": session_of(request) is not None,
                "login_enabled": settings.judge_login_enabled,
                "read_only": settings.read_only,
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "read_only": settings.read_only}  # minimal and public

    def login_html(request: Request, nxt: str, error: str = "", status: int = 200,
                   headers: dict[str, str] | None = None) -> Response:
        return templates.TemplateResponse(request, "login.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch, "logged_in": False,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "next": auth.safe_next(nxt), "error": error, "noindex": True,
        }, status_code=status, headers=headers)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "") -> Response:  # noqa: A002 - query name
        if session_of(request) is not None:
            return RedirectResponse(auth.safe_next(next), 303)
        return login_html(request, next)

    @app.post("/login")
    async def login(request: Request) -> Response:
        html = "text/html" in request.headers.get("accept", "")
        nxt = request.query_params.get("next", "")

        def fail(code: str, status: int, retry: int | None = None) -> Response:
            headers = {"Retry-After": str(retry)} if retry else None
            if html:  # one generic sentence whatever went wrong
                return login_html(request, nxt, LOGIN_FAILED, status, headers)
            return JSONResponse({"error": code}, status, headers=headers)

        if not settings.judge_login_enabled:
            return fail("login_disabled", 403)
        if not _same_origin(request):
            return fail("cross_origin", 403)
        ip = client_ip(request, settings.trusted_proxies)
        if not app.state.limiter.reserve(ip):  # reserved before the body is awaited
            return fail("too_many_attempts", 429, app.state.limiter.retry_after(ip))
        try:
            username, password, fields = await asyncio.wait_for(
                _read_credentials(request), LOGIN_BODY_TIMEOUT_S)
        except TimeoutError:  # the prefix attempt stays counted; the global budget does not
            return fail("body_timeout", 408)
        nxt = fields.get("next", nxt)
        if fields.get("_form") and not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                                         fields.get(auth.CSRF_FIELD)):
            app.state.limiter.settle(ip, False)  # a forged form counts as a failure
            return fail("csrf", 403)
        # No await from here to settle(): the global count is exact, never "in flight".
        user_ok = hmac.compare_digest(username.encode(), JUDGE_USER.encode())
        pass_ok = hmac.compare_digest(password.encode(), settings.judge_password.encode())
        ok = user_ok and pass_ok
        if not app.state.limiter.settle(ip, ok):
            return fail("too_many_attempts", 429, app.state.limiter.retry_after(ip))
        if not ok:
            return fail("invalid_credentials", 401)
        response: Response = (RedirectResponse(auth.safe_next(nxt), 303) if html
                              else JSONResponse({"ok": True}))
        response.set_cookie(
            COOKIE, app.state.sessions.issue(), max_age=SESSION_TTL_S, httponly=True,
            samesite="strict", secure=_cookie_secure(request, settings), path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        _, _, fields = await _read_credentials(request)
        if fields.get("_form") and not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                                         fields.get(auth.CSRF_FIELD)):
            return JSONResponse({"error": "csrf"}, 403)
        cookie = request.cookies.get(COOKIE)
        sid = app.state.sessions.check(cookie)
        app.state.sessions.revoke(cookie)
        if sid is not None:
            with db.transaction(conn):
                Keys.revoke_session(conn, sid)
        response: Response = (RedirectResponse("/login", 303)
                              if "text/html" in request.headers.get("accept", "")
                              else JSONResponse({"ok": True}))
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict",
                               secure=_cookie_secure(request, settings))
        return response

    @app.post("/api/keys")
    async def register_key(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        try:
            body = json.loads(await request.body())
            encoded = body["public_key"]
            if not isinstance(encoded, str) or len(encoded) > 64:
                raise ValueError
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if len(raw) != 32:
                raise ValueError
        except (ValueError, KeyError, TypeError, binascii.Error):
            return JSONResponse({"error": "invalid_public_key"}, 400)
        session_hash = hashlib.sha256(guard.encode()).hexdigest()
        with db.transaction(conn):
            count = conn.execute(
                "SELECT count(*) FROM keys WHERE session_sha256 = ? AND revoked_ts IS NULL",
                (session_hash,),
            ).fetchone()[0]
            if count >= MAX_KEYS_PER_SESSION:
                return JSONResponse({"error": "too_many_keys"}, 429)
            try:
                approver = Keys.register(
                    conn, guard, raw,
                    expires_ts=Sessions.expiry(request.cookies.get(COOKIE, "")),
                )
            except ValueError:
                return JSONResponse({"error": "invalid_public_key"}, 400)
            except KeyConflict:  # revoked, or another session's: generate a new key pair
                return JSONResponse({"error": "key_conflict"}, 409)
        return JSONResponse({"approver": approver})

    @app.get("/api/receipts.jsonl")
    def receipts_jsonl() -> Response:
        return PlainTextResponse(ledger.export_jsonl(), media_type="application/x-ndjson")

    @app.get("/api/ledger-manifest")
    def ledger_manifest() -> dict[str, Any]:
        return ledger.manifest()

    @app.get("/api/ledger-key")
    def ledger_key() -> dict[str, Any]:
        return {"public_key": public_key_raw(ledger.public_key).hex(), "epoch": ledger.epoch}

    @app.get("/api/approver-keys")
    def approver_keys() -> dict[str, str]:
        with conn.lock:
            registry = Keys.registry(conn)
        return {a: public_key_raw(k).hex() for a, k in registry.items()}

    sandbox.install(app, settings, conn)  # STUDIO_SANDBOX=1: reset, judge-only writes, limits
    return app


_APP: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """``uvicorn studio.app:app`` builds the app lazily, so importing this module is cheap."""
    global _APP
    if name == "app":
        if _APP is None:
            _APP = create_app()
        return _APP
    raise AttributeError(name)
