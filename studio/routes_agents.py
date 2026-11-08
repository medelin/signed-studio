"""For agents: the A2A agent card (``/.well-known/agent-card.json``) and ``/agentic``.

The card is public, static apart from the studio's own address, and honest: it lists only the
tools an assistant can actually call (``studio.assistants.OPS``), with the payment policy in
plain fields (``extensions.payments``). It holds no secret and no account data. The A2A
fields follow the zahadun mesh's cards (protocolVersion "1.0", ``securitySchemes``,
``capabilities`` without streaming or push).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from studio.receipts import Ledger
from studio.settings import Settings

CARD_PATH = "/.well-known/agent-card.json"
PROTOCOL_VERSION = "1.0"  # as the zahadun mesh's cards (zahadun_a2a.card.PROTOCOL_VERSION)
STUDIO_VERSION = "0.1.0"
MAX_AGE_S = 300

_MODES = ["text/plain", "application/json"]


def _skills() -> list[dict[str, Any]]:
    """Only tools that work today, named as in MCP and ``/api/v1``."""
    return [
        {"id": "draft_brief", "name": "Draft a brief",
         "description": "Draft a Brief v1 from the owner's words (EN or PL). Numbers and "
                        "claims nobody wrote become open [FACT] questions for the owner. "
                        "Needs a token with the draft scope.",
         "tags": ["brief", "draft", "paypal", "signed-approval"],
         "examples": ["Draft a Muse brief for a pottery workshop in Krakow, 2 h, 180 PLN, "
                      "Saturdays"],
         "inputModes": ["text/plain"], "outputModes": ["application/json"]},
        {"id": "confirm_brief", "name": "Confirm a brief",
         "description": "Confirm a drafted brief with one resolution per open [FACT] "
                        "question (the owner's fact, or strike the claim), checked like the "
                        "web UI. Nothing runs or is paid yet.",
         "tags": ["brief", "draft", "signed-approval"],
         "examples": ["Confirm brief b_123: answer the first question with 'clay and firing "
                      "included', strike the second claim"],
         "inputModes": ["application/json"], "outputModes": ["application/json"]},
        {"id": "run_brief", "name": "Run a brief and pay (or hand over a link)",
         "description": "Run a confirmed brief and wait for how its campaign is paid. "
                        "Outcome mandate_charge: a mandate the owner signed covers it, so "
                        "the studio held the money on the owner's saved PayPal and created "
                        "the campaign PAUSED. Outcome pay_and_sign: outside the mandate, "
                        "so the tool returns a pay-and-sign link for the human. It cannot "
                        "sign, activate or capture.",
         "tags": ["paypal", "mandate", "signed-approval", "agentic-commerce"],
         "examples": ["Run brief b_123 and tell me how it gets paid",
                      "Run the confirmed brief; if it is outside my mandate, give me the "
                      "link"],
         "inputModes": ["application/json"], "outputModes": ["application/json"]},
        {"id": "paypal_transactions", "name": "Check PayPal transactions",
         "description": "Read-only: the owner's own PayPal authorizations and captures "
                        "checked against PayPal's transaction list (matched, pending, held, "
                        "missing, amount_mismatch, unknown). Own money only, no payer data.",
         "tags": ["paypal", "read", "reconciliation"],
         "examples": ["Do my PayPal holds and captures match PayPal's own list?"],
         "inputModes": ["text/plain"], "outputModes": ["application/json"]},
        {"id": "propose_refund", "name": "Propose a refund",
         "description": "Propose a refund of part of a captured funding. It becomes a "
                        "pending approval; nothing is refunded until the owner signs it "
                        "in the browser. Returns the sign link.",
         "tags": ["paypal", "refund", "signed-approval"],
         "examples": ["Propose a refund of 12.50 on funding fund_0123456789abcdef"],
         "inputModes": ["application/json"], "outputModes": ["application/json"]},
    ]


def build_card(base: str) -> dict[str, Any]:
    base = base.rstrip("/")
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "Signed Studio",
        "description": "A studio that drafts, runs and pays for ad campaigns on behalf of a "
                       "small-business owner. An assistant pays only inside a mandate the "
                       "owner signed; otherwise it hands the owner a pay-and-sign link. "
                       "PayPal sandbox.",
        "url": base + "/mcp",
        "version": STUDIO_VERSION,
        "provider": {"organization": "Signed Studio", "url": base + "/"},
        "documentationUrl": base + "/agentic",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "securitySchemes": {"bearer": {
            "type": "http", "scheme": "bearer",
            "description": "A personal access token the signed-in owner makes on /account. "
                           "Scopes: read, draft. There is no sign scope."}},
        "security": [{"bearer": []}],
        "defaultInputModes": _MODES,
        "defaultOutputModes": ["application/json"],
        "skills": _skills(),
        "extensions": {
            "payments": {
                "pays": "only inside a mandate the owner signed",
                "limits": ["objectives", "countries", "landing_hosts", "max_daily_budget",
                           "currency", "max_campaigns", "valid_until", "token_ids",
                           "max_charge_minor", "funding_cap_minor", "period_days", "kinds"],
                "otherwise": "a pay-and-sign link for the human",
                "never": ["sign", "approve", "activate a campaign", "capture",
                          "change a budget", "create or revoke a mandate"],
                "proof": "hash-chained signed receipts, /receipts (offline check: "
                         "studio verify)",
                "proof_url": base + "/receipts",
                "environment": "PayPal sandbox",
            },
            "interfaces": {"mcp": base + "/mcp", "openapi": base + "/api/v1/openapi.json"},
        },
    }


def card_bytes(base: str) -> tuple[bytes, str]:
    body = json.dumps(build_card(base), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()
    return body, '"' + hashlib.sha256(body).hexdigest() + '"'


def register(app: FastAPI, settings: Settings, ledger: Ledger, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None]) -> None:
    def origin(request: Request) -> str:
        return str(settings.public_url or request.base_url).rstrip("/")

    @app.get(CARD_PATH)
    def agent_card(request: Request) -> Response:
        body, etag = card_bytes(origin(request))
        headers = {"ETag": etag, "Cache-Control": f"public, max-age={MAX_AGE_S}"}
        if etag in [p.strip() for p in request.headers.get("if-none-match", "").split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type="application/json", headers=headers)

    def page(request: Request, lang: str) -> Response:
        return templates.TemplateResponse(request, "agentic.html", {
            "seq": ledger.seq(), "epoch": ledger.epoch,
            "logged_in": session_of(request) is not None,
            "login_enabled": settings.judge_login_enabled, "read_only": settings.read_only,
            "page_lang": lang, "base": origin(request),
            "other_href": "/agentic?lang=en" if lang == "pl" else "/pl/agentic",
            "other_lang": "en" if lang == "pl" else "pl"})

    @app.get("/agentic", response_class=HTMLResponse)
    def agentic_en(request: Request) -> Response:
        return page(request, request.state.lang)

    @app.get("/pl/agentic", response_class=HTMLResponse)
    def agentic_pl(request: Request) -> Response:
        return page(request, "pl")
