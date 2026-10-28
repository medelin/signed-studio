"""``studio mcp``: Muse Studio Lite as an MCP server, so an external agent (Claude Code, an
IDE, another agent) can draft briefs, run the planning pipeline and read the results,
while a human still signs every send.

- **Transport**: stdio only. The server is a local process of the operator, started by the
  MCP client; it opens no network listener. It works on the studio's own database and
  ledger key (``STUDIO_DATA_DIR``) and is bound to one existing, active account
  (``--account``), checked again on every call.
- **Tools** (``TOOLS``) only read or draft: briefs, runs and campaigns, receipts, the
  report and measurement, ``studio verify``; a Brief v1 draft and its confirmation go
  through the same functions as the UI (``routes_studio.check_confirmation``,
  ``store_draft``, ``commit_confirmation``), and the run is the same producer.
- **Replay and mock only**: model calls are served by the recorded replay set
  (``--replay-set``, no key, no network, no spend); Meta and Google Ads are the mock
  adapters behind ``_ReadOnlyMeta`` (account currency, Page id, the targeting search) and
  ``_ReadOnlyGoogle`` (account currency, customer id), neither with a write method. Text
  from an MCP client is untrusted: drafts and runs are enveloped like a voice transcript
  and the copywriter never runs on the Contributor tier.
- **No signature, no send**: no tool reaches ``approvals.verify_and_consume``, an
  executor, the media buyer or an account operation. A run that ends at an approval, and
  ``request_launch``, return ``awaiting_human_signature`` with the URL of the approval page
  in the web UI (``--ui-url``).
- **A receipt per call**: every call (refused, failed and unknown ones too) is a
  ``tool_call`` receipt in this server's own run (``mcp_<hex>``), ``agent`` =
  ``mcp:<client name>`` (from the client's ``initialize``), with the hashes of the
  arguments and of the result, the account id and ``side_effects`` (``none`` for reads,
  ``local`` for drafts and runs). It never joins a campaign run, so it cannot move the
  chain head an approval is bound to. Under ``STUDIO_READ_ONLY=1`` every call is refused
  before anything, receipt included, is written.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import jsonschema

from studio import brief as brief_v1
from studio import routes_report, routes_studio
from studio.accounts import Account, Accounts
from studio.agents import media_buyer, producer
from studio.approvals import Keys
from studio.google import adapter as google_adapter
from studio.meta import adapter as meta_adapter
from studio.meta import measurement
from studio.muse.client import MuseClient, MuseError
from studio.muse.openweights import OpenWeightsClient
from studio.receipts import KINDS, Ledger, canonical, is_opaque, sha256_hex, verify
from studio.routes_inbox import public_view
from studio.settings import ReadOnlyMode, Settings
from studio.ui import i18n

LOG = logging.getLogger("studio.mcp")
SERVER_NAME = "muse-studio-lite"
ACTOR_PREFIX = "mcp:"
AWAITING = "awaiting_human_signature"
DEFAULT_REPLAY_SET = "brief"  # a recorded set with a Brief v1 draft in it
DEFAULT_UI_URL = "http://localhost:8000"
MAX_LIST = 100
MAX_RECEIPTS = 500
MAX_EXPORT = 64 * 1024 * 1024
_CLIENT = re.compile(r"[^A-Za-z0-9_.-]+")
_CAMPAIGN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


class McpRefused(Exception):
    """A call the server answers with ``{"error": <code>, ...}`` (and a receipt)."""

    def __init__(self, error: str, **extra: Any) -> None:
        super().__init__(error)
        self.body = {"error": error, **extra}


def actor(client_name: str) -> str:
    """The receipt ``agent`` of a client: ``mcp:<name>`` with the name folded to an opaque
    id (``mcp:client`` when nothing usable is left)."""
    name = _CLIENT.sub("-", client_name or "").strip("-.")[:60]
    agent = ACTOR_PREFIX + (name or "client")
    return agent if is_opaque(agent) else ACTOR_PREFIX + "client"


class _ReadOnlyMeta:
    """The only Meta surface the MCP server holds: what a run needs before an approval
    (the ad account's currency, the Page id, the read-only targeting search). It has no
    upload, create, activate or update method, so nothing reached from a tool can write."""

    def __init__(self, inner: Any) -> None:
        self.name = inner.name
        self.page_id = getattr(inner, "page_id", "")
        self.search_targeting = getattr(inner, "search_targeting", None)
        self._account_info: Callable[[], dict[str, str]] = inner.account_info

    def account_info(self) -> dict[str, str]:
        return self._account_info()


class _ReadOnlyGoogle:
    """The Google Ads side, the same way: the account's currency and the customer id the
    producer needs to build a ``google.create`` approval; no create or activate method."""

    def __init__(self, inner: Any) -> None:
        self.name = inner.name
        self.customer_id = getattr(inner, "customer_id", "")
        self._account_info: Callable[[], dict[str, str]] = inner.account_info

    def account_info(self) -> dict[str, str]:
        return self._account_info()


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    side_effects: str  # "none" (reads) or "local" (writes the studio database only)
    schema: dict[str, Any]
    fn: Callable[..., dict[str, Any]]  # (studio, agent, **arguments)


def _schema(props: dict[str, Any] | None = None, required: tuple[str, ...] = ()
            ) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": list(required),
            "additionalProperties": False}


_LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_LIST}
_ID = {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,80}$"}


class StudioMcp:
    """The tools, bound to one studio (database, ledger) and one account. ``call`` is the
    only entry: it checks, runs the tool and writes the receipt."""

    def __init__(self, settings: Settings, conn: Any, ledger: Ledger, account: Account, *,
                 ui_url: str = DEFAULT_UI_URL, replay_set: str = DEFAULT_REPLAY_SET,
                 clients: tuple[MuseClient, OpenWeightsClient] | None = None,
                 adapter: Any = None, google: Any = None) -> None:
        self.settings = replace(settings, replay=True, replay_set=replay_set,
                                meta_adapter="mock", google_adapter="mock",
                                model_api_key="", baseten_api_key="")
        self.conn = conn
        self.ledger = ledger
        self.accounts = Accounts(conn)
        self.account_id = account.id
        self.lang = i18n.norm(account.lang)
        parts = urlsplit(ui_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("--ui-url must be an http(s) URL of the studio's web UI")
        self.ui_url = ui_url.rstrip("/")
        self.run_id = producer.opaque_id("mcp_")  # this server's own receipt chain
        self._clients = clients
        self.meta = _ReadOnlyMeta(adapter or meta_adapter.make(self.settings, conn=conn))
        self.google = _ReadOnlyGoogle(google or google_adapter.make(self.settings, conn=conn))
        self._run_lock = threading.Lock()
        self._lock = threading.Lock()

    # -- the one entry ----------------------------------------------------------------

    def call(self, client_name: str, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        """Run tool ``name`` for the client and leave its receipt; the result always has
        ``receipt_id``, and ``error`` when it was refused or failed."""
        self.settings.require_writable()  # the kill switch: not even a receipt
        args = arguments if isinstance(arguments, dict) else {}
        tool = TOOLS.get(name)
        status = "ok"
        try:
            if tool is None:
                raise McpRefused("unknown_tool")
            try:
                jsonschema.validate(args, tool.schema)
            except jsonschema.ValidationError as exc:
                raise McpRefused("invalid_arguments", detail=exc.message[:200]) from None
            account = self.accounts.get(self.account_id)
            if account is None or account.status != "active":
                raise McpRefused("account_inactive")
            result = tool.fn(self, actor(client_name), **args)
        except McpRefused as exc:
            status, result = "refused", exc.body
        except routes_studio.Refused as exc:
            status, result = "refused", {**exc.body, "http_status": exc.status}
        except Exception as exc:  # noqa: BLE001 - the agent gets a code, the call a receipt
            LOG.warning("mcp tool %s failed: %s", name, type(exc).__name__)
            status, result = "error", {"error": "internal_error", "type": type(exc).__name__}
        if status == "ok" and result.get("status") == AWAITING:
            status = AWAITING
        code = result.get("error")
        if isinstance(code, str) and f"err.{code}" in i18n.EN and "message" not in result:
            result = {**result, "message": str(i18n.text(self.lang, f"err.{code}"))}
        details: dict[str, Any] = {
            "tool": "mcp." + name if tool is not None else "mcp.unknown",
            "status": status, "account": self.account_id,
            "side_effects": tool.side_effects if tool is not None and status != "refused"
            else "none"}
        if tool is None:
            details["tool_sha256"] = sha256_hex(str(name))
        for key in ("brief_id", "run_id", "approval_id", "google_approval_id", "item_id"):
            value = result.get(key) or args.get(key)
            if isinstance(value, str) and is_opaque(value):
                details[key] = value
        with self._lock:
            receipt = self.ledger.append(
                self.run_id, agent=actor(client_name), kind="tool_call",
                input_sha256=[sha256_hex(canonical(args))],
                output_sha256=sha256_hex(canonical(result)), details=details)
        return {**result, "receipt_id": receipt.id}

    # -- helpers ----------------------------------------------------------------------

    def clients(self) -> tuple[MuseClient, OpenWeightsClient]:
        """Replay clients on a fresh transport for every draft and run, so each one is
        served the recorded set from its start (a shared one drifts from run to run)."""
        if self._clients is not None:
            return self._clients
        return routes_studio.replay_clients(self.settings, self.ledger)

    def currency(self) -> str:
        return self.meta.account_info()["currency"]

    def google_currency(self) -> str:
        return self.google.account_info()["currency"]

    def page_url(self, run_id: str) -> str:
        query = {"run": run_id, **({"lang": self.lang} if self.lang != "en" else {})}
        return f"{self.ui_url}/studio?{urlencode(query)}"

    def signature(self, run_id: str) -> dict[str, Any]:
        """What stands between this run and a send: always a human in the web UI."""
        found = routes_studio.run_state(self.conn, run_id)
        if found is None:
            raise McpRefused("not_found")
        status, state, _ = found
        url = self.page_url(run_id)
        if status == "awaiting_approval" and state.get("pending_id"):
            google = {"google_approval_id": state["google_pending_id"]} \
                if state.get("google_pending_id") else {}
            return {"status": AWAITING, "action": "campaign.create", "run_id": run_id,
                    "approval_id": state["pending_id"], **google, "approval_url": url,
                    "message": i18n.text(self.lang, "mcp.awaiting_signature", url=url)}
        with self.conn.lock:
            row = self.conn.execute("SELECT id, status FROM campaigns WHERE run_id = ? "
                                    "ORDER BY created_ts DESC LIMIT 1", (run_id,)).fetchone()
        if row is not None and row[1] == "PAUSED":
            return {"status": AWAITING, "action": "campaign.activate", "run_id": run_id,
                    "item_id": row[0], "approval_url": url,
                    "message": i18n.text(self.lang, "mcp.awaiting_activation", url=url)}
        return {"status": status, "run_id": run_id, "campaign_status": row[1] if row else "",
                "message": i18n.text(self.lang, "mcp.nothing_to_sign", status=status)}


# -- tools ----------------------------------------------------------------------------


def list_briefs(s: StudioMcp, agent: str, limit: int = 20) -> dict[str, Any]:
    with s.conn.lock:
        rows = s.conn.execute(
            "SELECT id, run_id, source, created_ts, draft_run_id, draft != '', brief_sha256 "
            "FROM briefs ORDER BY created_ts DESC LIMIT ?", (limit,)).fetchall()
    return {"briefs": [{
        "brief_id": r[0], "run_id": r[1], "source": r[2], "created_ts": r[3],
        "draft_run_id": r[4],
        "state": "confirmed" if r[6] else "draft" if r[5] else "text" if r[1] else "unused",
        "brief_sha256": r[6]} for r in rows]}


def read_brief(s: StudioMcp, agent: str, brief_id: str) -> dict[str, Any]:
    with s.conn.lock:
        row = s.conn.execute("SELECT id, run_id, source, text, draft, brief_v1, brief_sha256, "
                             "draft_run_id FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if row is None:
        raise McpRefused("not_found")
    out: dict[str, Any] = {"brief_id": row[0], "run_id": row[1], "source": row[2],
                           "text": row[3], "draft_run_id": row[7], "brief_sha256": row[6]}
    for key, raw in (("draft", row[4]), ("brief_v1", row[5])):
        data = json.loads(raw) if raw else None
        out[key] = {k: v for k, v in data.items() if k != "schema"} if data else None
    if out["draft"] is not None and not row[1]:
        draft = brief_v1.BriefV1.model_validate(out["draft"])
        out["problems"] = brief_v1.problems(draft, confirmed=False,
                                            ceiling_daily=s.settings.max_daily_budget,
                                            currency=s.currency())
    return out


def list_campaigns(s: StudioMcp, agent: str, limit: int = 20) -> dict[str, Any]:
    """Runs (a campaign being made, its status and what waits for a signature) and the
    campaigns created through the Meta adapter."""
    with s.conn.lock:
        runs = s.conn.execute("SELECT run_id, status, updated_ts FROM run_state "
                              "ORDER BY updated_ts DESC LIMIT ?", (limit,)).fetchall()
        rows = s.conn.execute(
            "SELECT id, run_id, name, objective, status, daily_budget_minor, currency, "
            "adapter, created_ts FROM campaigns ORDER BY created_ts DESC LIMIT ?",
            (limit,)).fetchall()
    keys = ("item_id", "run_id", "name", "objective", "status", "daily_budget_minor",
            "currency", "adapter", "created_ts")
    return {"runs": [{"run_id": r[0], "status": r[1], "updated_ts": r[2],
                      **({"approval_url": s.page_url(r[0])}
                         if r[1] == "awaiting_approval" else {})} for r in runs],
            "campaigns": [dict(zip(keys, r, strict=True)) for r in rows]}


def read_run(s: StudioMcp, agent: str, run_id: str) -> dict[str, Any]:
    if not routes_studio.RUN_ID.fullmatch(run_id):
        raise McpRefused("not_found")
    found = routes_studio.run_state(s.conn, run_id)
    if found is None:
        raise McpRefused("not_found")
    status, state, updated = found
    return {"run_id": run_id, "status": status, "updated_ts": updated, "state": state,
            "receipts": [routes_studio.receipt_event(r) for r in s.ledger.receipts(run_id)],
            "signature": s.signature(run_id)}


def read_campaign(s: StudioMcp, agent: str, item_id: str) -> dict[str, Any]:
    c = media_buyer.campaign(s.conn, item_id) if _CAMPAIGN_ID.fullmatch(item_id) else None
    if c is None:
        raise McpRefused("not_found")
    c["item_id"] = c.pop("id")
    created = c.pop("created")
    preview = created.get("preview")
    # a Meta preview URL carries a short-lived access token in its query: never handed out
    inline = isinstance(preview, dict) and preview.get("kind") == "inline"
    sign = s.signature(c["run_id"]) if routes_studio.run_state(s.conn, c["run_id"]) else None
    return {**c, "preview": preview if inline else None,
            "preview_error": created.get("preview_error", ""),
            "ceiling": s.settings.max_daily_budget, "signature": sign}


def draft_brief(s: StudioMcp, agent: str, text: str, lang: str = "en") -> dict[str, Any]:
    """The producer drafts a Brief v1 from the client's text (``POST /api/briefs/draft``):
    gaps and numbers nobody wrote become open ``[FACT]`` questions."""
    text = text.strip()
    if not text:
        raise McpRefused("invalid_brief")
    if len(text) > brief_v1.MAX_SOURCE:
        raise McpRefused("brief_too_long")
    run_id = producer.opaque_id("run_")
    muse, _ = s.clients()
    try:
        draft = producer.draft_brief(muse, text, run_id=run_id, source="transcript",
                                     lang=lang, landing_url=s.settings.landing_url)
    except MuseError as exc:
        raise McpRefused("model_error", code=exc.code) from None
    brief_id = routes_studio.store_draft(s.conn, text, draft, run_id)
    return {"brief_id": brief_id, "draft_run_id": run_id,
            "brief": draft.model_dump(mode="json"),
            "problems": brief_v1.problems(draft, confirmed=False,
                                          ceiling_daily=s.settings.max_daily_budget,
                                          currency=s.currency()),
            "ceiling": s.settings.max_daily_budget, "currency": s.currency(),
            "google_currency": s.google_currency()}


def run_pipeline(s: StudioMcp, agent: str, brief_id: str, resolutions: list[dict[str, Any]],
                 brief: dict[str, Any] | None = None) -> dict[str, Any]:
    """Confirm a drafted Brief v1 (one resolution per open question; ``brief`` replaces
    the stored draft as the UI's edits do) with the UI's checks, then run the producer:
    plan, copy, images, reviews, and a pending approval that only a human can sign."""
    if brief is None:
        with s.conn.lock:
            row = s.conn.execute("SELECT draft FROM briefs WHERE id = ?",
                                 (brief_id,)).fetchone()
        if row is None or not row[0]:
            raise McpRefused("unknown_draft")
        brief = {k: v for k, v in json.loads(row[0]).items() if k != "schema"}
    body = {"brief_id": brief_id, "brief_v1": brief, "resolutions": resolutions}
    c = routes_studio.check_confirmation(s.conn, body, ceiling=s.settings.max_daily_budget,
                                         currency=s.currency,
                                         google_currency=s.google_currency, untrusted=True)
    if not s._run_lock.acquire(blocking=False):
        raise McpRefused("run_in_progress")
    try:
        routes_studio.commit_confirmation(s.conn, s.ledger, c, agent=agent)
        muse, open_weights = s.clients()
        ctx = routes_studio.run_context(s.settings, s.conn, muse, open_weights, s.meta,
                                        currency=s.currency(), brief_source=c.source,
                                        confirmed=c.brief, digest=c.digest,
                                        google=s.google)
        try:
            producer.run(ctx, brief_v1.render(c.brief), run_id=c.run_id)
        except Exception as exc:  # noqa: BLE001 - as the UI's worker: recorded, reported
            LOG.warning("run %s failed: %s", c.run_id, type(exc).__name__)
            producer.mark_failed(s.conn, c.run_id, type(exc).__name__)
    finally:
        s._run_lock.release()
    out = read_run(s, agent, c.run_id)
    sig = out["signature"]
    return {"run_id": c.run_id, "brief_id": brief_id, "brief_sha256": c.digest, **c.counts,
            "run_status": out["status"], "state": out["state"],
            **{k: v for k, v in sig.items() if k != "run_id"}}


def request_launch(s: StudioMcp, agent: str, run_id: str) -> dict[str, Any]:
    """What an agent calls to send or turn on a campaign: it sends nothing and signs
    nothing, it says which human signature is missing and where to give it."""
    if not routes_studio.RUN_ID.fullmatch(run_id):
        raise McpRefused("not_found")
    return s.signature(run_id)


def read_receipts(s: StudioMcp, agent: str, run_id: str = "", kind: str = "",
                  limit: int = MAX_LIST) -> dict[str, Any]:
    rows = [r for r in s.ledger.receipts(run_id or None) if not kind or r.kind == kind]
    return {"seq": s.ledger.seq(), "epoch": s.ledger.epoch, "matched": len(rows),
            "receipts": [asdict(r) for r in rows[-limit:]]}


def read_report(s: StudioMcp, agent: str) -> dict[str, Any]:
    """The latest analyst report (as a visitor sees it: e-mails and phones redacted) and
    the latest measurement with its recommendations (each executes only after a signed
    ``campaign.update`` in the web UI)."""
    rep = routes_report.latest(s.conn)
    return {"report": public_view(rep) if rep is not None else None,
            "measurement": public_view(measurement.latest(s.conn))}


def verify_export(s: StudioMcp, agent: str, path: str = "", min_seq: int = 0) -> dict[str, Any]:
    """``studio verify`` with this studio's ledger key and approver keys: on an exported
    ``.jsonl`` file, or (no ``path``) on a fresh export of the whole ledger."""
    key = s.ledger.public_key
    with s.conn.lock:
        approvers = Keys.registry(s.conn)
    if path:
        file = Path(path).expanduser()
        if file.suffix != ".jsonl" or not file.is_file() or file.stat().st_size > MAX_EXPORT:
            raise McpRefused("path_not_allowed", detail="a .jsonl receipts export, ≤ 64 MB")
        report = verify(file, key, min_seq=min_seq, approver_keys=approvers)
    else:
        with tempfile.TemporaryDirectory(prefix="studio-mcp-") as tmp:
            file = Path(tmp) / "receipts.jsonl"
            file.write_text(s.ledger.export_jsonl())
            report = verify(file, key, min_seq=max(min_seq, s.ledger.seq()),
                            approver_keys=approvers)
    return {"ok": report.ok, "receipts": report.count, "signatures": report.signatures,
            "errors": report.errors[:50], "exit": 0 if report.ok else 2}


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("list_briefs", "Briefs, newest first: id, state (draft/confirmed), run, brief hash.",
         "none", _schema({"limit": _LIMIT}), list_briefs),
    Tool("read_brief", "One brief: the source text, the Brief v1 draft with its field "
         "problems and open [FACT] questions, the confirmed brief and its hash.",
         "none", _schema({"brief_id": _ID}, ("brief_id",)), read_brief),
    Tool("list_campaigns", "Runs (campaigns being made, their status, the approval page "
         "when one waits for a signature) and the campaigns created on Meta.",
         "none", _schema({"limit": _LIMIT}), list_campaigns),
    Tool("read_run", "One run: plan, copy, images, reviews, its receipts, and what waits "
         "for a human signature.", "none", _schema({"run_id": _ID}, ("run_id",)), read_run),
    Tool("read_campaign", "One created campaign (always created PAUSED): Meta ids, budget, "
         "currency, status, and the signature its activation needs.",
         "none", _schema({"item_id": _ID}, ("item_id",)), read_campaign),
    Tool("draft_brief", "Draft a Brief v1 from free text (EN or PL), from recorded answers: "
         "returns the draft, its field problems and the open [FACT] questions to resolve.",
         "local", _schema({"text": {"type": "string", "minLength": 1,
                                    "maxLength": brief_v1.MAX_SOURCE},
                           "lang": {"type": "string", "enum": ["en", "pl"]}}, ("text",)),
         draft_brief),
    Tool("run_pipeline", "Confirm a drafted brief (resolve every open [FACT] question: "
         "answer or strike) with the same checks as the web UI, then run plan, copy, images "
         "and reviews from recorded answers. Ends awaiting a human signature in the web UI: "
         "nothing is created or sent.",
         "local", _schema({
             "brief_id": _ID,
             "resolutions": {"type": "array", "maxItems": brief_v1.MAX_QUESTIONS,
                             "items": {"type": "object", "properties": {
                                 "action": {"type": "string", "enum": ["answer", "strike"]},
                                 "text": {"type": "string",
                                          "maxLength": brief_v1.MAX_FACT}},
                                 "required": ["action"], "additionalProperties": False}},
             "brief": {"type": "object"}}, ("brief_id", "resolutions")), run_pipeline),
    Tool("request_launch", "Ask to create, send or turn on a run's campaign. Never does it: "
         "returns awaiting_human_signature and the approval page URL for the owner.",
         "none", _schema({"run_id": _ID}, ("run_id",)), request_launch),
    Tool("read_receipts", "Receipts as exported (hash chain, ledger signature), newest last; "
         "filter by run and kind.", "none", _schema({
             "run_id": {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{0,80}$"},
             "kind": {"type": "string", "enum": ["", *sorted(KINDS)]},
             "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RECEIPTS}}),
         read_receipts),
    Tool("read_report", "The latest analyst report and the latest insights with their "
         "recommendations.", "none", _schema(), read_report),
    Tool("verify_export", "studio verify with this studio's keys: a .jsonl export at "
         "`path`, or the current ledger when `path` is empty. exit 0 = chain OK.",
         "none", _schema({"path": {"type": "string", "maxLength": 4096},
                          "min_seq": {"type": "integer", "minimum": 0}}), verify_export),
)}


def open_studio(env_file: Path, username: str, **kwargs: Any) -> StudioMcp:
    """The configured studio's existing database and ledger, bound to an active account."""
    from studio import db

    settings = Settings.load(env_file=env_file)
    path = settings.data_dir / "studio.db"
    if not path.is_file():
        raise ValueError(f"no studio database at {path} (start the studio once first)")
    conn = db.connect(path)
    ledger = db.open_ledger(conn, settings.data_dir / "ledger.key")
    account = Accounts(conn).by_username(username)
    if account is None or account.status != "active":
        raise ValueError(f"no active account {username!r} (studio account list)")
    return StudioMcp(settings, conn, ledger, account, **kwargs)


def build_server(studio: StudioMcp) -> Any:
    """The MCP server (low-level SDK): the tool list from ``TOOLS``, every call through
    ``StudioMcp.call`` in a worker thread. Input is validated there, not by the SDK, so a
    malformed call still leaves its receipt."""
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server

    server: Any = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(name=t.name, description=t.description, inputSchema=t.schema)
                for t in TOOLS.values()]

    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        params = server.request_context.session.client_params
        client = params.clientInfo.name if params is not None else ""

        try:
            result = await anyio.to_thread.run_sync(studio.call, client, name, arguments)
        except ReadOnlyMode:
            result = {"error": "read_only"}
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False,
                                                                    indent=1))],
            structuredContent=result, isError="error" in result)

    return server


def serve(studio: StudioMcp) -> int:
    """Serve on stdin/stdout until the client closes them."""
    import anyio
    from mcp.server.stdio import stdio_server

    server = build_server(studio)

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(main)
    return 0
