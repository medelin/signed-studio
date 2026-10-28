"""Google Ads REST adapter (API v23) over ``httpx``; no client library.

Runs only with ``GOOGLE_ADS_ADAPTER=rest`` on the operator's machine; the sandbox never
has a credential.

- Auth: the OAuth 2.0 refresh-token flow. ``GOOGLE_ADS_CLIENT_ID``,
  ``GOOGLE_ADS_CLIENT_SECRET`` and ``GOOGLE_ADS_REFRESH_TOKEN`` are exchanged at
  ``https://oauth2.googleapis.com/token`` for a short-lived access token, sent as
  ``Authorization: Bearer``. Calls carry ``login-customer-id`` (the manager account,
  ``GOOGLE_ADS_LOGIN_CUSTOMER_ID``) and target ``GOOGLE_ADS_CUSTOMER_ID``. The
  ``developer-token`` header is sent only when ``GOOGLE_ADS_DEVELOPER_TOKEN`` is set: some
  Google Cloud projects are granted access without one.
- Tokens are never logged, put in a URL, or included in an error message; an error carries
  the HTTP status and Google's status and error code, never the response body.
- Writes are ``POST customers/{id}/googleAds:mutate`` with ``mutateOperations`` built from
  the signed spec (``adapter.create_operations``): one atomic request with temporary ids,
  ``partialFailure`` off, and ``validateOnly`` for the dry run. Campaign, ad group and ad
  carry ``status: PAUSED`` from a literal. Activation is one mutate that enables the ad,
  the ad group and the campaign (the parent last) behind a ``google.activate`` approval.
- Reads (the customer's currency, insights by day) are ``googleAds:search`` with GAQL.
- Retries: HTTP 429/500/503 are retried with backoff (at most ``RETRIES`` times); a
  network error on a POST is not (the write may have happened).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from studio.approvals import Verified
from studio.google.adapter import (
    ACTIVATE,
    CREATE,
    GAQL,
    activation_names,
    activation_operations,
    create_operations,
    customer_id_of,
    names_of,
    normalize_rows,
    spec_of,
)
from studio.meta.adapter import AdapterError, check_paused, require

VERSION = "v23"
BASE = "https://googleads.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
TRANSIENT = frozenset({429, 500, 503})
RETRIES = 3


class RestAdapter:
    name = "rest"

    def __init__(self, client_id: str, client_secret: str, refresh_token: str,
                 customer_id: str, *, login_customer_id: str = "", developer_token: str = "",
                 account_currency: str = "", version: str = VERSION,
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 30.0,
                 conn: Any = None) -> None:
        if not (client_id and client_secret and refresh_token):
            raise ValueError("rest adapter needs a client id, a client secret and a "
                             "refresh token")
        self._conn = conn
        self.customer_id = customer_id_of(customer_id)
        self._login = customer_id_of(login_customer_id) if login_customer_id else ""
        self._client = (client_id, client_secret, refresh_token)
        self._developer = developer_token
        self._currency = account_currency
        self._access: tuple[str, float] | None = None  # (token, expires monotonic)
        self.base = f"{BASE}/{version}/customers/{self.customer_id}"
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @classmethod
    def from_settings(cls, settings: Any, **kwargs: Any) -> RestAdapter:
        return cls(settings.google_client_id, settings.google_client_secret,
                   settings.google_refresh_token, settings.google_customer_id,
                   login_customer_id=settings.google_login_customer_id,
                   developer_token=settings.google_developer_token,
                   account_currency=settings.google_account_currency, **kwargs)

    def __repr__(self) -> str:
        return f"RestAdapter(base={self.base!r})"

    # -- transport -----------------------------------------------------------------

    def _token(self, force: bool = False) -> str:
        if not force and self._access and self._access[1] > time.monotonic() + 60:
            return self._access[0]
        client_id, secret, refresh = self._client
        try:
            resp = self._http.post(TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": client_id,
                "client_secret": secret, "refresh_token": refresh})
        except httpx.HTTPError as exc:
            raise AdapterError(f"Google OAuth unreachable ({type(exc).__name__})",
                               code="network") from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        token = body.get("access_token") if isinstance(body, dict) else None
        if resp.status_code != 200 or not isinstance(token, str) or not token:
            err = body.get("error") if isinstance(body, dict) else None
            raise AdapterError(f"Google OAuth refused the refresh token ({resp.status_code}"
                               f"{', ' + err if isinstance(err, str) else ''})",
                               code="oauth_error")
        ttl = body.get("expires_in") if isinstance(body, dict) else None
        self._access = (token, time.monotonic() + (ttl if isinstance(ttl, int) else 1800))
        return token

    def _headers(self, token: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if self._login:
            headers["login-customer-id"] = self._login
        if self._developer:  # optional: access is managed per Google Cloud project
            headers["developer-token"] = self._developer
        return headers

    @staticmethod
    def _error(body: Any) -> tuple[str, str]:
        """``(status, error code)`` from a Google error body; never the message text."""
        err = body.get("error") if isinstance(body, dict) else None
        if not isinstance(err, dict):
            return "", ""
        status = str(err.get("status") or "")
        for detail in err.get("details") or []:
            for e in (detail.get("errors") or []) if isinstance(detail, dict) else []:
                code = e.get("errorCode") if isinstance(e, dict) else None
                if isinstance(code, dict) and code:
                    k, v = next(iter(code.items()))
                    return status, f"{k}.{v}"[:80]
        return status, ""

    def _post(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        refreshed = False
        for attempt in range(RETRIES + 1):
            token = self._token()
            try:
                resp = self._http.post(f"{self.base}/{path}", headers=self._headers(token),
                                       json=dict(body))
            except httpx.HTTPError as exc:
                raise AdapterError(f"Google Ads unreachable ({type(exc).__name__})",
                                   code="network") from None
            try:
                data = resp.json()
            except ValueError:
                data = None
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self._token(force=True)
                continue
            if resp.status_code < 400 and isinstance(data, dict):
                return data
            if resp.status_code in TRANSIENT and attempt < RETRIES:
                self._sleep(2 ** attempt)
                continue
            status, code = self._error(data)
            raise AdapterError(
                f"Google Ads error {resp.status_code}{' ' + status if status else ''}"
                f"{' ' + code if code else ''}", code="google_error",
                transient=resp.status_code in TRANSIENT)
        raise AdapterError("Google Ads retries exhausted", code="google_error", transient=True)

    def _search(self, query: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        body: dict[str, Any] = {"query": query, "pageSize": 1000}
        for _ in range(20):
            data = self._post("googleAds:search", body)
            out += [r for r in data.get("results") or [] if isinstance(r, dict)]
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return out
            body = {**body, "pageToken": token}
        return out

    # -- reads ---------------------------------------------------------------------

    def account_info(self) -> dict[str, str]:
        """The customer's currency and time zone (``googleAds:search`` on ``customer``),
        read once and kept; ``GOOGLE_ADS_ACCOUNT_CURRENCY`` replaces the currency."""
        cached = getattr(self, "_account", None)
        if cached is None:
            rows = self._search("SELECT customer.currency_code, customer.time_zone "
                                "FROM customer LIMIT 1")
            customer = rows[0].get("customer") if rows else None
            code = customer.get("currencyCode") if isinstance(customer, dict) else None
            currency = self._currency or code
            if not isinstance(currency, str) or len(currency) != 3:
                raise AdapterError("Google Ads returned no account currency",
                                   code="bad_response")
            cached = {"currency": currency,
                      "time_zone": str((customer or {}).get("timeZone", ""))}
            self._account: dict[str, str] | None = cached
        return dict(cached)

    def insights(self, campaign: str) -> list[dict[str, Any]]:
        if not campaign.startswith(f"customers/{self.customer_id}/campaigns/") or not \
                campaign.rsplit("/", 1)[1].isdigit():
            raise AdapterError("not a campaign of this account", code="bad_id")
        return normalize_rows(self._search(GAQL.format(name=campaign)))

    # -- writes --------------------------------------------------------------------

    def create_search_campaign(self, payload: Mapping[str, Any], approval: Verified, *,
                               validate_only: bool = False) -> dict[str, str]:
        require(approval, CREATE, payload, self._conn)
        check_paused(payload)
        ops = create_operations(spec_of(payload, self.customer_id))
        data = self._post("googleAds:mutate", {
            "mutateOperations": ops, "partialFailure": False, "validateOnly": validate_only})
        if validate_only:
            return {}
        responses = data.get("mutateOperationResponses")
        if not isinstance(responses, list):
            raise AdapterError("Google Ads returned no operation results", code="bad_response")
        return names_of([r for r in responses if isinstance(r, dict)])

    def activate(self, payload: Mapping[str, Any], approval: Verified) -> None:
        require(approval, ACTIVATE, payload, self._conn)
        names = activation_names(payload, self.customer_id)
        self._post("googleAds:mutate", {"mutateOperations": activation_operations(names),
                                        "partialFailure": False})
