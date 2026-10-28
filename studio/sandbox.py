"""Judge sandbox: hourly reset of the demo data, judge-only writes, per-IP/session limits.

Enabled with ``STUDIO_SANDBOX=1`` (off by default, so local runs and tests are untouched).
The public pages stay readable by anyone; everything else is layered on top of the app's own
guards (``write_guard``, ``BodyLimit``, the login limiter, the daily model budget):

- **judge-only writes**: any state-changing request except ``/login`` and ``/logout`` needs a
  valid judge session *before* its body is read (401 otherwise), whatever the route does;
- **limits**: per client prefix (IPv4 /32, IPv6 /64) for all requests and for writes, and
  per session for writes per minute and per hour (429 with ``Retry-After``); the tables are
  bounded and evict their least recently used entries;
- **hourly reset** (``reset_demo(keep_receipts=True)``): only while the sandbox is enabled;
  the first tick on a database without a marker writes the marker only (a fresh deployment
  or a first visitor never wipes anything); the marker is written by ``reset_demo`` as the
  last statement of its transaction, so a failed reset leaves the old marker and is retried;
  receipts, runs, consumed nonces and approver keys are kept; a reset is deferred while
  judges are writing, but never by more than half an interval;
- startup refuses an unsafe sandbox: no judge password (writes would be impossible, and an
  empty password must never be an open door), a Graph (real Meta) adapter, or cookies
  without ``Secure``.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from studio import db
from studio.settings import ConfigError, Settings

LOG = logging.getLogger("studio.sandbox")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MARKER = "last_reset"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SESSION_PATHS = frozenset({"/login", "/logout"})  # guarded by the login limiter / same-origin
QUIET_S = 120.0  # a reset waits until no judge has written for this long ...
TICK_S = 60.0
MIN_INTERVAL_S = 300.0
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class SandboxConfig:
    enabled: bool = False
    reset_interval_s: float = 3600.0
    ip_requests_per_min: int = 600
    ip_writes_per_min: int = 30
    session_writes_per_min: int = 30
    session_writes_per_hour: int = 300
    reset_thread: bool = True

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> SandboxConfig:
        e = os.environ if env is None else env
        raw = e.get("STUDIO_SANDBOX", "").strip().lower()
        if raw not in _TRUE | _FALSE:
            raise ConfigError(f"STUDIO_SANDBOX must be 0 or 1, got {raw!r}")

        def number(name: str, default: float, low: float, high: float) -> float:
            try:
                value = float(e.get(name, "").strip() or default)
            except ValueError:
                value = math.nan
            if not math.isfinite(value) or not low <= value <= high:
                raise ConfigError(f"{name} must be a number in [{low:g}, {high:g}]")
            return value

        return cls(
            enabled=raw in _TRUE,
            reset_interval_s=number("STUDIO_RESET_INTERVAL_S", 3600, MIN_INTERVAL_S, 7 * 86400),
            ip_requests_per_min=int(number("STUDIO_IP_REQUESTS_PER_MIN", 600, 10, 100_000)),
            ip_writes_per_min=int(number("STUDIO_IP_WRITES_PER_MIN", 30, 1, 10_000)),
            session_writes_per_min=int(number("STUDIO_SESSION_WRITES_PER_MIN", 30, 1, 10_000)),
            session_writes_per_hour=int(
                number("STUDIO_SESSION_WRITES_PER_HOUR", 300, 1, 100_000)),
        )

    def validate(self, settings: Settings) -> None:
        """Refuse to start a sandbox that would be unsafe on the open internet."""
        if not self.enabled:
            return
        if not settings.judge_login_enabled:
            raise ConfigError("STUDIO_SANDBOX=1 requires JUDGE_PASSWORD and SESSION_SECRET")
        if settings.meta_adapter != "mock":
            raise ConfigError("STUDIO_SANDBOX=1 requires META_ADAPTER=mock")
        if not settings.cookie_secure:
            raise ConfigError("STUDIO_SANDBOX=1 requires COOKIE_SECURE=1")


# -- hourly reset -----------------------------------------------------------------------


def read_marker(conn: Any) -> float | None:
    with conn.lock:
        row = conn.execute("SELECT value FROM sandbox_meta WHERE key = ?", (MARKER,)).fetchone()
    if row is None:
        return None
    try:
        value = float(row[0])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _write_marker(conn: Any, now: float) -> None:
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO sandbox_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (MARKER, str(now)))


def maybe_reset(
    conn: Any,
    *,
    enabled: bool,
    interval_s: float = 3600.0,
    now: float | None = None,
    last_write: float | None = None,
    seed_dir: Any = None,
) -> str:
    """One reset decision: ``disabled`` | ``marked`` | ``waiting`` | ``deferred`` | ``reset``.

    ``last_write`` is when a judge last wrote; a due reset is ``deferred`` while that is
    under ``QUIET_S`` ago, until the reset is overdue by half an interval.
    """
    if not enabled:
        return "disabled"
    t = time.time() if now is None else now
    marker = read_marker(conn)
    if marker is None or marker > t + TICK_S:  # first run, unreadable, or clock went back
        _write_marker(conn, t)
        return "marked"
    age = t - marker
    if age < interval_s:
        return "waiting"
    if last_write is not None and t - last_write < QUIET_S and age < interval_s * 1.5:
        return "deferred"
    kwargs = {} if seed_dir is None else {"seed_dir": seed_dir}
    db.reset_demo(conn, keep_receipts=True, now=t, **kwargs)  # marker last, same transaction
    LOG.info("sandbox: demo data reset (receipts kept)")
    return "reset"


class Resetter:
    """Daemon thread calling ``maybe_reset`` every ``TICK_S``; errors are logged, retried."""

    def __init__(self, conn: Any, config: SandboxConfig,
                 last_write: Callable[[], float | None]) -> None:
        self._conn = conn
        self._config = config
        self._last_write = last_write
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sandbox-reset", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> str:
        return maybe_reset(self._conn, enabled=self._config.enabled,
                           interval_s=self._config.reset_interval_s,
                           last_write=self._last_write())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # keep the sandbox up; the old marker makes the next tick retry
                LOG.exception("sandbox: reset failed, will retry")
            self._stop.wait(TICK_S)


# -- limits -----------------------------------------------------------------------------


class Window:
    """Sliding-window counters per key, at most ``max_keys`` keys (LRU eviction). Thread-safe."""

    def __init__(self, limit: int, window_s: float, max_keys: int = 20_000) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._hits)

    def hit(self, key: str, now: float | None = None) -> float:
        """Count one hit; 0 if allowed, else seconds until a slot frees (not counted)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = deque()
                while len(self._hits) > self.max_keys:
                    self._hits.popitem(last=False)
            self._hits.move_to_end(key)
            while hits and hits[0] <= t - self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1.0, hits[0] + self.window_s - t)
            hits.append(t)
            return 0.0


