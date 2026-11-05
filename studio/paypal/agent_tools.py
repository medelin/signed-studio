"""The PayPal Agent Toolkit's read tools, run on the studio's own receipted sandbox client.

``studio/vendor/paypal_agent_toolkit`` is PayPal's tool registry (parameter models,
prompts, handlers; Apache-2.0) without its HTTP client. Each handler calls
``client.get(uri, payload)``; here that client is an adapter over ``PayPalClient`` (the
constant sandbox base URL, OAuth, retries), so:

- only four read tools are reachable (``READ_TOOLS``); every write tool and
  ``get_merchant_insights`` (not available in the sandbox) is refused before any call;
- every HTTP call leaves one ``paypal.call`` receipt (``step: paypal.agent_tool``) with the
  tool name, the path **without its query**, the status, ``ok`` and the SHA-256 of PayPal's
  answer: no payer data, no query string;
- the answer is stripped of payer fields (``redact``) and reduced to the calling account's
  own money (``only_own``): the sandbox merchant is shared by every account, PayPal's
  transaction list is not.

The receipts go in the chain ``chain_id`` the caller names (see ``tools_chain``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

from pydantic import ValidationError

from studio.paypal.client import PayPalClient, PayPalError
from studio.receipts import PAYPAL_KIND, Ledger, sha256_hex
from studio.vendor.paypal_agent_toolkit.shared.tools import tools as _REGISTRY

READ_TOOLS: frozenset[str] = frozenset(
    {"list_transactions", "get_order_details", "list_disputes", "get_dispute"})
MAX_CALLS_PER_TOOL = 12  # HTTP calls one tool run may make (the by-id search pages months)
MAX_SEARCH_MONTHS = 12
PAGE_SIZE = 500  # list_transactions: the one page size an agent gets (always page 1)
PAGING_KEYS = ("page", "total_pages", "total_items", "has_more", "links")
AGENT = "treasury"
PROVIDER = "paypal"
STEP = "paypal.agent_tool"
# Receipt chains of an account's toolkit calls: ``acct:<account id>:paypal-tools`` (an
# opaque ledger id: ``Ledger.validate_ids``).
TOOLS_CHAIN_PREFIX = "acct:"
TOOLS_CHAIN_SUFFIX = ":paypal-tools"

DROP_KEYS = frozenset({"payer_info", "shipping_info", "payer", "payment_source", "buyer",
                       "seller", "email_address", "phone", "name", "address",
                       # free text a payer or an agent typed
                       "transaction_subject", "transaction_note", "messages", "note", "notes",
                       "reason", "buyer_comments", "memo"})
_EMAIL = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[^\s@<>\"']+")


def tools_chain(account_id: str) -> str:
    """The receipt chain of an account's toolkit calls."""
    return f"{TOOLS_CHAIN_PREFIX}{account_id}{TOOLS_CHAIN_SUFFIX}"


class ToolRefused(Exception):
    """A tool call was not made or not answered. ``code``: ``not_allowed`` (not a read
    tool, or not this account's money), ``invalid_arguments``, ``paypal_error``."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class AccountRefs:
    """What identifies one account's money at PayPal: the ``content_sha256`` its fundings
    put in ``custom_id``/``custom_field`` and the PayPal ids of their order, authorization
    and capture."""

    content_sha256: frozenset[str]
    paypal_ids: frozenset[str]


def _api_owner(conn: Any) -> Any:
    def owner(brief_id: str) -> str | None:
        row = conn.execute("SELECT account_id FROM api_briefs WHERE brief_id = ?",
                           (brief_id,)).fetchone()
        return str(row[0]) if row else None
    return owner


def _owned_rows(conn: Any, account_id: str) -> list[tuple[Any, ...]]:
    """``(funding id, content_sha256, order, authorization, capture)`` of the fundings
    this account may see, by the app's own ownership rule. Call under ``conn.lock``."""
    from studio.routes_studio import STUDIO, approval_owner  # late: routes import this layer

    api_owner = _api_owner(conn)
    out = []
    rows = conn.execute("SELECT run_id, item_id, id, content_sha256, order_id, "
                        "authorization_id, capture_id FROM fundings").fetchall()
    for run_id, item_id, *rest in rows:
        pending = SimpleNamespace(action="campaign.create", id="", run_id=run_id,
                                  item_id=item_id)
        owner = approval_owner(conn, pending, api_owner)
        if owner is not None and (owner == STUDIO or owner == account_id):
            out.append(tuple(rest))
    return out


def owned_funding_ids(conn: Any, account_id: str) -> list[str]:
    """Ids of the fundings this account may see (same rule as ``account_refs``). Call
    under ``conn.lock``."""
    return [str(r[0]) for r in _owned_rows(conn, account_id)]


