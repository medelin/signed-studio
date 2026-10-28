"""Settings from the environment, the read-only kill switch and the daily model budget.

Misconfiguration fails fast at startup with a ``ConfigError`` that names the variable.
Secrets are kept out of ``repr`` so they never end up in logs or tracebacks.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import math
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from studio.notify import MailConfig

MIN_SECRET_LEN = 32
MIN_SECRET_DISTINCT = 12
MIN_JUDGE_PASSWORD_LEN = 20
MIN_JUDGE_PASSWORD_CLASSES = 3  # of: lower case, upper case, digits, other
MIN_JUDGE_PASSWORD_DISTINCT = 10
ADAPTERS = frozenset({"mock", "graph"})
GOOGLE_ADAPTERS = frozenset({"mock", "rest"})
# Synthetic, model-generated demo photo (see CHANGELOG); STUDIO_PRODUCT_PHOTO replaces it.
DEFAULT_PRODUCT_PHOTO = (
    Path(__file__).resolve().parent.parent / "fixtures" / "assets" / "product-demo.png"
)
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


def _password_is_weak(password: str) -> bool:
    """The login lock is short by design (availability), so the password carries the load."""
    classes = sum(any(test(c) for c in password) for test in (
        str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum()))
    return (len(password) < MIN_JUDGE_PASSWORD_LEN or classes < MIN_JUDGE_PASSWORD_CLASSES
            or len(set(password)) < MIN_JUDGE_PASSWORD_DISTINCT)


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
    # form (access_token in the POST body) | bearer
    meta_token_transport: str = "form"  # noqa: S105 - a mode name, not a token
    meta_app_secret: str = field(default="", repr=False)  # enables appsecret_proof
    # the token Meta echoes when subscribing the leadgen webhook (GET /api/meta/webhook)
    meta_webhook_verify_token: str = field(default="", repr=False)
    session_secret: bytes = field(default=b"", repr=False)
    judge_password: str = field(default="", repr=False)
    read_only: bool = False
    daily_budget_usd: float = 8.0
    replay: bool = False  # serve recorded Model API / Baseten / Graph responses (studio.replay)
    replay_set: str = "demo"
    trusted_proxies: frozenset[str] = frozenset()
    cookie_secure: bool = True
    # every page and API needs a judge session (default on with STUDIO_SANDBOX=1)
    require_login: bool = False
    store_prompts: bool = False
    data_dir: Path = Path("var")
    # The operator's product photo replaces the synthetic one by setting these two.
    product_photo: Path = DEFAULT_PRODUCT_PHOTO
    product_concept: str = "mug"
    keep_audio: bool = False  # voice briefs: keep the converted WAV after transcription
    # campaign.activate is refused above this daily budget (whole units of the ad account's
    # currency), whatever was signed
    max_daily_budget: int = 50
    # the mock adapter's ad account currency (the graph adapter reads the real account's)
    account_currency: str = "PLN"
    landing_url: str = "https://example.com/"
    # e-mail notifications (studio.notify): off unless a transport is configured
    mail: MailConfig = field(default_factory=MailConfig)
    public_url: str = ""  # base of the links in notification mails
    # Google Ads (a second, minimal channel): mock unless GOOGLE_ADS_ADAPTER=rest
    google_adapter: str = "mock"
    google_client_id: str = field(default="", repr=False)
    google_client_secret: str = field(default="", repr=False)
    google_refresh_token: str = field(default="", repr=False)
    google_developer_token: str = field(default="", repr=False)  # optional
    google_login_customer_id: str = ""  # the manager account
    google_customer_id: str = ""
    # replaces the currency read from the customer resource (the mock's default is GBP)
    google_account_currency: str = ""

    @property
    def judge_login_enabled(self) -> bool:
        return bool(self.judge_password)

    @property
    def model_available(self) -> bool:
        """A model call can be made: a Model API key, or replay mode (recorded answers)."""
        return bool(self.model_api_key) or self.replay

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
            if judge_password and _password_is_weak(judge_password):
                raise ConfigError(
                    f"JUDGE_PASSWORD must be >= {MIN_JUDGE_PASSWORD_LEN} characters from >= "
                    f"{MIN_JUDGE_PASSWORD_CLASSES} classes (lower, upper, digits, symbols); "
                    'generate one: python -c "import secrets;print(secrets.token_urlsafe(24))"'
                )
        else:
            session_secret = secrets.token_bytes(32)  # process-random: sessions die on restart

        adapter = e.get("META_ADAPTER", "mock") or "mock"
        if adapter not in ADAPTERS:
            raise ConfigError(f"META_ADAPTER must be one of {sorted(ADAPTERS)}, got {adapter!r}")
        if adapter == "graph":
            for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_PAGE_ID"):
                if not e.get(name):
                    raise ConfigError(f"{name} is required when META_ADAPTER=graph")

        google = e.get("GOOGLE_ADS_ADAPTER", "mock") or "mock"
        if google not in GOOGLE_ADAPTERS:
            raise ConfigError(
                f"GOOGLE_ADS_ADAPTER must be one of {sorted(GOOGLE_ADAPTERS)}, got {google!r}")
        for name in ("GOOGLE_ADS_CUSTOMER_ID", "GOOGLE_ADS_LOGIN_CUSTOMER_ID"):
            if e.get(name) and not re.fullmatch(r"\d{3}-?\d{3}-?\d{4}", e[name]):
                raise ConfigError(f"{name} must be a ten-digit Google Ads customer id")
        if google == "rest":
            for name in ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
                         "GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_CUSTOMER_ID"):
                if not e.get(name):
                    raise ConfigError(f"{name} is required when GOOGLE_ADS_ADAPTER=rest")

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

        concept = e.get("STUDIO_PRODUCT_CONCEPT", "") or "mug"
        if len(concept) > 60 or not re.fullmatch(r"[\w][\w \-]*", concept):
            raise ConfigError("STUDIO_PRODUCT_CONCEPT must be a short noun phrase")

        base_url = e.get("MODEL_API_BASE_URL", "") or "https://api.meta.ai/v1"
        if not base_url.startswith("https://"):
            raise ConfigError("MODEL_API_BASE_URL must use https")

        try:
            ceiling = int(e.get("STUDIO_MAX_DAILY_BUDGET", "")
                          or e.get("STUDIO_MAX_DAILY_BUDGET_PLN", "") or 50)
        except ValueError:
            raise ConfigError("STUDIO_MAX_DAILY_BUDGET must be an integer") from None
        if not 1 <= ceiling <= 500:
            raise ConfigError("STUDIO_MAX_DAILY_BUDGET must be 1-500")
        landing = _landing_url(e, adapter)
        transport = e.get("META_TOKEN_TRANSPORT", "") or "form"
        if transport not in ("form", "bearer"):
            raise ConfigError("META_TOKEN_TRANSPORT must be form or bearer")
        require_login = _bool(e, "STUDIO_REQUIRE_LOGIN", _bool(e, "STUDIO_SANDBOX", False))
        if require_login and not judge_password:
            raise ConfigError("STUDIO_REQUIRE_LOGIN=1 needs JUDGE_PASSWORD (nobody could sign in)")
        try:
            mail = MailConfig.from_env(e)
        except ValueError as exc:
            raise ConfigError(str(exc)) from None
        public_url = _public_url(e.get("STUDIO_PUBLIC_URL", ""))
        return cls(
            model_api_key=e.get("MODEL_API_KEY", ""),
            model_api_base_url=base_url.rstrip("/"),
            baseten_api_key=e.get("BASETEN_API_KEY", ""),
            meta_adapter=adapter,
            meta_access_token=e.get("META_ACCESS_TOKEN", ""),
            meta_ad_account_id=e.get("META_AD_ACCOUNT_ID", ""),
            meta_page_id=e.get("META_PAGE_ID", ""),
            meta_page_token=e.get("META_PAGE_TOKEN", ""),
            meta_token_transport=transport,
            meta_app_secret=e.get("META_APP_SECRET", ""),
            meta_webhook_verify_token=e.get("META_WEBHOOK_VERIFY_TOKEN", ""),
            session_secret=session_secret,
            judge_password=judge_password,
            read_only=_bool(e, "STUDIO_READ_ONLY", False),
            daily_budget_usd=budget,
            replay=_bool(e, "STUDIO_REPLAY", False),
            replay_set=_replay_set(e.get("STUDIO_REPLAY_SET", "") or "demo"),
            trusted_proxies=frozenset(proxies),
            cookie_secure=_bool(e, "COOKIE_SECURE", True),
            require_login=require_login,
            store_prompts=_bool(e, "STORE_PROMPTS", False),
            data_dir=Path(e.get("STUDIO_DATA_DIR", "") or "var"),
            product_photo=Path(e.get("STUDIO_PRODUCT_PHOTO", "") or DEFAULT_PRODUCT_PHOTO),
            product_concept=concept,
            keep_audio=_bool(e, "STUDIO_KEEP_AUDIO", False),
            max_daily_budget=ceiling,
            account_currency=_currency(e.get("STUDIO_ACCOUNT_CURRENCY", "") or "PLN"),
            landing_url=landing,
            mail=mail,
            public_url=public_url,
            google_adapter=google,
            google_client_id=e.get("GOOGLE_ADS_CLIENT_ID", ""),
            google_client_secret=e.get("GOOGLE_ADS_CLIENT_SECRET", ""),
            google_refresh_token=e.get("GOOGLE_ADS_REFRESH_TOKEN", ""),
            google_developer_token=e.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            google_login_customer_id=e.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", ""),
            google_customer_id=e.get("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", ""),
            google_account_currency=(_currency(e["GOOGLE_ADS_ACCOUNT_CURRENCY"])
                                     if e.get("GOOGLE_ADS_ACCOUNT_CURRENCY") else ""),
        )


def _public_url(url: str) -> str:
    """``STUDIO_PUBLIC_URL``: the studio's own address, https (http only for localhost), no
    credentials, path, query or whitespace; ``""`` when unset."""
    if not url:
        return ""
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parts.scheme != "https" and not (parts.scheme == "http" and local)) or \
            not parts.hostname or parts.username or parts.password or \
            parts.path not in ("", "/") or parts.query or parts.fragment or \
            any(c.isspace() for c in url):
        raise ConfigError("STUDIO_PUBLIC_URL must be the site's origin, e.g. "
                          "https://muse.udun.eu (https; no path, query or credentials)")
    return url.rstrip("/")


def _landing_url(e: Mapping[str, str], adapter: str) -> str:
    """``META_LANDING_URL`` (``STUDIO_LANDING_URL`` is an alias): the ad's link. Required
    with the Graph adapter (a real ad must not point at a placeholder); https with a host,
    no credentials, no whitespace."""
    meta, alias = e.get("META_LANDING_URL", ""), e.get("STUDIO_LANDING_URL", "")
    if meta and alias and meta != alias:
        raise ConfigError("META_LANDING_URL and STUDIO_LANDING_URL differ; set one")
    url = meta or alias
    if not url:
        if adapter == "graph":
            raise ConfigError("META_LANDING_URL is required when META_ADAPTER=graph")
        return "https://example.com/"  # mock adapter only: nothing is published
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        host = None
    if (parts.scheme != "https" or not host or "@" in parts.netloc
            or any(c.isspace() or ord(c) < 0x20 for c in url)):
        raise ConfigError("META_LANDING_URL must be an https URL with a host")
    return url


def _utc_today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def utc_day_start(now: float | None = None) -> float:
    t = time.time() if now is None else now
    day = dt.datetime.fromtimestamp(t, dt.UTC).date()
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC).timestamp()


class SpendSource(Protocol):
    def model_spend_since(self, ts: float) -> float: ...


class Budget:
    """Daily (UTC) spend cap for the Model API. Thread-safe.

    A call ``reserve``s its worst-case estimate under the lock before it is sent (so
    concurrent calls cannot all pass a check and then overspend together) and ``settle``s
    it with the incurred cost afterwards. ``from_ledger`` rebuilds today's spend from the
    durable ``model_call`` receipts, so a restart does not reset the cap.
    """

    def __init__(
        self, limit_usd: float, *, spent_usd: float = 0.0, today: Callable[[], str] = _utc_today
    ) -> None:
        if not math.isfinite(limit_usd) or limit_usd <= 0:
            raise ValueError("budget limit must be positive")
        self.limit_usd = limit_usd
        self._today = today
        self._day = today()
        self._spent = spent_usd
        self._reserved = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_ledger(
        cls, limit_usd: float, ledger: SpendSource, *, now: float | None = None
    ) -> Budget:
        """A budget whose spend today is the sum of today's ``model_call`` receipt costs."""
        start = utc_day_start(now)
        spent = ledger.model_spend_since(start)
        if now is None:
            return cls(limit_usd, spent_usd=spent)
        day = dt.datetime.fromtimestamp(now, dt.UTC).date().isoformat()
        return cls(limit_usd, spent_usd=spent, today=lambda: day)

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
        committed = self._spent + self._reserved
        full = cost == 0 and committed >= self.limit_usd
        if full or committed + cost > self.limit_usd + 1e-9:
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

    def reserve(self, estimate_usd: float) -> float:
        """Hold ``estimate_usd`` of today's budget; ``BudgetExceeded`` if it does not fit."""
        self._valid(estimate_usd)
        with self._lock:
            self._roll()
            self._over(estimate_usd)
            self._reserved += estimate_usd
            return estimate_usd

    def settle(self, reserved_usd: float, cost_usd: float) -> None:
        """Release a reservation and record the cost actually incurred (even past the cap)."""
        self._valid(reserved_usd)
        self._valid(cost_usd)
        with self._lock:
            self._roll()
            self._reserved = max(0.0, self._reserved - reserved_usd)
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
            return max(0.0, self.limit_usd - self._spent - self._reserved)


def _currency(code: str) -> str:
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ConfigError("STUDIO_ACCOUNT_CURRENCY must be an ISO 4217 code, e.g. GBP")
    return code


def _replay_set(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
        raise ConfigError("STUDIO_REPLAY_SET must be a set name like 'demo'")
    return name
