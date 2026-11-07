"""PayPal Vault v3: the owner saves their PayPal once, with the buyer present, so that runs
under a signed mandate can be paid later without them (``studio.mandates``).

The flow (buyer present, on ``/account``):

1. ``Vault.start``: ``POST /v3/vault/setup-tokens`` (``payment_source.paypal``:
   ``usage_type: MERCHANT``, ``customer_type: CONSUMER``, one token per approval, the
   return and cancel URLs) → the buyer is sent to PayPal's ``approve`` link.
2. PayPal sends the buyer back to the return URL with ``approval_token_id`` (the setup
   token). ``Vault.finish`` reads the setup token (``APPROVED``) and exchanges it with
   ``POST /v3/vault/payment-tokens``. The answer's payment-token id is the **vault id**.
3. ``Vault.delete``: the row is marked deleted first (so it can no longer be charged, even
   if PayPal is down), then ``DELETE /v3/vault/payment-tokens/{id}``.

What is stored: the studio's own reference (``vlt_…``), the account, the setup token id,
the vault id and its SHA-256, the status and times. **No payer data**: PayPal's answers
carry the payer's e-mail, name and payer id, and none of it is kept. The vault id never
enters a receipt, a model prompt or an API answer: receipts and the signed mandate carry
its SHA-256 (``token_sha256``); only ``studio.paypal.money`` reads the id itself, to put
it in a vault order's ``payment_source``.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from studio.approvals import transaction
from studio.paypal.client import PayPalClient, PayPalError, Reply, path_id, request_id
from studio.paypal.orders import BRAND
from studio.receipts import PAYPAL_KIND, Ledger, is_opaque, new_id, sha256_hex

AGENT = "treasury"
PROVIDER = "paypal"
STATUSES = ("setup", "active", "deleted", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS paypal_vault (
    id TEXT PRIMARY KEY,                  -- vlt_…: the studio's own reference
    account_id TEXT NOT NULL,
    setup_token_id TEXT NOT NULL UNIQUE,
    token_id TEXT UNIQUE,                 -- PayPal's vault id; never in a receipt
    token_sha256 TEXT NOT NULL DEFAULT '',-- what receipts and mandates name
    status TEXT NOT NULL CHECK (status IN ('setup', 'active', 'deleted', 'failed')),
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS paypal_vault_account ON paypal_vault (account_id, status);
"""