def account_refs(conn: Any, account_id: str) -> AccountRefs:
    """The references of the fundings this account may see, by the app's own ownership rule
    (``routes_studio.approval_owner``, as ``routes_paypal.owns_item``): the account whose
    token drafted the brief, the account a WhatsApp brief was sent for, and the studio's
    own work (web-UI briefs, the demo seed), which every signed-in account sees.
    ``approval_owner`` reads the database: **call under ``conn.lock``**."""
    shas: set[str] = set()
    ids: set[str] = set()
    for _fid, sha, *pids in _owned_rows(conn, account_id):
        if sha:
            shas.add(sha)
        ids.update(i for i in pids if i)
    return AccountRefs(frozenset(shas), frozenset(ids))


# -- registry ----------------------------------------------------------------------------


def _entry(name: str) -> dict[str, Any]:
    for t in _REGISTRY:
        if t["method"] == name:
            return t
    raise ToolRefused("not_allowed", f"{name!r} is not a PayPal tool")  # pragma: no cover


def tool_specs() -> list[dict[str, Any]]:
    """Name, description and JSON schema of each read tool, from the vendored registry."""
    out = []
    for name in sorted(READ_TOOLS):
        t = _entry(name)
        out.append({"name": name, "description": t["description"],
                    "input_schema": t["args_schema"].model_json_schema()})
    return out


# -- answers -----------------------------------------------------------------------------


def redact(obj: Any) -> Any:
    """``obj`` without payer fields (``DROP_KEYS``, at any depth); e-mail addresses inside
    strings become ``[redacted]``."""
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items() if k not in DROP_KEYS}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and _EMAIL.search(obj):
        return "[redacted]"
    return obj


def _own_transaction(info: dict[str, Any], refs: AccountRefs) -> bool:
    # PayPal ids only: a content hash (custom_field) is not unique per account, two
    # accounts can fund identical ad content, so it is never an access key.
    return (info.get("transaction_id") in refs.paypal_ids
            or info.get("paypal_reference_id") in refs.paypal_ids)


def _disputed_ids(dispute: Any) -> list[str]:
    items = dispute.get("disputed_transactions") if isinstance(dispute, dict) else None
    return [t["seller_transaction_id"] for t in items or []
            if isinstance(t, dict) and isinstance(t.get("seller_transaction_id"), str)]


def only_own(name: str, answer: dict[str, Any], refs: AccountRefs) -> dict[str, Any]:
    """Reduce a tool's answer to the account's own money; ``ToolRefused("not_allowed")``
    for a single order or dispute that is not the account's."""
    if name == "list_transactions":
        kept = [t for t in answer.get("transaction_details") or []
                if isinstance(t, dict) and isinstance(t.get("transaction_info"), dict)
                and _own_transaction(t["transaction_info"], refs)]
        out = {**answer, "transaction_details": kept}
        if "total_items" in out:
            out["total_items"] = len(kept)
        pages, page = answer.get("total_pages"), answer.get("page")
        for k in ("total_pages", "links"):  # describe PayPal's whole list, not the account's
            out.pop(k, None)
        if isinstance(pages, int) and isinstance(page, int):
            # internal (reconcile): the public wrapper strips it, it would let a caller
            # binary-search the shared merchant's transaction count
            out["has_more"] = page < pages
        return out
    if name == "get_order_details":
        raw = answer.get("raw")
        if not isinstance(raw, dict) or raw.get("id") not in refs.paypal_ids:
            raise ToolRefused("not_allowed", "not an order of this account")
        return answer
    if name == "get_dispute":
        if not any(i in refs.paypal_ids for i in _disputed_ids(answer)):
            raise ToolRefused("not_allowed", "not a dispute about this account's money")
        return answer
    if name == "list_disputes":
        kept = [d for d in answer.get("items") or []
                if any(i in refs.paypal_ids for i in _disputed_ids(d))]
        return {**answer, "items": kept}
    raise ToolRefused("not_allowed", f"{name!r} is not a read tool")


# -- the adapter -------------------------------------------------------------------------


