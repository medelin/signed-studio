"""E-mail notifications: tell the owner when something needs them.

- **Configured only by environment**: ``STUDIO_MAIL_TRANSPORT`` = ``jmap`` (primary: RFC
  8620/8621 over HTTPS, ``STUDIO_JMAP_SESSION_URL``, Basic auth with ``STUDIO_MAIL_USER`` /
  ``STUDIO_MAIL_PASSWORD``), ``smtp`` (submission over implicit TLS, ``STUDIO_SMTP_HOST``,
  ``STUDIO_SMTP_PORT`` = 465, AUTH login) or ``mock`` (mails kept in ``Notifier.outbox``);
  ``STUDIO_MAIL_FROM`` is the sender. Unset, the transport follows what is configured;
  nothing configured = notifications off and every call a no-op. Both real transports use a
  10 s timeout.
- **Never in the request's way**: ``Notifier.event`` only queues work for one background
  thread; resolving recipients, the rate limit, the send and the receipt happen there. A
  failure is logged without a secret (exception class and SMTP code) and receipted.
- **Who**: studio events (approval waiting, campaign created/launched/paused, new leads,
  recommendations) go to every active, non-demo account that has an address and the
  category on; security events go to the one account concerned and cannot be turned off.
  The shared judge account never sends mail.
- **No personal data in a mail body**: a count and a link, never a lead's name or contact.
- **Rate limit**: leads and recommendations at most once per account per ten minutes; the
  counts of the suppressed events are added to the next mail.
- **WhatsApp** (``studio.whatsapp.Channel``, optional): the same studio events with a
  template (approval waiting, new leads, ad disapproved) to accounts that opted in with a
  number; it runs on the same worker, after the mails, with its own rate-limit state.
- **Receipts**: one ``notify.send`` tool-call receipt per mail (event, category, language,
  status, account id; no address, no subject, no body).
"""

from __future__ import annotations

import html
import logging
import queue
import smtplib
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any
from urllib.parse import urlsplit

import httpx

from studio.approvals import transaction
from studio.receipts import Ledger

LOG = logging.getLogger("studio.notify")

TIMEOUT_S = 10.0
COALESCE_S = 600.0
DEFAULT_PORT = 465
DEFAULT_FROM = "Muse Studio Lite <muse@udun.eu>"
MOCK_HOST = "mock"
OUTBOX_MAX = 200
RUN_ID = "notify"
AGENT = "notifier"
CATEGORIES = ("campaigns", "leads", "security")
TOGGLEABLE = ("campaigns", "leads")


@dataclass(frozen=True)
class Event:
    category: str
    path: str  # the page the mail links to ('' = no link)
    coalesce: bool = False


EVENTS: dict[str, Event] = {
    "approval_waiting": Event("campaigns", "/studio"),
    "campaign_created": Event("campaigns", "/studio"),
    "campaign_launched": Event("campaigns", "/studio"),
    "campaign_paused": Event("campaigns", "/studio"),
    "lead_new": Event("leads", "/leads", coalesce=True),
    "insights_ready": Event("campaigns", "/report", coalesce=True),
    "ad_disapproved": Event("campaigns", "/studio"),  # hook name: ``ad.disapproved``
    "password_changed": Event("security", "/account#password"),
    "two_factor_on": Event("security", "/account#two-factor"),
    "two_factor_off": Event("security", "/account#two-factor"),
    "signin": Event("security", "/account#sessions"),
    "deletion_requested": Event("security", ""),
    "export_ready": Event("security", "/account#export"),
}

