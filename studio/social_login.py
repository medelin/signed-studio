"""Sign in or register with PayPal, Facebook or Google (OAuth 2.0 authorization code flow).

- ``/auth/{provider}/start`` opens the provider's consent page. The ``state`` is random,
  kept server-side for ten minutes and bound to a ``SameSite=Lax`` cookie on ``/auth/``, so a
  callback the browser did not start is refused. Google also gets PKCE (S256); Facebook's
  profile call carries ``appsecret_proof``. PayPal ("Log in with PayPal", sandbox only here)
  takes neither: its token call authenticates with HTTP Basic, never with a body secret.
- ``/auth/{provider}/callback`` exchanges the code, reads the provider's stable user id
  (OIDC ``sub``, Facebook ``id``, PayPal ``user_id``) and signs that identity's account in,
  or registers a new ``trial`` account (``studio.accounts``). An e-mail match never links an
  existing account;
  linking happens only from a signed-in session (``/auth/{provider}/start`` while signed in).
- The session cookie is ``SameSite=Strict``, which the browser holds back for the rest of a
  redirect chain the provider started; so the callback answers with a tiny page that moves
  on by ``<meta http-equiv="refresh">``, a same-site navigation that carries the cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from html import escape
from typing import Any
from urllib.parse import urlencode

import httpx

from studio.meta.graph import VERSION as GRAPH_VERSION

STATE_COOKIE = "studio_oauth"
STATE_TTL_S = 600.0
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Provider:
    name: str
    authorize_url: str
    token_url: str
    scope: str
    pkce: bool


def paypal_hosts(env: str) -> tuple[str, str]:
    """(consent page, API base) of PayPal's sandbox, or of live for any other ``env``. Sign-in
    refuses to start on anything but the sandbox (``SocialLoginConfig.from_env``)."""
    if env == "sandbox":
        return "https://www.sandbox.paypal.com/connect", "https://api-m.sandbox.paypal.com"
    return "https://www.paypal.com/connect", "https://api-m.paypal.com"


PAYPAL_CONNECT, PAYPAL_API = paypal_hosts("sandbox")

PROVIDERS = {
    "paypal": Provider("paypal", PAYPAL_CONNECT, PAYPAL_API + "/v1/oauth2/token",
                       "openid profile email", False),
    "google": Provider("google", "https://accounts.google.com/o/oauth2/v2/auth",
                       "https://oauth2.googleapis.com/token", "openid email profile", True),
    "facebook": Provider("facebook", f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth",
                         f"https://graph.facebook.com/{GRAPH_VERSION}/oauth/access_token",
                         "email,public_profile", False),
}


@dataclass(frozen=True)
class SocialLoginConfig:
    google_id: str = ""
    google_secret: str = field(default="", repr=False)
    facebook_id: str = ""
    facebook_secret: str = field(default="", repr=False)
    paypal_id: str = ""
    paypal_secret: str = field(default="", repr=False)
    paypal_env: str = "sandbox"

    def client(self, provider: str) -> tuple[str, str] | None:
        pair = {"google": (self.google_id, self.google_secret),
                "facebook": (self.facebook_id, self.facebook_secret),
                "paypal": (self.paypal_id, self.paypal_secret)}.get(provider)
        return pair if pair and all(pair) else None

    @property
    def enabled(self) -> list[str]:
        """Configured providers in display order: PayPal first (the studio's own money
        runs on it, so judges already have a sandbox login), then Facebook (the studio is
        for Meta advertisers), then Google."""
        return [p for p in ("paypal", "facebook", "google") if self.client(p)]

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> SocialLoginConfig:
        """Raises ``ValueError`` naming the variable; a provider is on when both are set."""
        values = {name: e.get(name, "").strip() for name in (
            "GOOGLE_LOGIN_CLIENT_ID", "GOOGLE_LOGIN_CLIENT_SECRET",
            "FACEBOOK_LOGIN_CLIENT_ID", "FACEBOOK_LOGIN_CLIENT_SECRET",
            "PAYPAL_LOGIN_CLIENT_ID", "PAYPAL_LOGIN_CLIENT_SECRET")}
        for prefix in ("GOOGLE_LOGIN_", "FACEBOOK_LOGIN_", "PAYPAL_LOGIN_"):
            pair = [values[prefix + "CLIENT_ID"], values[prefix + "CLIENT_SECRET"]]
            if any(pair) and not all(pair):
                raise ValueError(f"{prefix}CLIENT_ID and {prefix}CLIENT_SECRET go together")
        fb = values["FACEBOOK_LOGIN_CLIENT_ID"]
        if fb and not fb.isdigit():
            raise ValueError("FACEBOOK_LOGIN_CLIENT_ID is the numeric Meta app id")
        env = e.get("PAYPAL_ENV", "").strip().lower() or "sandbox"
        if values["PAYPAL_LOGIN_CLIENT_ID"] and env != "sandbox":
            raise ValueError("PAYPAL_LOGIN_* is sandbox-only in this release "
                             f"(PAYPAL_ENV={env!r})")
        return cls(values["GOOGLE_LOGIN_CLIENT_ID"], values["GOOGLE_LOGIN_CLIENT_SECRET"],
                   fb, values["FACEBOOK_LOGIN_CLIENT_SECRET"],
                   values["PAYPAL_LOGIN_CLIENT_ID"], values["PAYPAL_LOGIN_CLIENT_SECRET"], env)


@dataclass(frozen=True)
class Pending:
    provider: str
    verifier: str
    nxt: str
    link_account: str  # the signed-in account to link to, or ""
    expires: float


class TooManyStarts(Exception):
    """This client (limiter prefix) started more sign-ins than ``States.STARTS`` per
    ``STATE_TTL_S``: refused for this client only."""


class States:
    """Started sign-ins by ``state``, each usable once; bounded and expiring. A client
    (its limiter prefix) may start ``STARTS`` per ``STATE_TTL_S`` and holds at most
    ``PER_CLIENT`` (a new one ends its own oldest). When ``LIMIT`` live ones exist, the
    client holding the most loses its oldest: nobody is refused because of others, and
    whoever floods the table pays for it first."""

    LIMIT = 2000
    PER_CLIENT = 10
    STARTS = 30

    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[Pending, str]] = OrderedDict()  # -> client
        self._starts: dict[str, list[float]] = {}  # client -> recent start times
        self._lock = threading.Lock()

    def open(self, provider: str, nxt: str, link_account: str = "",
             now: float | None = None, *, client: str = "") -> tuple[str, Pending]:
        now = time.time() if now is None else now
        state = secrets.token_urlsafe(32)
        pending = Pending(provider, secrets.token_urlsafe(48), nxt, link_account,
                          now + STATE_TTL_S)
        with self._lock:
            for key in [k for k, (p, _) in self._items.items() if p.expires < now]:
                del self._items[key]
            for c in [c for c, ts in self._starts.items() if ts[-1] <= now - STATE_TTL_S]:
                del self._starts[c]
            starts = [t for t in self._starts.get(client, []) if t > now - STATE_TTL_S]
            if len(starts) >= self.STARTS:
                self._starts[client] = starts
                raise TooManyStarts("too many sign-ins started")
            self._starts[client] = [*starts, now]
            mine = [k for k, (_, c) in self._items.items() if c == client]
            for key in mine[:max(0, len(mine) - self.PER_CLIENT + 1)]:
                del self._items[key]
            while self._items and len(self._items) >= self.LIMIT:
                top = Counter(c for _, c in self._items.values()).most_common(1)[0][0]
                del self._items[next(k for k, (_, c) in self._items.items() if c == top)]
            self._items[state] = (pending, client)
        return state, pending

    def take(self, state: str | None, cookie: str | None, provider: str,
             now: float | None = None) -> Pending | None:
        if not state or not cookie or not hmac.compare_digest(state.encode(), cookie.encode()):
            return None
        now = time.time() if now is None else now
        with self._lock:
            item = self._items.pop(state, None)
        pending = item[0] if item is not None else None
        if pending is None or pending.provider != provider or pending.expires < now:
            return None
        return pending


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(provider: str, client_id: str, redirect_uri: str, state: str,
                  pending: Pending) -> str:
    p = PROVIDERS[provider]
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": p.scope, "state": state}
    if provider == "paypal":
        query["flowEntry"] = "static"  # the consent page without PayPal's sign-up detour
    if p.pkce:
        query |= {"code_challenge": challenge_of(pending.verifier),
                  "code_challenge_method": "S256", "prompt": "select_account"}
    return p.authorize_url + "?" + urlencode(query)


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str
    email: str
    name: str


class ProviderError(Exception):
    """The provider refused or answered something unusable (the message is safe to log)."""


def fetch_identity(provider: str, client: tuple[str, str], code: str, redirect_uri: str,
                   pending: Pending, http: httpx.Client | None = None) -> Identity:
    """Code → token → the provider's user id, name and (verified) e-mail."""
    p = PROVIDERS[provider]
    client_id, secret = client
    form = {"client_id": client_id, "client_secret": secret, "code": code,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
    basic = None
    if provider == "paypal":  # HTTP Basic carries the client; the secret never rides the body
        form = {"grant_type": "authorization_code", "code": code}
        basic = httpx.BasicAuth(client_id, secret)
    if p.pkce:
        form["code_verifier"] = pending.verifier
    own = http is None
    http = http or httpx.Client(timeout=TIMEOUT_S)
    try:
        r = http.post(p.token_url, data=form, auth=basic) if basic else http.post(
            p.token_url, data=form)
        if r.status_code != 200:
            raise ProviderError(f"{provider} token exchange: HTTP {r.status_code}")
        token = str(r.json().get("access_token") or "")
        if not token:
            raise ProviderError(f"{provider} token exchange: no access token")
        if provider == "google":
            r = http.get("https://openidconnect.googleapis.com/v1/userinfo",
                         headers={"Authorization": f"Bearer {token}"})
        elif provider == "paypal":
            r = http.get(PAYPAL_API + "/v1/identity/oauth2/userinfo",
                         params={"schema": "paypalv1.1"},
                         headers={"Authorization": f"Bearer {token}"})
        else:
            proof = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
            r = http.get(f"https://graph.facebook.com/{GRAPH_VERSION}/me",
                         params={"fields": "id,name,email", "access_token": token,
                                 "appsecret_proof": proof})
        if r.status_code != 200:
            raise ProviderError(f"{provider} profile: HTTP {r.status_code}")
        data: dict[str, Any] = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(f"{provider}: {type(exc).__name__}") from None
    finally:
        if own:
            http.close()
    subject = str(data.get({"google": "sub", "paypal": "user_id"}.get(provider, "id")) or "")
    if not subject or len(subject) > 255:
        raise ProviderError(f"{provider} profile: no user id")
    email = str(data.get("email") or "")
    if provider == "google" and data.get("email_verified") is not True:
        email = ""
    if provider == "paypal":
        email = paypal_verified_email(data)
    return Identity(provider, subject, email, str(data.get("name") or ""))


def paypal_verified_email(data: Mapping[str, Any]) -> str:
    """The e-mail only when PayPal vouches for it: ``email_verified`` is true, or the primary
    entry of ``emails`` is confirmed (``schema=paypalv1.1``). Otherwise ``""``."""
    if data.get("email_verified") is True:
        return str(data.get("email") or "")
    for entry in data.get("emails") or []:
        if isinstance(entry, dict) and entry.get("primary") is True \
                and entry.get("confirmed") is True:
            return str(entry.get("value") or "")
    return ""


def bounce_page(target: str, label: str) -> str:
    """Moves on to ``target`` (a same-site path) by a same-site navigation."""
    url = escape(target, quote=True)
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"refresh\" content=\"0;url={url}\">"
            "<meta name=\"robots\" content=\"noindex\"><title>Signed Studio</title></head>"
            f"<body><p><a href=\"{url}\">{escape(label)}</a></p></body></html>")