class _Adapter:
    """The ``client`` the vendored handlers expect, over the receipted ``PayPalClient``."""

    def __init__(self, client: PayPalClient, ledger: Ledger, conn: Any, chain_id: str,
                 tool: str) -> None:
        self.client, self.ledger, self.conn = client, ledger, conn
        self.chain_id, self.tool = chain_id, tool
        self.error: ToolRefused | None = None
        self.calls = 0

    def get(self, uri: str, payload: dict[str, Any] | None = None) -> Any:
        """PayPal's answer as a dict, which is what the handlers re-serialise or read with
        ``.get``. The one exception is upstream's transaction-by-id search, the only caller
        that passes ``payload``: it ``json.loads`` the answer, so it gets the JSON text."""
        if self.error is not None:  # a call failed: no further request
            raise self.error
        self.calls += 1
        if self.calls > MAX_CALLS_PER_TOOL:
            self.error = ToolRefused("invalid_arguments", "too many PayPal calls for one tool")
            raise self.error
        path = uri.split("?", 1)[0]
        target = uri
        query = {k: v for k, v in (payload or {}).items() if v is not None}
        if query:
            target = path + "?" + urlencode(query)
        try:
            reply = self.client.get(target)
        except PayPalError as exc:
            self._receipt(path, target, status=exc.status, ok=False, body="")
            self.error = self.error or ToolRefused("paypal_error", str(exc))
            raise self.error from None
        self._receipt(path, target, status=reply.status, ok=True, body=reply.body_sha256)
        return json.dumps(reply.json) if payload is not None else reply.json

    def post(self, uri: str, payload: Any = None) -> str:  # pragma: no cover - never reached
        raise ToolRefused("not_allowed", "the studio's toolkit client only reads")

    def _receipt(self, path: str, target: str, *, status: int, ok: bool, body: str) -> None:
        self.ledger.append(
            self.chain_id, agent=AGENT, kind=PAYPAL_KIND, provider=PROVIDER,
            conn=self.conn if self.ledger.joins(self.conn) else None,
            output_sha256=body, input_sha256=[sha256_hex(target)],
            details={"step": STEP, "tool": self.tool, "path": path, "status": status,
                     "ok": ok, "body_sha256": body})


def run_read_tool(*, client: PayPalClient, ledger: Ledger, conn: Any, chain_id: str,
                  account_refs: AccountRefs, name: str, params: dict[str, Any]
                  ) -> dict[str, Any]:
    """Run one read tool for an agent. ``ToolRefused``: ``not_allowed`` (not a read
    tool, or another account's order/dispute), ``invalid_arguments``, ``paypal_error``.
    ``list_transactions`` is pinned to page 1 at ``PAGE_SIZE`` and the answer has no
    paging fields (``PAGING_KEYS``): pagination cannot be probed for the shared
    merchant's totals."""
    if name == "list_transactions" and isinstance(params, dict):
        params = {**params, "page": 1, "page_size": PAGE_SIZE}
    out = _run(client=client, ledger=ledger, conn=conn, chain_id=chain_id,
               account_refs=account_refs, name=name, params=params)
    return {k: v for k, v in out.items() if k not in PAGING_KEYS}


def run_list_page(*, client: PayPalClient, ledger: Ledger, conn: Any, chain_id: str,
                  account_refs: AccountRefs, params: dict[str, Any]
                  ) -> tuple[dict[str, Any], bool]:
    """Internal (reconcile, never an agent): one page of ``list_transactions`` as asked,
    and whether PayPal has more pages. The caller's own code, not an agent's, picks
    ``page``."""
    out = _run(client=client, ledger=ledger, conn=conn, chain_id=chain_id,
               account_refs=account_refs, name="list_transactions", params=params)
    more = bool(out.get("has_more"))
    return {k: v for k, v in out.items() if k not in PAGING_KEYS}, more


def _run(*, client: PayPalClient, ledger: Ledger, conn: Any, chain_id: str,
         account_refs: AccountRefs, name: str, params: dict[str, Any]) -> dict[str, Any]:
    if name not in READ_TOOLS:
        raise ToolRefused("not_allowed", f"{name!r} is not a read tool")
    Ledger.validate_ids(chain_id, AGENT, provider=PROVIDER)
    entry = _entry(name)
    if not isinstance(params, dict):
        raise ToolRefused("invalid_arguments", "arguments must be an object")
    try:
        checked = entry["args_schema"](**params)
    except (ValidationError, TypeError, ValueError):
        raise ToolRefused("invalid_arguments", f"invalid arguments for {name}") from None
    months = getattr(checked, "search_months", None)
    if name == "list_transactions" and months is not None \
            and not 1 <= months <= MAX_SEARCH_MONTHS:
        raise ToolRefused("invalid_arguments", f"search_months must be 1-{MAX_SEARCH_MONTHS}")
    if name == "get_order_details" and checked.order_id not in account_refs.paypal_ids:
        raise ToolRefused("not_allowed", "not an order of this account")
    adapter = _Adapter(client, ledger, conn, chain_id, name)
    try:
        raw = entry["execute"](adapter, params)
    except ToolRefused:
        raise
    except (ValidationError, TypeError, ValueError):
        raise ToolRefused("invalid_arguments", f"invalid arguments for {name}") from None
    if adapter.error is not None:  # the handler swallowed it (month-by-month search)
        raise adapter.error
    answer = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(answer, dict):
        raise ToolRefused("paypal_error", "unexpected answer")
    out: dict[str, Any] = redact(only_own(name, answer, account_refs))
    return out
