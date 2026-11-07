"""PayPal webhooks: offline signature verification, dedupe and receipts.

``POST /webhooks/paypal`` keeps the raw body bytes and verifies **before** parsing:

1. Headers ``paypal-transmission-id``, ``-time``, ``-sig``, ``-cert-url`` and
   ``-auth-algo`` (``SHA256withRSA``) are present and well-formed; the transmission time is
   within ``MAX_SKEW_S`` of now.
2. The cert URL is ``https://api.paypal.com/…`` or ``https://api.sandbox.paypal.com/…``
   (an allowlist: no other host is ever fetched, so the header cannot point the server at
   an internal address). The PEM is fetched once, pinned by its SHA-256 in
   ``paypal_certs``, and checked: the leaf is valid now, names a ``paypal.com`` host, and
   each certificate is signed by the next, up to a trusted root (the ``certifi`` bundle,
   or a test CA).
3. The message ``transmission_id|transmission_time|webhook_id|crc32(body)`` (CRC32 of the
   raw bytes, unsigned decimal) is checked against the base64 ``paypal-transmission-sig``
   with the leaf's RSA key, PKCS#1 v1.5, SHA-256.

Only then is the body parsed, and it is only a **trigger**: PayPal's signature binds it
through CRC32, which is easy to collide, so nothing in it is taken as attested and no
state is ever keyed on it alone. The resource it names (capture, refund, authorization or
order) is read from PayPal's API with the studio's own credentials; it must belong to a
funding of ours (its order, authorization or capture id is in ``fundings`` and its
``custom_id``, when present, is that funding's ad hash). Only the API's facts are
recorded, once per attested state: ``paypal_events`` is unique on (resource kind,
resource id, API status), so a replayed or colliding body that points at a state already
recorded is a no-op, and a new genuine state of our resource is recorded. A delivery that
was recorded is also remembered by (transmission id, body hash), a no-op on redelivery.

Anything else is refused with nothing stored: a body that is not an event or names no
checkable resource, a resource PayPal's API does not know or that is not ours
(``WebhookRefused``, the route answers 400), or a failed API read (``lookup_failed``,
503, so PayPal delivers again). The receipt (``paypal.webhook``, in the funding's run)
holds the API's ids, status and class, amount, currency and ``custom_id``, plus the
body's hash, type and event id as unattested labels, the transmission's hash and time, the
signature's and cert's hashes and ``verified: rsa``. It records no payer data. The raw
deliveries go only into the owner-only export (``export_events``), with the cert PEMs
(``export_certs``), for ``studio verify --paypal-events``.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import re
import sqlite3
import time
import zlib
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding

from studio import funding
from studio.funding import FundingError, parse_amount
from studio.receipts import PAYPAL_WEBHOOK_KIND, Ledger, Receipt, sha256_hex

CERT_HOSTS = frozenset({"api.paypal.com", "api.sandbox.paypal.com"})
CERT_PATH = re.compile(r"^/v1/notifications/certs/.")  # PayPal's cert endpoint, nothing else
CERT_FETCH_TIMEOUT_S = 5.0
CERT_NEGATIVE_TTL_S = 60.0  # a cert URL that failed is not fetched again for this long
CERT_NEGATIVE_MAX = 64
MAX_SKEW_S = 300
MAX_BODY = 256 * 1024
AUTH_ALGO = "SHA256withRSA"
AGENT = "paypal"
_TRANSMISSION = re.compile(r"[A-Za-z0-9-]{8,64}")
_EVENT_ID = re.compile(r"WH-[0-9A-Z-]{8,80}")
_PAYPAL_ID = re.compile(r"[A-Z0-9]{8,36}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS paypal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_kind TEXT NOT NULL,             -- attested by PayPal's API
    resource_id TEXT NOT NULL,
    api_status TEXT NOT NULL,
    event_id TEXT NOT NULL,                  -- from the body: a label, never a key
    event_type TEXT NOT NULL,                -- from the body: a label
    order_id TEXT NOT NULL DEFAULT '',
    funding_id TEXT NOT NULL,
    body TEXT NOT NULL,                      -- the raw bytes PayPal signed (owner-only)
    body_sha256 TEXT NOT NULL,
    transmission_id TEXT NOT NULL,
    transmission_time TEXT NOT NULL,
    signature TEXT NOT NULL,
    cert_sha256 TEXT NOT NULL,
    webhook_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    received_ts REAL NOT NULL,
    UNIQUE (resource_kind, resource_id, api_status),  -- one record per attested state
    UNIQUE (transmission_id, body_sha256)             -- a recorded delivery, once
);
CREATE TABLE IF NOT EXISTS paypal_certs (
    sha256 TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    pem TEXT NOT NULL,
    not_after REAL NOT NULL,
    fetched_ts REAL NOT NULL
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


class WebhookRefused(Exception):
    """A webhook that must not be trusted; ``code`` says why. Nothing was stored."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Transmission:
    transmission_id: str
    transmission_time: str
    signature: str
    cert_url: str
    auth_algo: str

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> Transmission:
        def h(name: str) -> str:
            value = headers.get(name) or headers.get(name.lower()) or ""
            return value.strip()

        t = cls(h("paypal-transmission-id"), h("paypal-transmission-time"),
                h("paypal-transmission-sig"), h("paypal-cert-url"), h("paypal-auth-algo"))
        if not all((t.transmission_id, t.transmission_time, t.signature, t.cert_url,
                    t.auth_algo)):
            raise WebhookRefused("a PayPal transmission header is missing", "headers")
        if not _TRANSMISSION.fullmatch(t.transmission_id):
            raise WebhookRefused("malformed transmission id", "headers")
        if t.auth_algo != AUTH_ALGO:
            raise WebhookRefused("unsupported signature algorithm", "auth_algo")
        return t

    def when(self) -> float:
        try:
            return dt.datetime.fromisoformat(
                self.transmission_time.replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise WebhookRefused("malformed transmission time", "headers") from None


def _locked(conn: sqlite3.Connection) -> AbstractContextManager[Any]:
    """The connection's shared lock: ``apply`` runs on worker threads now, and one SQLite
    connection serves them all. Held around database work only, never around network I/O."""
    lock = getattr(conn, "lock", None)
    return lock if lock is not None else nullcontext()


def cert_url_ok(url: str) -> bool:
    """Only PayPal's own API hosts, over HTTPS, default port, no credentials, and only
    the certificate path (``/v1/notifications/certs/...``)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme == "https" and parts.hostname in CERT_HOSTS and parts.port is None
            and not parts.username and not parts.password and "@" not in parts.netloc
            and CERT_PATH.match(parts.path) is not None)


def message(t: Transmission, webhook_id: str, body: bytes) -> bytes:
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return f"{t.transmission_id}|{t.transmission_time}|{webhook_id}|{crc}".encode()


def load_roots(pem_bundle: bytes | None = None) -> list[x509.Certificate]:
    """Trusted roots: a given PEM bundle (tests, mock), else the ``certifi`` bundle."""
    if pem_bundle is None:
        import certifi

        pem_bundle = Path(certifi.where()).read_bytes()
    return x509.load_pem_x509_certificates(pem_bundle)


INTERMEDIATES = Path(__file__).with_name("intermediates.pem")


def load_intermediates(pem_bundle: bytes | None = None) -> list[x509.Certificate]:
    """Pinned intermediate CAs (``intermediates.pem``): PayPal serves only the leaf."""
    if pem_bundle is None:
        pem_bundle = INTERMEDIATES.read_bytes()
    return x509.load_pem_x509_certificates(pem_bundle)


def check_chain(chain: list[x509.Certificate], roots: Iterable[x509.Certificate],
                now: float, intermediates: Iterable[x509.Certificate] = ()) -> x509.Certificate:
    """The leaf, once every certificate is valid now, each is signed by the next, the last
    by a trusted root, and the leaf names a ``paypal.com`` host. ``WebhookRefused``
    otherwise. A chain that stops below a root is completed only from the pinned
    ``intermediates``, which are then checked like any other link (never trusted alone)."""
    if not chain:
        raise WebhookRefused("no certificate", "cert")
    roots = list(roots)
    chain = list(chain)
    pinned = list(intermediates)
    for _ in range(3):
        if any(r.subject == chain[-1].issuer for r in roots):
            break
        nxt = next((c for c in pinned if c.subject == chain[-1].issuer and c not in chain), None)
        if nxt is None:
            break
        chain.append(nxt)
    moment = dt.datetime.fromtimestamp(now, dt.UTC)
    for cert in chain:
        if not cert.not_valid_before_utc <= moment <= cert.not_valid_after_utc:
            raise WebhookRefused("a certificate is expired or not yet valid", "cert_expired")
    for child, parent in zip(chain, chain[1:], strict=False):
        try:
            child.verify_directly_issued_by(parent)
        except (ValueError, TypeError, InvalidSignature):
            raise WebhookRefused("the certificate chain is broken", "cert_chain") from None
        try:  # an issuer inside the chain must be a CA
            ca = parent.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        except x509.ExtensionNotFound:
            ca = False
        if not ca:
            raise WebhookRefused("an intermediate certificate is not a CA", "cert_chain")
    last = chain[-1]
    trusted = [r for r in roots if r.subject == last.issuer]
    for root in trusted:
        try:
            last.verify_directly_issued_by(root)
            break
        except (ValueError, TypeError, InvalidSignature):
            continue
    else:
        if not any(r.fingerprint(hashes.SHA256()) == last.fingerprint(hashes.SHA256())
                   for r in roots):
            raise WebhookRefused("the certificate chain ends at an untrusted root",
                                 "cert_chain")
    leaf = chain[0]
    names = [a.value for a in leaf.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)]
    try:
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names += san.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        pass
    if not any(isinstance(n, str) and (n == "paypal.com" or n.endswith(".paypal.com"))
               for n in names):
        raise WebhookRefused("the certificate is not PayPal's", "cert_name")
    if not isinstance(leaf.public_key(), rsa.RSAPublicKey):
        raise WebhookRefused("the certificate key is not RSA", "cert")
    return leaf


Fetch = Callable[[str], bytes]


def http_fetch(url: str) -> bytes:
    """GET the cert PEM: no redirects, a short timeout, a size cap."""
    import httpx

    if not cert_url_ok(url):
        raise WebhookRefused("cert URL is not PayPal's", "cert_url")
    with httpx.Client(timeout=CERT_FETCH_TIMEOUT_S, follow_redirects=False) as c:
        r = c.get(url)
    if r.status_code != 200 or len(r.content) > 64 * 1024:
        raise WebhookRefused("could not fetch PayPal's certificate", "cert_fetch")
    return r.content


class Verifier:
    """Verifies transmissions for one webhook id; certs are pinned in ``conn``."""

    def __init__(self, conn: sqlite3.Connection, webhook_id: str, *,
                 fetch: Fetch = http_fetch, roots: list[x509.Certificate] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.conn = conn
        self.webhook_id = webhook_id
        self.fetch = fetch
        self.roots = roots if roots is not None else load_roots()
        self.intermediates = load_intermediates()
        self.clock = clock
        self._failed: dict[str, float] = {}  # cert URL -> when it failed (negative cache)

    def cert(self, url: str) -> tuple[x509.Certificate, str]:
        if not cert_url_ok(url):
            raise WebhookRefused("cert URL is not PayPal's", "cert_url")
        now = self.clock()
        with _locked(self.conn):
            row = self.conn.execute("SELECT pem, sha256 FROM paypal_certs WHERE url = ? AND "
                                    "not_after > ? ORDER BY fetched_ts DESC LIMIT 1",
                                    (url, now)).fetchone()
        if row is None:
            failed_at = self._failed.get(url)
            if failed_at is not None and now - failed_at < CERT_NEGATIVE_TTL_S:
                raise WebhookRefused("could not fetch PayPal's certificate", "cert_fetch")
        try:
            pem = row[0].encode() if row else self.fetch(url)
            try:
                chain = x509.load_pem_x509_certificates(pem)
            except ValueError:
                raise WebhookRefused("the certificate is not PEM", "cert") from None
            leaf = check_chain(chain, self.roots, now, self.intermediates)
        except WebhookRefused:
            if row is None:
                self._remember_failure(url, now)
            raise
        digest = hashlib.sha256(leaf.public_bytes(Encoding.DER)).hexdigest()
        if row is None:
            with _locked(self.conn), funding.atomic(self.conn):
                self.conn.execute(
                    "INSERT OR IGNORE INTO paypal_certs (sha256, url, pem, not_after, "
                    "fetched_ts) VALUES (?, ?, ?, ?, ?)",
                    (digest, url, pem.decode(), leaf.not_valid_after_utc.timestamp(), now))
        elif row[1] != digest:
            raise WebhookRefused("the pinned certificate changed", "cert_pin")
        return leaf, digest

    def _remember_failure(self, url: str, now: float) -> None:
        for old in [u for u, t in self._failed.items() if now - t >= CERT_NEGATIVE_TTL_S]:
            del self._failed[old]
        while len(self._failed) >= CERT_NEGATIVE_MAX:
            del self._failed[next(iter(self._failed))]  # the oldest (insertion order)
        self._failed[url] = now

    def verify(self, headers: Mapping[str, str], body: bytes) -> tuple[Transmission, str]:
        """``(transmission, cert_sha256)`` of a genuine, fresh PayPal transmission."""
        if not self.webhook_id:
            raise WebhookRefused("no PAYPAL_WEBHOOK_ID configured", "not_configured")
        if len(body) > MAX_BODY:
            raise WebhookRefused("body too large", "too_large")
        t = Transmission.from_headers(headers)
        if abs(self.clock() - t.when()) > MAX_SKEW_S:
            raise WebhookRefused("transmission time outside the allowed window", "stale")
        leaf, digest = self.cert(t.cert_url)
        try:
            sig = base64.b64decode(t.signature, validate=True)
        except (ValueError, binascii.Error):
            raise WebhookRefused("signature is not base64", "signature") from None
        key = leaf.public_key()
        assert isinstance(key, rsa.RSAPublicKey)
        try:
            key.verify(sig, message(t, self.webhook_id, body), padding.PKCS1v15(),
                       hashes.SHA256())
        except InvalidSignature:
            raise WebhookRefused("PayPal's signature does not verify", "signature") from None
        return t, digest


# -- applying an event ---------------------------------------------------------------------


def _related(resource: Mapping[str, Any]) -> dict[str, str]:
    rel = ((resource.get("supplementary_data") or {}).get("related_ids") or {})
    return {k: v for k, v in rel.items() if isinstance(v, str)} if isinstance(rel, dict) else {}


def _ids(event_type: str, resource: Mapping[str, Any]) -> dict[str, str]:
    """PayPal ids an event names, under receipt keys."""
    raw = resource.get("id")
    rid: str = raw if isinstance(raw, str) else ""
    rel = _related(resource)
    out = {"order_id": rel.get("order_id", ""), "authorization_id":
           rel.get("authorization_id", ""), "capture_id": rel.get("capture_id", "")}
    if event_type.startswith("CHECKOUT.ORDER."):
        out["order_id"] = rid
    elif event_type.startswith("PAYMENT.AUTHORIZATION."):
        out["authorization_id"] = rid
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":  # the resource is the refund
        out["refund_id"] = rid
        for link in resource.get("links") or []:
            href = link.get("href") if isinstance(link, dict) else None
            if isinstance(link, dict) and link.get("rel") == "up" and isinstance(href, str) \
                    and "/captures/" in href:
                out["capture_id"] = href.rstrip("/").rsplit("/", 1)[-1]
    elif event_type.startswith("PAYMENT.CAPTURE."):
        out["capture_id"] = rid
    return {k: v for k, v in out.items() if v and _PAYPAL_ID.fullmatch(v)}


def _find_funding(conn: sqlite3.Connection, ids: Mapping[str, str]) -> funding.Funding | None:
    for key in ("order_id", "authorization_id", "capture_id"):
        value = ids.get(key)
        if value:
            row = conn.execute(f"SELECT id FROM fundings WHERE {key} = ?",  # noqa: S608
                               (value,)).fetchone()
            if row:
                return funding.get(conn, row[0])
    return None


def _amount(resource: Mapping[str, Any]) -> tuple[int, str] | None:
    amount = resource.get("amount")
    if not isinstance(amount, dict):
        units = resource.get("purchase_units")
        if isinstance(units, list) and units and isinstance(units[0], dict):
            amount = units[0].get("amount")
    if not isinstance(amount, dict):
        return None
    try:
        cur = str(amount.get("currency_code"))
        return parse_amount(amount.get("value"), cur), cur
    except FundingError:
        return None


def _custom_id(resource: Mapping[str, Any]) -> str:
    custom = resource.get("custom_id")
    if not isinstance(custom, str):
        units = resource.get("purchase_units")
        if isinstance(units, list) and units and isinstance(units[0], dict):
            custom = units[0].get("custom_id")
    return custom if isinstance(custom, str) else ""


@dataclass(frozen=True)
class Applied:
    status: str  # recorded | duplicate
    event_id: str
    receipt: Receipt | None = None


# What the event is about: the API path of the resource to re-fetch (the webhook body is
# only CRC32-bound to PayPal's signature, so its fields are a trigger, never attested).
RESOURCE_KINDS = {"order_id": "order", "authorization_id": "authorization",
                  "capture_id": "capture", "refund_id": "refund"}
RESOURCE_PATHS = {"order_id": "/v2/checkout/orders/{}",
                  "authorization_id": "/v2/payments/authorizations/{}",
                  "capture_id": "/v2/payments/captures/{}",
                  "refund_id": "/v2/payments/refunds/{}"}
Lookup = Callable[[str], dict[str, Any] | None]


def _subject(event_type: str, ids: Mapping[str, str]) -> tuple[str, str] | None:
    """``(id key, id)`` of the resource an event is about."""
    if event_type.startswith("CHECKOUT.ORDER."):
        key = "order_id"
    elif event_type.startswith("PAYMENT.AUTHORIZATION."):
        key = "authorization_id"
    elif event_type == "PAYMENT.CAPTURE.REFUNDED":
        key = "refund_id"
    elif event_type.startswith("PAYMENT.CAPTURE."):
        key = "capture_id"
    else:
        return None
    value = ids.get(key)
    return (key, value) if value else None


def apply(conn: sqlite3.Connection, ledger: Ledger, verifier: Verifier,
          headers: Mapping[str, str], body: bytes, *, lookup: Lookup | None = None) -> Applied:
    """Verify, look the resource up in PayPal's API, and record its attested state once.
    ``WebhookRefused`` (nothing stored) for anything untrusted or not ours; see the module
    docstring."""
    t, cert_digest = verifier.verify(headers, body)
    body_hash = hashlib.sha256(body).hexdigest()
    try:
        event = json.loads(body)
    except ValueError:
        raise WebhookRefused("the body is not JSON", "body") from None
    if not isinstance(event, dict):
        raise WebhookRefused("the body is not an event", "body")
    eid, etype = event.get("id"), event.get("event_type")
    resource = event.get("resource")
    if not (isinstance(eid, str) and _EVENT_ID.fullmatch(eid) and isinstance(etype, str)
            and re.fullmatch(r"[A-Z][A-Z0-9_.-]{2,80}", etype) and isinstance(resource, dict)):
        raise WebhookRefused("the event lacks an id, a type or a resource", "body")
    with _locked(conn):
        seen = conn.execute("SELECT 1 FROM paypal_events WHERE transmission_id = ? AND "
                            "body_sha256 = ?", (t.transmission_id, body_hash)).fetchone()
    if seen:
        return Applied("duplicate", eid)  # this very delivery was recorded
    subject = _subject(etype, _ids(etype, resource))
    if subject is None:
        raise WebhookRefused("the event names no resource the studio can check",
                             "unknown_resource")
    if lookup is None:
        raise WebhookRefused("PayPal's API is not reachable from here", "lookup_failed")
    from studio.paypal.client import PayPalError

    try:
        api = lookup(RESOURCE_PATHS[subject[0]].format(subject[1]))
    except PayPalError as exc:
        if exc.status == 404:
            raise WebhookRefused("PayPal's API has no such resource",
                                 "unknown_resource") from None
        raise WebhookRefused("PayPal's API could not be read", "lookup_failed") from None
    except Exception:  # noqa: BLE001 - nothing stored; PayPal delivers again
        raise WebhookRefused("PayPal's API could not be read", "lookup_failed") from None
    if not isinstance(api, dict) or api.get("id") != subject[1]:
        raise WebhookRefused("PayPal's API answered about another resource", "lookup_failed")
    api_status = api.get("status")
    if not (isinstance(api_status, str) and re.fullmatch(r"[A-Z_]{1,40}", api_status)):
        raise WebhookRefused("PayPal's API gave no status", "lookup_failed")
    ids = _ids(etype, api)
    ids[subject[0]] = subject[1]
    kind = RESOURCE_KINDS[subject[0]]
    custom = _custom_id(api)
    with _locked(conn), funding.atomic(conn):
        f = _find_funding(conn, ids)
        if f is None or (custom and custom != f.content_sha256):
            raise WebhookRefused("the resource is not one of the studio's fundings",
                                 "not_ours")
        if conn.execute("SELECT 1 FROM paypal_events WHERE resource_kind = ? AND "
                        "resource_id = ? AND api_status = ?",
                        (kind, subject[1], api_status)).fetchone():
            return Applied("duplicate", eid)  # this attested state is already recorded
        if f.order_id and "order_id" not in ids:
            ids = {**ids, "order_id": f.order_id}
        details: dict[str, Any] = {
            # attested by PayPal's API: the resource kind, its status and their class
            "event_class": f"{kind}.{api_status.lower()}", "resource_kind": kind,
            "status": api_status, **ids, "funding_id": f.id, "item_id": f.item_id,
            # from the body (CRC32-bound only): labels, never evidence
            "body_event_type": etype, "body_event_id": eid, "body_sha256": body_hash,
            "transmission_id_sha256": sha256_hex(t.transmission_id),
            "transmission_ts": int(t.when()),
            "signature_sha256": sha256_hex(t.signature), "cert_sha256": cert_digest,
            "verified": "rsa", "resource_source": "api"}
        money = _amount(api)
        if money is not None:
            details["amount_minor"], details["currency"] = money
        if custom:
            details["custom_id_sha256"] = custom  # equal to the funding's ad hash (checked)
        receipt = ledger.append(f.run_id, agent=AGENT, kind=PAYPAL_WEBHOOK_KIND,
                                provider="paypal", conn=conn if ledger.joins(conn) else None,
                                output_sha256=body_hash, details=details)
        conn.execute(
            "INSERT INTO paypal_events (resource_kind, resource_id, api_status, event_id, "
            "event_type, order_id, funding_id, body, body_sha256, transmission_id, "
            "transmission_time, signature, cert_sha256, webhook_id, receipt_id, received_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, subject[1], api_status, eid, etype, ids.get("order_id", ""), f.id,
             body.decode("utf-8", "replace"), body_hash, t.transmission_id,
             t.transmission_time, t.signature, cert_digest, verifier.webhook_id, receipt.id,
             verifier.clock()))
    return Applied("recorded", eid, receipt)


# -- the owner-only export and the offline re-check ------------------------------------------


def export_events(conn: sqlite3.Connection) -> str:
    """``paypal-events.jsonl``: every recorded event with what PayPal signed (owner-only:
    the raw bodies may hold payer data)."""
    cols = ("event_id", "event_type", "body", "transmission_id", "transmission_time",
            "signature", "cert_sha256", "webhook_id")
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM paypal_events "  # noqa: S608
                        "ORDER BY id").fetchall()
    return "".join(json.dumps(dict(zip(cols, r, strict=True)), sort_keys=True) + "\n"
                   for r in rows)


def export_certs(conn: sqlite3.Connection, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for digest, pem in conn.execute("SELECT sha256, pem FROM paypal_certs"):
        (out / f"{digest}.pem").write_text(pem)
        n += 1
    return n


_DIGEST = re.compile(r"[0-9a-f]{64}")


def recheck(events_path: Path, certs_dir: Path, receipts: Iterable[Receipt], *,
            webhook_id: str, roots: list[x509.Certificate] | None = None) -> list[str]:
    """Offline: every exported event verifies with PayPal's key under the verifier's own
    ``webhook_id`` (never the export's), from a pinned cert whose leaf DER hash is its file
    name, the event's and the receipt's ``cert_sha256``, and whose chain reaches a trusted
    root (``roots``, default the ``certifi`` bundle) as of the transmission time, with a
    ``paypal.com`` leaf and CA-only intermediates; every ``paypal.webhook`` receipt has
    exactly one such event with the same body hash and transmission."""
    anchors = roots if roots is not None else load_roots()
    # one delivery = (transmission, body); one body may legitimately recur under another
    # transmission, so receipts are matched on both, one to one, never by body alone
    waiting: dict[tuple[Any, Any], list[Receipt]] = {}
    for r in receipts:
        if r.kind == PAYPAL_WEBHOOK_KIND:
            slot = (r.details.get("transmission_id_sha256"), r.details.get("body_sha256"))
            waiting.setdefault(slot, []).append(r)
    errors: list[str] = []
    for n, line in enumerate(events_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            body = ev["body"].encode()
            t = Transmission(ev["transmission_id"], ev["transmission_time"], ev["signature"],
                             "https://api.paypal.com/", AUTH_ALGO)
            digest = ev["cert_sha256"]
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ValueError("cert_sha256")
            chain = x509.load_pem_x509_certificates((certs_dir / f"{digest}.pem").read_bytes())
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
            errors.append(f"event line {n}: unreadable ({type(exc).__name__})")
            continue
        leaf_digest = hashlib.sha256(chain[0].public_bytes(Encoding.DER)).hexdigest()
        if leaf_digest != digest:
            errors.append(f"event line {n}: the cert file is not the pinned certificate")
            continue
        try:
            leaf = check_chain(chain, anchors, t.when(), load_intermediates())
        except WebhookRefused as exc:
            errors.append(f"event line {n}: PayPal's certificate is not trusted ({exc.code})")
            continue
        key = leaf.public_key()
        try:
            assert isinstance(key, rsa.RSAPublicKey)
            key.verify(base64.b64decode(t.signature), message(t, webhook_id, body),
                       padding.PKCS1v15(), hashes.SHA256())
        except (InvalidSignature, AssertionError, ValueError):
            errors.append(f"event line {n}: PayPal's signature does not verify")
            continue
        delivery = (sha256_hex(t.transmission_id), hashlib.sha256(body).hexdigest())
        matches = waiting.get(delivery) or []
        if not matches:
            errors.append(f"event line {n}: no paypal.webhook receipt for this delivery")
            continue
        r = matches.pop(0)  # each receipt answers for exactly one export line
        if r.details.get("cert_sha256") != digest or r.details.get(
                "signature_sha256") != sha256_hex(t.signature):
            errors.append(f"{r.id}: the receipt does not match PayPal's transmission")
    for left in waiting.values():
        for r in left:
            errors.append(f"{r.id}: paypal.webhook receipt with no signed PayPal event")
    return errors