class SandboxGuard:
    """ASGI middleware: per-prefix request limit; writes need a judge session and are limited
    per prefix and per session. Rejections happen before the body is read."""

    def __init__(self, app: ASGIApp, *, config: SandboxConfig,
                 ip_of: Callable[[Request], str],
                 session_of: Callable[[Request], str | None]) -> None:
        self.app = app
        self.ip_of = ip_of
        self.session_of = session_of
        self.ip_requests = Window(config.ip_requests_per_min, 60.0)
        self.ip_writes = Window(config.ip_writes_per_min, 60.0)
        self.session_minute = Window(config.session_writes_per_min, 60.0, max_keys=2_000)
        self.session_hour = Window(config.session_writes_per_hour, 3600.0, max_keys=2_000)
        self.last_write: float | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        ip = self.ip_of(request)
        wait = self.ip_requests.hit(ip)
        method = scope.get("method", "GET").upper()
        if not wait and method not in SAFE_METHODS and scope.get("path") not in SESSION_PATHS:
            sid = self.session_of(request)
            if sid is None:
                await JSONResponse({"error": "login_required"}, 401)(scope, receive, send)
                return
            wait = (self.ip_writes.hit(ip) or self.session_minute.hit(sid)
                    or self.session_hour.hit(sid))
            if not wait:
                self.last_write = time.time()
        if wait:
            response = JSONResponse({"error": "rate_limited"}, 429,
                                    headers={"Retry-After": str(math.ceil(wait))})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install(app: Any, settings: Settings, conn: Any,
            config: SandboxConfig | None = None) -> SandboxConfig:
    """Wire the sandbox into ``app`` when enabled (``STUDIO_SANDBOX=1``); no-op otherwise."""
    from studio.app import COOKIE, client_ip, limiter_key  # the app imports this module lazily

    cfg = config or SandboxConfig.load()
    cfg.validate(settings)
    app.state.sandbox = cfg
    if not cfg.enabled:
        return cfg

    def ip_of(request: Request) -> str:
        return limiter_key(client_ip(request, settings.trusted_proxies))

    def session_of(request: Request) -> str | None:
        return app.state.sessions.check(request.cookies.get(COOKIE))  # type: ignore[no-any-return]

    guards: list[SandboxGuard] = []

    class _Guard(SandboxGuard):
        def __init__(self, inner: ASGIApp) -> None:
            super().__init__(inner, config=cfg, ip_of=ip_of, session_of=session_of)
            guards.append(self)

    app.add_middleware(_Guard)
    app.state.sandbox_guards = guards  # built with the middleware stack (first request)

    def last_write() -> float | None:
        return max((g.last_write for g in guards if g.last_write is not None), default=None)

    resetter = Resetter(conn, cfg, last_write)
    app.state.sandbox_resetter = resetter
    if cfg.reset_thread:
        resetter.start()
        app.router.on_shutdown.append(resetter.stop)
    LOG.info("sandbox enabled: reset every %ss, judge-only writes", cfg.reset_interval_s)
    return cfg
