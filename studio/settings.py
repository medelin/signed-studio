"""Settings from the environment, the read-only kill switch and the daily model budget.

Misconfiguration fails fast at startup with a ``ConfigError`` that names the variable.
Secrets are kept out of ``repr`` so they never end up in logs or tracebacks.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import math
import os
import secrets
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

MIN_SECRET_LEN = 32
MIN_SECRET_DISTINCT = 12
ADAPTERS = frozenset({"mock", "graph"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


class ConfigError(RuntimeError):
    """The environment is invalid; the process must not start."""


class ReadOnlyMode(RuntimeError):
    """``STUDIO_READ_ONLY=1``: no model calls and no writes."""


class BudgetExceeded(RuntimeError):
    """The daily Model API budget would be exceeded by this charge."""


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse a ``KEY=value`` file. Refuses files readable by group or others."""
    p = Path(path)
    mode = p.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{p}: permissions {oct(mode & 0o777)} too open, use chmod 600")
    out: dict[str, str] = {}
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in {"'", '"'} and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        out[key.strip()] = value
    return out


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be 0 or 1, got {raw!r}")


def _secret_is_weak(secret: str, judge_password: str) -> bool:
    if len(secret) < MIN_SECRET_LEN or len(set(secret)) < MIN_SECRET_DISTINCT:
        return True
    return bool(judge_password) and judge_password in secret


@dataclass(frozen=True)
class Settings:
    model_api_key: str = field(default="", repr=False)
    model_api_base_url: str = "https://api.meta.ai/v1"
    baseten_api_key: str = field(default="", repr=False)
    meta_adapter: str = "mock"
    meta_access_token: str = field(default="", repr=False)
    meta_ad_account_id: str = field(default="", repr=False)
    meta_page_id: str = field(default="", repr=False)
    meta_page_token: str = field(default="", repr=False)
    session_secret: bytes = field(default=b"", repr=False)
    judge_password: str = field(default="", repr=False)
    read_only: bool = False
    daily_budget_usd: float = 8.0
    replay: bool = False
    trusted_proxies: frozenset[str] = frozenset()
    cookie_secure: bool = True
    store_prompts: bool = False
    data_dir: Path = Path("var")

    @property
    def judge_login_enabled(self) -> bool:
        return bool(self.judge_password)

    def require_writable(self) -> None:
        if self.read_only:
            raise ReadOnlyMode("STUDIO_READ_ONLY=1: writes and model calls are disabled")

    @classmethod
    def load(
        cls, env: Mapping[str, str] | None = None, env_file: str | Path | None = None
    ) -> Settings:
        """Load from ``env`` (default ``os.environ``), optionally layered over ``env_file``."""
        merged: dict[str, str] = {}
        if env_file is not None and Path(env_file).exists():
            merged.update(read_env_file(env_file))
        merged.update(os.environ if env is None else env)
        e = {k: v.strip() for k, v in merged.items()}

        judge_password = e.get("JUDGE_PASSWORD", "")
        secret = e.get("SESSION_SECRET", "")
        if secret or judge_password:
            if _secret_is_weak(secret, judge_password):
                raise ConfigError(
                    f"SESSION_SECRET must be >= {MIN_SECRET_LEN} random characters, "
                    "unrelated to JUDGE_PASSWORD (required when JUDGE_PASSWORD is set)"
                )
            session_secret = secret.encode()
        else:
            session_secret = secrets.token_bytes(32)  # process-random: sessions die on restart

        adapter = e.get("META_ADAPTER", "mock") or "mock"
        if adapter not in ADAPTERS:
            raise ConfigError(f"META_ADAPTER must be one of {sorted(ADAPTERS)}, got {adapter!r}")
        if adapter == "graph":
            for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID"):
                if not e.get(name):
                    raise ConfigError(f"{name} is required when META_ADAPTER=graph")

        try:
            budget = float(e.get("STUDIO_DAILY_BUDGET_USD", "8") or "8")
        except ValueError:
            budget = math.nan
        if not math.isfinite(budget) or budget <= 0:
            raise ConfigError("STUDIO_DAILY_BUDGET_USD must be a positive number")

        proxies: set[str] = set()
        for item in filter(None, (p.strip() for p in e.get("TRUSTED_PROXY", "").split(","))):
            try:
                proxies.add(str(ipaddress.ip_address(item)))
            except ValueError as exc:
                raise ConfigError(f"TRUSTED_PROXY must be IP addresses, got {item!r}") from exc

        base_url = e.get("MODEL_API_BASE_URL", "") or "https://api.meta.ai/v1"
        if not base_url.startswith("https://"):
            raise ConfigError("MODEL_API_BASE_URL must use https")

        return cls(
            model_api_key=e.get("MODEL_API_KEY", ""),
            model_api_base_url=base_url.rstrip("/"),
            baseten_api_key=e.get("BASETEN_API_KEY", ""),
            meta_adapter=adapter,
            meta_access_token=e.get("META_ACCESS_TOKEN", ""),
            meta_ad_account_id=e.get("META_AD_ACCOUNT_ID", ""),
            meta_page_id=e.get("META_PAGE_ID", ""),
            meta_page_token=e.get("META_PAGE_TOKEN", ""),
            session_secret=session_secret,
            judge_password=judge_password,
            read_only=_bool(e, "STUDIO_READ_ONLY", False),
            daily_budget_usd=budget,
            replay=_bool(e, "STUDIO_REPLAY", False),
            trusted_proxies=frozenset(proxies),
            cookie_secure=_bool(e, "COOKIE_SECURE", True),
            store_prompts=_bool(e, "STORE_PROMPTS", False),
            data_dir=Path(e.get("STUDIO_DATA_DIR", "") or "var"),
        )


def _utc_today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class Budget:
    """Daily (UTC) spend cap for the Model API. Thread-safe."""

    def __init__(
        self, limit_usd: float, *, spent_usd: float = 0.0, today: Callable[[], str] = _utc_today
    ) -> None:
        if not math.isfinite(limit_usd) or limit_usd <= 0:
            raise ValueError("budget limit must be positive")
        self.limit_usd = limit_usd
        self._today = today
        self._day = today()
        self._spent = spent_usd
        self._lock = threading.Lock()

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._spent = day, 0.0

    @staticmethod
    def _valid(cost: float) -> None:
        if not math.isfinite(cost) or cost < 0:
            raise ValueError(f"invalid cost: {cost!r}")

    def check(self, estimate_usd: float = 0.0) -> None:
        """Raise ``BudgetExceeded`` if ``estimate_usd`` would not fit today."""
        self._valid(estimate_usd)
        with self._lock:
            self._roll()
            self._over(estimate_usd)

    def _over(self, cost: float) -> None:
        full = cost == 0 and self._spent >= self.limit_usd
        if full or self._spent + cost > self.limit_usd + 1e-9:
            raise BudgetExceeded(
                f"daily model budget ${self.limit_usd:.2f} reached (spent ${self._spent:.4f})"
            )

    def charge(self, cost_usd: float) -> None:
        """Record ``cost_usd``; refuses (and records nothing) if it would exceed the cap."""
        self._valid(cost_usd)
        with self._lock:
            self._roll()
            self._over(cost_usd)
            self._spent += cost_usd

    def record(self, cost_usd: float) -> None:
        """Record a cost already incurred (after a model call), even past the cap."""
        self._valid(cost_usd)
        with self._lock:
            self._roll()
            self._spent += cost_usd

    def spent(self) -> float:
        with self._lock:
            self._roll()
            return self._spent

    def remaining(self) -> float:
        with self._lock:
            self._roll()
            return max(0.0, self.limit_usd - self._spent)
