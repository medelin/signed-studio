"""Sign in or register with Google or Facebook (OAuth 2.0 authorization code flow).

- ``/auth/{provider}/start`` opens the provider's consent page. The ``state`` is random,
  kept server-side for ten minutes and bound to a ``SameSite=Lax`` cookie on ``/auth/``, so a
  callback the browser did not start is refused. Google also gets PKCE (S256); Facebook's
  profile call carries ``appsecret_proof``.
- ``/auth/{provider}/callback`` exchanges the code, reads the provider's stable user id
  (OIDC ``sub``, Facebook ``id``) and signs that identity's account in, or registers a new
  ``trial`` account (``studio.accounts``). An e-mail match never links an existing account;
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
from collections import OrderedDict
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


PROVIDERS = {
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

    def client(self, provider: str) -> tuple[str, str] | None:
        pair = {"google": (self.google_id, self.google_secret),
                "facebook": (self.facebook_id, self.facebook_secret)}.get(provider)
        return pair if pair and all(pair) else None

    @property
    def enabled(self) -> list[str]:
        """Configured providers in display order: Facebook first (the studio is for Meta
        advertisers), then Google."""
        return [p for p in ("facebook", "google") if self.client(p)]

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> SocialLoginConfig:
        """Raises ``ValueError`` naming the variable; a provider is on when both are set."""
        values = {name: e.get(name, "").strip() for name in (
            "GOOGLE_LOGIN_CLIENT_ID", "GOOGLE_LOGIN_CLIENT_SECRET",
            "FACEBOOK_LOGIN_CLIENT_ID", "FACEBOOK_LOGIN_CLIENT_SECRET")}
        for prefix in ("GOOGLE_LOGIN_", "FACEBOOK_LOGIN_"):
            pair = [values[prefix + "CLIENT_ID"], values[prefix + "CLIENT_SECRET"]]
            if any(pair) and not all(pair):
                raise ValueError(f"{prefix}CLIENT_ID and {prefix}CLIENT_SECRET go together")
        fb = values["FACEBOOK_LOGIN_CLIENT_ID"]
        if fb and not fb.isdigit():
            raise ValueError("FACEBOOK_LOGIN_CLIENT_ID is the numeric Meta app id")
        return cls(values["GOOGLE_LOGIN_CLIENT_ID"], values["GOOGLE_LOGIN_CLIENT_SECRET"],
                   fb, values["FACEBOOK_LOGIN_CLIENT_SECRET"])


@dataclass(frozen=True)
class Pending:
    provider: str
    verifier: str
    nxt: str
    link_account: str  # the signed-in account to link to, or ""
    expires: float


class StatesFull(Exception):
    """Every slot holds a live started sign-in: a new start is refused, none is evicted."""


class States:
    """Started sign-ins by ``state``, each usable once; bounded and expiring. A client
    (its limiter prefix) holds at most ``PER_CLIENT`` and a new one ends its own oldest;
    when ``LIMIT`` live ones exist a new start is refused (``StatesFull``), so nobody can
    push other people's sign-ins out."""

    LIMIT = 2000
    PER_CLIENT = 10

    def __init__(self) -> None:
        self._items: OrderedDict[str, tuple[Pending, str]] = OrderedDict()  # -> client
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
            if client:
                mine = [k for k, (_, c) in self._items.items() if c == client]
                for key in mine[:max(0, len(mine) - self.PER_CLIENT + 1)]:
                    del self._items[key]
            if len(self._items) >= self.LIMIT:
                raise StatesFull("too many sign-ins in progress")
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
    if p.pkce:
        form["code_verifier"] = pending.verifier
    own = http is None
    http = http or httpx.Client(timeout=TIMEOUT_S)
    try:
        r = http.post(p.token_url, data=form)
        if r.status_code != 200:
            raise ProviderError(f"{provider} token exchange: HTTP {r.status_code}")
        token = str(r.json().get("access_token") or "")
        if not token:
            raise ProviderError(f"{provider} token exchange: no access token")
        if provider == "google":
            r = http.get("https://openidconnect.googleapis.com/v1/userinfo",
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
    subject = str(data.get("sub" if provider == "google" else "id") or "")
    if not subject or len(subject) > 255:
        raise ProviderError(f"{provider} profile: no user id")
    email = str(data.get("email") or "")
    if provider == "google" and data.get("email_verified") is not True:
        email = ""
    return Identity(provider, subject, email, str(data.get("name") or ""))


def bounce_page(target: str, label: str) -> str:
    """Moves on to ``target`` (a same-site path) by a same-site navigation."""
    url = escape(target, quote=True)
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"refresh\" content=\"0;url={url}\">"
            "<meta name=\"robots\" content=\"noindex\"><title>Muse Studio Lite</title></head>"
            f"<body><p><a href=\"{url}\">{escape(label)}</a></p></body></html>")
