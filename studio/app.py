"""FastAPI app: public read-only views, judge login, approver keys and receipt export.

Security notes:
- the login attempt is reserved in the limiter *before* the body is awaited, so concurrent
  requests cannot all slip past the check; success clears the client's bucket;
- credentials are compared as bytes in constant time (non-ASCII input is a 401, not a 500);
- sessions are HMAC-signed with the dedicated ``SESSION_SECRET`` and also tracked server
  side, so ``POST /logout`` revokes them; cookies are HttpOnly, SameSite=Lax, Secure;
- ``X-Forwarded-For`` is used only when the direct peer is a ``TRUSTED_PROXY``, and then
  only its rightmost element (the address that proxy saw);
- request bodies are capped (413), state-changing requests must be same-origin, and the
  ``STUDIO_READ_ONLY`` kill switch turns every write into a 503.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from studio import db
from studio.approvals import Keys
from studio.receipts import public_key_raw
from studio.settings import ReadOnlyMode, Settings

UI = Path(__file__).resolve().parent / "ui"
COOKIE = "studio_session"
SESSION_TTL_S = 8 * 3600
MAX_BODY = 64 * 1024
JUDGE_USER = "judge"
MAX_KEYS_PER_SESSION = 5
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
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


class LoginLimiter:
    """At most ``max_attempts`` per client per window; bounded memory (LRU eviction)."""

    def __init__(self, max_attempts: int = 5, window_s: float = 900, max_clients: int = 10_000):
        self.max_attempts = max_attempts
        self.window_s = window_s
        self.max_clients = max_clients
        self._hits: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._hits)

    def reserve(self, key: str, now: float | None = None) -> bool:
        """Atomically count one attempt; False when the client is over the limit."""
        t = time.time() if now is None else now
        with self._lock:
            hits = [h for h in self._hits.pop(key, []) if t - h < self.window_s]
            allowed = len(hits) < self.max_attempts
            if allowed:
                hits.append(t)
            self._hits[key] = hits
            while len(self._hits) > self.max_clients:
                self._hits.popitem(last=False)
            return allowed

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


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

    def revoke(self, value: str | None) -> None:
        if value and value.count(".") == 2:
            with self._lock:
                self._active.pop(value.split(".")[0], None)


# -- middleware -------------------------------------------------------------------------


class BodyLimit:
    """413 for bodies over ``limit`` bytes, by header or while streaming (chunked)."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.limit):
            await PlainTextResponse("request body too large", 413)(scope, receive, send)
            return
        seen = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
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
        return True  # non-browser client; the SameSite=Lax cookie still guards browsers
    host = request.headers.get("host", "")
    return origin in {f"https://{host}", f"http://{host}"}


async def _read_credentials(request: Request) -> tuple[str, str]:
    raw = await request.body()
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    try:
        if ctype == "application/json":
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                return "", ""
            return str(data.get("username", "")), str(data.get("password", ""))
        form = parse_qs(raw.decode("utf-8"), max_num_fields=10)
    except (ValueError, UnicodeDecodeError):
        return "", ""
    return (form.get("username") or [""])[0], (form.get("password") or [""])[0]


# -- application ------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load(env_file=Path(".env"))
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = db.connect(data_dir / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, data_dir / "ledger.key")

    app = FastAPI(title="Muse Studio Lite", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.conn = conn
    app.state.ledger = ledger
    app.state.limiter = LoginLimiter()
    app.state.sessions = Sessions(settings.session_secret)
    templates = Jinja2Templates(directory=str(UI / "templates"))
    app.mount("/static", StaticFiles(directory=str(UI / "static")), name="static")
    app.add_middleware(BodyLimit)

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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        receipts = ledger.receipts()[-50:][::-1]
        manifest = ledger.manifest()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "receipts": receipts,
                "seq": manifest["seq"],
                "epoch": manifest["epoch"],
                "logged_in": session_of(request) is not None,
                "login_enabled": settings.judge_login_enabled,
                "read_only": settings.read_only,
            },
        )

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "read_only": settings.read_only, "seq": ledger.manifest()["seq"]}

    @app.post("/login")
    async def login(request: Request) -> Response:
        if not settings.judge_login_enabled:
            return JSONResponse({"error": "login_disabled"}, 403)
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        ip = client_ip(request, settings.trusted_proxies)
        if not app.state.limiter.reserve(ip):  # reserved before the body is awaited
            return JSONResponse({"error": "too_many_attempts"}, 429)
        username, password = await _read_credentials(request)
        user_ok = hmac.compare_digest(username.encode(), JUDGE_USER.encode())
        pass_ok = hmac.compare_digest(password.encode(), settings.judge_password.encode())
        if not (user_ok and pass_ok):
            return JSONResponse({"error": "invalid_credentials"}, 401)
        app.state.limiter.clear(ip)
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE, app.state.sessions.issue(), max_age=SESSION_TTL_S, httponly=True,
            samesite="lax", secure=_cookie_secure(request, settings), path="/",
        )
        return response

    @app.post("/logout")
    def logout(request: Request) -> Response:
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        app.state.sessions.revoke(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax",
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
                "SELECT count(*) FROM keys WHERE session_sha256 = ?", (session_hash,)
            ).fetchone()[0]
            if count >= MAX_KEYS_PER_SESSION:
                return JSONResponse({"error": "too_many_keys"}, 429)
            try:
                approver = Keys.register(conn, guard, raw)
            except ValueError:
                return JSONResponse({"error": "invalid_public_key"}, 400)
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