class VaultError(Exception):
    def __init__(self, message: str, code: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def token_sha256(token_id: str) -> str:
    """The digest a mandate signs and receipts carry instead of the vault id."""
    return sha256_hex("paypal-vault\x1f" + token_id)


@dataclass(frozen=True)
class Saved:
    """One saved PayPal of an account (no payer data)."""

    id: str
    account_id: str
    status: str
    token_sha256: str
    created_ts: float
    updated_ts: float

    def view(self) -> dict[str, Any]:
        return {"vault_ref": self.id, "status": self.status,
                "token_sha256": self.token_sha256, "created_ts": self.created_ts}


# -- PayPal calls --------------------------------------------------------------------------


def setup_body(return_url: str, cancel_url: str) -> dict[str, Any]:
    return {"payment_source": {"paypal": {
        "description": "Signed Studio: ad budgets your assistant may pay within the "
                       "mandate you sign",
        "usage_pattern": "IMMEDIATE", "usage_type": "MERCHANT", "customer_type": "CONSUMER",
        "permit_multiple_payment_tokens": False,
        "experience_context": {"brand_name": BRAND, "shipping_preference": "NO_SHIPPING",
                               "return_url": return_url, "cancel_url": cancel_url}}}}


def _id_status(reply: Reply) -> tuple[str, str]:
    data = reply.json
    tid, status = data.get("id"), data.get("status", "")
    if not isinstance(tid, str) or not tid:
        raise PayPalError(reply.status, "UNEXPECTED_RESPONSE", "NO_ID", unknown=True)
    return path_id(tid), status if isinstance(status, str) else ""


def _approve_link(data: dict[str, Any]) -> str:
    for link in data.get("links") or []:
        if isinstance(link, dict) and link.get("rel") in ("approve", "payer-action"):
            href = link.get("href")
            if isinstance(href, str) and href.startswith("https://www.sandbox.paypal.com/"):
                return href
    return ""


def create_setup_token(client: PayPalClient, body: dict[str, Any], *,
                       request_id: str) -> tuple[str, str, str, str]:
    """``(setup token id, status, approve URL, body_sha256)``."""
    reply = client.post("/v3/vault/setup-tokens", body, request_id=request_id)
    sid, status = _id_status(reply)
    return sid, status, _approve_link(reply.json), reply.body_sha256


def get_setup_token(client: PayPalClient, setup_token_id: str) -> tuple[str, str]:
    """``(status, body_sha256)``."""
    reply = client.get(f"/v3/vault/setup-tokens/{path_id(setup_token_id)}")
    return _id_status(reply)[1], reply.body_sha256


def create_payment_token(client: PayPalClient, setup_token_id: str, *,
                         request_id: str) -> tuple[str, str]:
    """``(vault id, body_sha256)``. The payer data in the answer is dropped here."""
    reply = client.post("/v3/vault/payment-tokens",
                        {"payment_source": {"token": {"id": path_id(setup_token_id),
                                                      "type": "SETUP_TOKEN"}}},
                        request_id=request_id)
    return _id_status(reply)[0], reply.body_sha256


def delete_payment_token(client: PayPalClient, token_id: str) -> str:
    return client.delete(f"/v3/vault/payment-tokens/{path_id(token_id)}").body_sha256


# -- the service ---------------------------------------------------------------------------

_COLUMNS = "id, account_id, status, token_sha256, created_ts, updated_ts"


class Vault:
    """Saved PayPal methods of the studio's accounts, in ``conn``; receipts in ``ledger``
    (one run per saved method, named by its ``vlt_…`` reference)."""

    def __init__(self, conn: sqlite3.Connection, ledger: Ledger, client: PayPalClient, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.conn = conn
        self.ledger = ledger
        self.client = client
        self.clock = clock

    def _receipt(self, ref: str, step: str, **details: Any) -> None:
        self.ledger.append(ref, agent=AGENT, kind=PAYPAL_KIND, provider=PROVIDER,
                           details={"step": step, "vault_ref": ref,
                                    **{k: v for k, v in details.items()
                                       if v not in (None, "")}})

    def _fail(self, ref: str, step: str, exc: PayPalError) -> None:
        self._receipt(ref, step, ok=False, error=exc.name or "HTTP_ERROR", issue=exc.issue,
                      http_status=exc.status, debug_id=exc.debug_id or None)

    def start(self, account_id: str, *, return_url: str, cancel_url: str) -> tuple[str, str]:
        """A setup token for ``account_id``: ``(vault ref, PayPal approve URL)``. The
        return URL gets the reference appended, so the buyer's return names its row."""
        if not is_opaque(account_id):
            raise VaultError("not an account", "not_found", 404)
        ref = new_id("vlt_", 8)
        rid = request_id(ref, "setup")
        body = setup_body(f"{return_url}?vault_ref={ref}", cancel_url)
        try:
            sid, status, approve, digest = create_setup_token(self.client, body,
                                                              request_id=rid)
        except PayPalError as exc:
            self._fail(ref, "paypal.vault_setup", exc)
            raise
        if not approve:
            self._receipt(ref, "paypal.vault_setup", ok=False, error="NO_APPROVE_LINK",
                          status=status)
            raise VaultError("PayPal returned no approval link", "no_approve_link", 502)
        now = self.clock()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO paypal_vault (id, account_id, setup_token_id, status, "
                "created_ts, updated_ts) VALUES (?, ?, ?, 'setup', ?, ?)",
                (ref, account_id, sid, now, now))
        self._receipt(ref, "paypal.vault_setup", ok=True, status=status,
                      setup_token_sha256=sha256_hex(sid), body_sha256=digest)
        return ref, approve

    def setup_token_of(self, account_id: str, ref: str) -> str:
        row = self.conn.execute("SELECT setup_token_id, status FROM paypal_vault WHERE id = ? "
                                "AND account_id = ?", (ref, account_id)).fetchone()
        if row is None:
            raise VaultError("no such saved PayPal", "not_found", 404)
        return str(row[0])

    def finish(self, account_id: str, ref: str, setup_token_id: str = "") -> Saved:
        """After the buyer approved: exchange the setup token for a vault token. Only the
        account that started it, only its own setup token (``setup_token_id``, when PayPal
        passes it back, must be the stored one)."""
        row = self.conn.execute("SELECT setup_token_id, status FROM paypal_vault WHERE id = ? "
                                "AND account_id = ?", (ref, account_id)).fetchone()
        if row is None:
            raise VaultError("no such saved PayPal", "not_found", 404)
        sid, status = str(row[0]), str(row[1])
        if setup_token_id and setup_token_id != sid:
            raise VaultError("PayPal returned another setup token", "not_found", 404)
        if status == "active":
            return self.get(ref)  # a reload of the return page
        if status != "setup":
            raise VaultError("this saved PayPal is no longer being set up", "wrong_state")
        try:
            got, digest = get_setup_token(self.client, sid)
        except PayPalError as exc:
            self._fail(ref, "paypal.vault_save", exc)
            raise
        if got != "APPROVED":
            self._receipt(ref, "paypal.vault_save", ok=False, error="NOT_APPROVED",
                          status=got, body_sha256=digest)
            raise VaultError("the buyer has not approved saving PayPal yet",
                             "not_approved_yet")
        try:
            token, digest = create_payment_token(self.client, sid,
                                                 request_id=request_id(ref, "save", sid))
        except PayPalError as exc:
            self._fail(ref, "paypal.vault_save", exc)
            raise
        digest_token = token_sha256(token)
        now = self.clock()
        with transaction(self.conn):
            # PayPal answers the same vault id when the same payer saves PayPal again: on
            # this account the new save takes it over (its SHA-256, which mandates name, is
            # unchanged); on another account it is refused
            held = self.conn.execute("SELECT id, account_id FROM paypal_vault WHERE token_id = ? "
                                     "AND id != ?", (token, ref)).fetchone()
            if held is not None and held[1] != account_id:
                self.conn.execute("UPDATE paypal_vault SET status = 'failed', updated_ts = ? "
                                  "WHERE id = ? AND status = 'setup'", (now, ref))
                cur = None
            else:
                if held is not None:
                    self.conn.execute("UPDATE paypal_vault SET token_id = NULL, status = 'deleted', "
                                      "updated_ts = ? WHERE id = ?", (now, held[0]))
                cur = self.conn.execute(
                    "UPDATE paypal_vault SET token_id = ?, token_sha256 = ?, status = 'active', "
                    "updated_ts = ? WHERE id = ? AND status = 'setup'",
                    (token, digest_token, now, ref))
        if cur is None:
            self._receipt(ref, "paypal.vault_save", ok=False, error="SAVED_ELSEWHERE",
                          status="ACTIVE", vault_sha256=digest_token, body_sha256=digest)
            raise VaultError("this PayPal is already saved on another account", "saved_elsewhere")
        if held is not None:
            self._receipt(held[0], "paypal.vault_replaced", ok=True, status="REPLACED",
                          vault_sha256=digest_token, replaced_by=ref)
        if cur.rowcount != 1:
            raise VaultError("this saved PayPal changed meanwhile", "conflict")
        self._receipt(ref, "paypal.vault_save", ok=True, status="ACTIVE",
                      vault_sha256=digest_token, body_sha256=digest)
        return self.get(ref)

    def delete(self, account_id: str, ref: str) -> Saved:
        """Stop charges at once (local), then delete the token at PayPal."""
        row = self.conn.execute("SELECT token_id, status FROM paypal_vault WHERE id = ? AND "
                                "account_id = ?", (ref, account_id)).fetchone()
        if row is None or row[1] == "deleted":
            raise VaultError("no such saved PayPal", "not_found", 404)
        with transaction(self.conn):
            self.conn.execute("UPDATE paypal_vault SET status = 'deleted', updated_ts = ? "
                              "WHERE id = ?", (self.clock(), ref))
        saved = self.get(ref)
        if row[0]:
            try:
                digest = delete_payment_token(self.client, str(row[0]))
            except PayPalError as exc:
                if exc.status != 404:  # already gone at PayPal: deleted all the same
                    self._fail(ref, "paypal.vault_delete", exc)
                    return saved
                digest = ""
            self._receipt(ref, "paypal.vault_delete", ok=True, status="DELETED",
                          vault_sha256=saved.token_sha256, body_sha256=digest)
        return saved

    def get(self, ref: str) -> Saved:
        row = self.conn.execute(f"SELECT {_COLUMNS} FROM paypal_vault WHERE id = ?",  # noqa: S608
                                (ref,)).fetchone()
        if row is None:
            raise VaultError("no such saved PayPal", "not_found", 404)
        return Saved(*row)

    def of_account(self, account_id: str) -> list[Saved]:
        rows = self.conn.execute(f"SELECT {_COLUMNS} FROM paypal_vault WHERE "  # noqa: S608
                                 "account_id = ? AND status IN ('active', 'deleted') "
                                 "ORDER BY created_ts DESC LIMIT 20", (account_id,)).fetchall()
        return [Saved(*r) for r in rows]

    def active(self, account_id: str, digest: str) -> tuple[str, str] | None:
        """``(vault ref, PayPal vault id)`` of the account's active saved PayPal whose
        SHA-256 is ``digest``; ``None`` when there is none (deleted, never saved, another
        account's). Only the money step reads the id."""
        row = self.conn.execute("SELECT id, token_id FROM paypal_vault WHERE account_id = ? "
                                "AND token_sha256 = ? AND status = 'active'",
                                (account_id, digest)).fetchone()
        return (str(row[0]), str(row[1])) if row and row[1] else None