ALIASES = {"ad.disapproved": "ad_disapproved"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_state (
    account_id TEXT NOT NULL,
    event TEXT NOT NULL,
    last_ts REAL NOT NULL,
    pending INTEGER NOT NULL DEFAULT 0,  -- events held back since the last mail
    PRIMARY KEY (account_id, event)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


TRANSPORTS = ("jmap", "smtp", "mock")


@dataclass(frozen=True)
class MailConfig:
    transport: str = ""  # "" = notifications off
    jmap_url: str = ""
    host: str = ""
    port: int = DEFAULT_PORT
    user: str = ""
    password: str = field(default="", repr=False)
    sender: str = DEFAULT_FROM

    @property
    def enabled(self) -> bool:
        return bool(self.transport)

    @property
    def mock(self) -> bool:
        return self.transport == "mock"

    @classmethod
    def from_env(cls, e: Mapping[str, str]) -> MailConfig:
        """Raises ``ValueError`` naming the variable. ``STUDIO_MAIL_TRANSPORT`` picks the
        transport; unset, it follows what is configured (a JMAP session URL, else an SMTP
        host, ``mock`` included); nothing configured = off."""
        sender = e.get("STUDIO_MAIL_FROM", "") or DEFAULT_FROM
        host = e.get("STUDIO_SMTP_HOST", "")
        jmap_url = e.get("STUDIO_JMAP_SESSION_URL", "")
        transport = e.get("STUDIO_MAIL_TRANSPORT", "").lower() or (
            "jmap" if jmap_url else "mock" if host == MOCK_HOST else "smtp" if host else "")
        if not transport:
            return cls(sender=sender)
        if transport not in TRANSPORTS:
            raise ValueError("STUDIO_MAIL_TRANSPORT must be jmap, smtp or mock")
        address = parseaddr(sender)[1]
        if "@" not in address or any(c in sender for c in "\r\n"):
            raise ValueError("STUDIO_MAIL_FROM must be an address, e.g. 'Name <a@b.example>'")
        if transport == "mock":
            return cls("mock", sender=sender)
        user = e.get("STUDIO_MAIL_USER", "") or e.get("STUDIO_SMTP_USER", "")
        password = e.get("STUDIO_MAIL_PASSWORD", "") or e.get("STUDIO_SMTP_PASSWORD", "")
        if not (user and password):
            raise ValueError("STUDIO_MAIL_USER and STUDIO_MAIL_PASSWORD are required with "
                             "STUDIO_MAIL_TRANSPORT")
        if transport == "jmap":
            parts = urlsplit(jmap_url)
            if parts.scheme != "https" or not parts.hostname or parts.username \
                    or any(c.isspace() for c in jmap_url):
                raise ValueError("STUDIO_JMAP_SESSION_URL must be an https URL, e.g. "
                                 "https://mail.example.org/jmap/session")
            return cls("jmap", jmap_url=jmap_url, user=user, password=password, sender=sender)
        if not _host_ok(host) or host == MOCK_HOST:
            raise ValueError("STUDIO_SMTP_HOST must be a host name")
        try:
            port = int(e.get("STUDIO_SMTP_PORT", "") or DEFAULT_PORT)
        except ValueError:
            raise ValueError("STUDIO_SMTP_PORT must be an integer") from None
        if not 1 <= port <= 65535:
            raise ValueError("STUDIO_SMTP_PORT must be 1-65535")
        return cls("smtp", host=host, port=port, user=user, password=password, sender=sender)


def _host_ok(host: str) -> bool:
    return bool(host) and len(host) <= 253 and all(
        c.isalnum() or c in ".-" for c in host) and not host.startswith(("-", "."))


class MailError(Exception):
    """A transport refused or failed; ``code`` is safe to log (no address, no secret)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SmtpTransport:
    """SMTP submission over implicit TLS (port 465), AUTH login."""

    def __init__(self, cfg: MailConfig) -> None:
        self.cfg = cfg

    def send(self, msg: EmailMessage) -> None:
        with smtplib.SMTP_SSL(self.cfg.host, self.cfg.port, timeout=TIMEOUT_S) as s:
            s.login(self.cfg.user, self.cfg.password)
            s.send_message(msg)


CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SUBMISSION = "urn:ietf:params:jmap:submission"


class JmapTransport:
    """RFC 8620/8621 over HTTPS: a session GET, a one-off lookup of the identity for the
    from-address and the Sent mailbox (cached per process), then one POST per mail that
    creates the Email in Sent and submits it (``$draft`` is dropped on success). Basic auth,
    no redirects (the credentials go only where configured), timeout 10 s."""

    def __init__(self, cfg: MailConfig) -> None:
        self.cfg = cfg
        self._ids: tuple[str, str, str, str] | None = None  # api url, account, identity, sent
        self._lock = threading.Lock()

    def _client(self) -> httpx.Client:
        return httpx.Client(auth=(self.cfg.user, self.cfg.password), timeout=TIMEOUT_S,
                            follow_redirects=False)

    @staticmethod
    def _call(c: httpx.Client, url: str, calls: list[Any]) -> list[Any]:
        r = c.post(url, json={"using": [CORE, MAIL, SUBMISSION], "methodCalls": calls})
        if r.status_code != 200:
            raise MailError(f"http_{r.status_code}")
        out = r.json().get("methodResponses")
        if not isinstance(out, list):
            raise MailError("bad_response")
        for name, args, _ in out:
            if name == "error":
                raise MailError("jmap_" + str(args.get("type", "error"))[:40])
        return out

    def _setup(self, c: httpx.Client) -> tuple[str, str, str, str]:
        r = c.get(self.cfg.jmap_url)
        if r.status_code != 200:
            raise MailError(f"session_http_{r.status_code}")
        sess = r.json()
        api = str(sess.get("apiUrl", ""))
        account = str((sess.get("primaryAccounts") or {}).get(SUBMISSION, ""))
        if not api.startswith("https://") or not account:
            raise MailError("bad_session")
        out = self._call(c, api, [
            ["Identity/get", {"accountId": account}, "i"],
            ["Mailbox/query", {"accountId": account, "filter": {"role": "sent"}}, "m"]])
        want = parseaddr(self.cfg.sender)[1].lower()
        identity = next((str(i["id"]) for i in out[0][1].get("list", [])
                         if str(i.get("email", "")).lower() == want), "")
        sent = next(iter(out[1][1].get("ids", [])), "")
        if not identity or not sent:
            raise MailError("no_identity" if not identity else "no_sent_mailbox")
        return api, account, identity, str(sent)

    def send(self, msg: EmailMessage) -> None:
        text, page = msg.get_body(("plain",)), msg.get_body(("html",))
        name, address = parseaddr(str(msg["From"]))
        try:
            with self._client() as c:
                with self._lock:
                    if self._ids is None:
                        self._ids = self._setup(c)
                    api, account, identity, sent = self._ids
                email: dict[str, Any] = {
                    "mailboxIds": {sent: True}, "keywords": {"$seen": True, "$draft": True},
                    "from": [{"name": name, "email": address}],
                    "to": [{"email": str(msg["To"])}], "subject": str(msg["Subject"]),
                    "header:Auto-Submitted:asText": "auto-generated",
                    "bodyStructure": {"type": "multipart/alternative", "subParts": [
                        {"partId": "t", "type": "text/plain"},
                        {"partId": "h", "type": "text/html"}]},
                    "bodyValues": {"t": {"value": text.get_content() if text else ""},
                                   "h": {"value": page.get_content() if page else ""}}}
                out = self._call(c, api, [
                    ["Email/set", {"accountId": account, "create": {"m": email}}, "e"],
                    ["EmailSubmission/set", {
                        "accountId": account,
                        "create": {"s": {"identityId": identity, "emailId": "#m"}},
                        "onSuccessUpdateEmail": {"#s": {"keywords/$draft": None}}}, "s"]])
            for step, key in ((out[0], "m"), (out[1], "s")):
                failed = (step[1].get("notCreated") or {}).get(key)
                if failed is not None or key not in (step[1].get("created") or {}):
                    raise MailError(step[0].split("/")[0].lower() + "_" + str(
                        (failed or {}).get("type", "failed"))[:40])
        except Exception:
            self._ids = None  # a changed identity or mailbox is found again next time
            raise


_ACTIVE = ("SELECT id, email, lang FROM accounts WHERE status = 'active' AND is_demo = 0 "
           "AND email != ''")
_RECIPIENTS = {"campaigns": _ACTIVE + " AND notify_campaigns = 1",
               "leads": _ACTIVE + " AND notify_leads = 1"}


@dataclass(frozen=True)
class Recipient:
    account_id: str
    email: str
    lang: str


@dataclass(frozen=True)
class Sent:
    """One mail kept by the mock outbox."""
    to: str
    event: str
    subject: str
    text: str
    html: str


def set_preferences(conn: sqlite3.Connection, account_id: str, *, campaigns: bool,
                    leads: bool) -> None:
    with transaction(conn) as c:
        c.execute("UPDATE accounts SET notify_campaigns = ?, notify_leads = ? WHERE id = ?",
                  (int(campaigns), int(leads), account_id))


def preferences(conn: sqlite3.Connection, account_id: str) -> dict[str, bool]:
    """Category → on. ``security`` is always on."""
    with _locked(conn):
        row = conn.execute("SELECT notify_campaigns, notify_leads FROM accounts WHERE id = ?",
                           (account_id,)).fetchone()
    return {"campaigns": bool(row[0]) if row else True, "leads": bool(row[1]) if row else True,
            "security": True}


def _locked(conn: Any) -> Any:
    return getattr(conn, "lock", None) or threading.RLock()


class Notifier:
    """Queue events; one worker thread resolves, rate-limits, sends and receipts them."""

    def __init__(self, conn: sqlite3.Connection, ledger: Ledger, mail: MailConfig,
                 public_url: str = "", *, clock: Callable[[], float] = time.time,
                 whatsapp: Any = None) -> None:
        self.conn = conn
        self.ledger = ledger
        self.mail = mail
        self.public_url = public_url.rstrip("/")
        self.clock = clock
        self.outbox: list[Sent] = []
        self.whatsapp = whatsapp  # a studio.whatsapp.Channel, or None
        self.transport: SmtpTransport | JmapTransport | None = (
            JmapTransport(mail) if mail.transport == "jmap"
            else SmtpTransport(mail) if mail.transport == "smtp" else None)
        self._queue: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.mail.enabled

    # -- API used by the hooks ---------------------------------------------------------

    def event(self, name: str, *, count: int = 1) -> None:
        """A studio event for every account that wants it; returns at once."""
        name = ALIASES.get(name, name)
        if not self.enabled and self.whatsapp is None:
            return
        ev = EVENTS[name]
        self._submit(lambda: self._deliver(name, ev, None, count))

    def security(self, name: str, account: Any) -> None:
        """A security event for one account (its address is read now: the account may be
        erased before the worker runs). ``account`` is a ``studio.accounts.Account``."""
        if not self.enabled or account.is_demo or account.status != "active":
            return
        if not account.email:
            return
        who = Recipient(account.id, account.email, account.lang)
        ev = EVENTS[name]
        self._submit(lambda: self._deliver(name, ev, who, 1))

    def flush(self, timeout: float = 15.0) -> None:
        """Wait until everything queued so far was handled (tests, CLI exit)."""
        if self._thread is None:
            return
        done = threading.Event()
        self._queue.put(done.set)
        done.wait(timeout)

    # -- worker --------------------------------------------------------------------------

    def _submit(self, job: Callable[[], None]) -> None:
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="notify", daemon=True)
                self._thread.start()
        self._queue.put(job)

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - a notification never breaks the studio
                LOG.warning("notification job failed: %s", type(exc).__name__)

    def _recipients(self, ev: Event) -> list[Recipient]:
        sql = _RECIPIENTS[ev.category]
        with _locked(self.conn):
            rows = self.conn.execute(sql).fetchall()
        return [Recipient(r[0], r[1], r[2]) for r in rows]

    def _due(self, who: Recipient, name: str, count: int) -> int:
        """Reserve a mail for a coalescing event: the count to report (held-back events
        included), or 0 when one went out less than ``COALESCE_S`` ago."""
        now = self.clock()
        with transaction(self.conn) as c:
            row = c.execute("SELECT last_ts, pending FROM notify_state WHERE account_id = ? "
                            "AND event = ?", (who.account_id, name)).fetchone()
            if row and now - row[0] < COALESCE_S:
                c.execute("UPDATE notify_state SET pending = pending + ? WHERE account_id = ? "
                          "AND event = ?", (count, who.account_id, name))
                return 0
            total = count + (row[1] if row else 0)
            c.execute("INSERT INTO notify_state (account_id, event, last_ts, pending) "
                      "VALUES (?, ?, ?, 0) ON CONFLICT (account_id, event) DO UPDATE SET "
                      "last_ts = excluded.last_ts, pending = 0",
                      (who.account_id, name, now))
            return total

    def _deliver(self, name: str, ev: Event, who: Recipient | None, count: int) -> None:
        if self.enabled:
            self._deliver_mail(name, ev, who, count)
        if self.whatsapp is not None and who is None:
            self.whatsapp.deliver(
                name, ev.category, count, coalesce=ev.coalesce,
                due=lambda w, key, n: self._due(Recipient(w.account_id, "", w.lang), key, n))

    def _deliver_mail(self, name: str, ev: Event, who: Recipient | None, count: int) -> None:
        for r in [who] if who else self._recipients(ev):
            n = count
            if ev.coalesce:
                n = self._due(r, name, count)
                if not n:
                    continue
            self._send_one(name, ev, r, n)

    def _send_one(self, name: str, ev: Event, who: Recipient, count: int) -> None:
        status, detail = "ok", ""
        msg = build_message(name, ev, who, count, sender=self.mail.sender,
                            base_url=self.public_url)
        try:
            if self.transport is None:
                self._keep(who, name, msg)
            else:
                self.transport.send(msg)
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            status = "failed"
            code = getattr(exc, "smtp_code", "") or getattr(exc, "code", "")
            detail = f"{type(exc).__name__}{f' {code}' if code else ''}"
            LOG.warning("notification %s failed: %s", name, detail)
        try:
            self.ledger.append(RUN_ID, agent=AGENT, kind="tool_call", details={
                "tool": "notify.send", "status": status, "side_effects": "notice",
                "event": name, "category": ev.category, "lang": who.lang or "en",
                "account": who.account_id, "count": count,
                "transport": self.mail.transport,
                **({"error": detail} if detail else {})})
        except Exception as exc:  # noqa: BLE001
            LOG.warning("notification receipt failed: %s", type(exc).__name__)

    def _keep(self, who: Recipient, name: str, msg: EmailMessage) -> None:
        text = msg.get_body(("plain",))
        page = msg.get_body(("html",))
        self.outbox.append(Sent(who.email, name, str(msg["Subject"]),
                                text.get_content() if text else "",
                                page.get_content() if page else ""))
        del self.outbox[:-OUTBOX_MAX]


def build_message(name: str, ev: Event, who: Recipient, count: int, *, sender: str,
                  base_url: str) -> EmailMessage:
    """Plain text plus a simple HTML alternative, in the account's language."""
    from studio.ui import i18n

    lang = i18n.norm(who.lang)
    subject = str(i18n.text(lang, f"notify.{name}.subject"))
    body = str(i18n.text(lang, f"notify.{name}.body", count=count))
    link = base_url + ev.path if (base_url and ev.path) else ""
    foot_key = "notify.footer_security" if ev.category == "security" else "notify.footer"
    account_url = base_url + "/account#notifications" if base_url else ""
    footer = str(i18n.text(lang, foot_key))
    lines = [body]
    if link:
        lines += ["", str(i18n.text(lang, "notify.open")) + " " + link]
    lines += ["", "--", footer]
    if account_url and ev.category != "security":
        lines.append(account_url)
    rows = [f"<p>{html.escape(body)}</p>"]
    if link:
        rows.append(f'<p><a href="{html.escape(link)}">'
                    f"{html.escape(str(i18n.text(lang, 'notify.open')))}</a></p>")
    foot = html.escape(footer)
    if account_url and ev.category != "security":
        foot += f' <a href="{html.escape(account_url)}">{html.escape(account_url)}</a>'
    rows.append(f'<p style="color:#666;font-size:13px">{foot}</p>')
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = who.email
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content("\n".join(lines))
    msg.add_alternative("<!doctype html><html lang=\"" + lang + "\"><body>"
                        + "".join(rows) + "</body></html>", subtype="html")
    return msg


__all__ = ["CATEGORIES", "EVENTS", "MailConfig", "Notifier", "TOGGLEABLE", "ensure_schema",
           "preferences", "set_preferences"]
