"""``/api/v1`` (HTTPS API for the owner's own assistant), ``/mcp`` (remote MCP) and the
"Assistants & API" forms of ``/account`` that make and revoke their tokens.

- ``BearerGate`` sits in front of both: ``Authorization: Bearer <token>`` or 401 (with
  ``WWW-Authenticate: Bearer``), then the per-token request limit (429). Session cookies
  are never read here, and the app's login gate (``auth.Gate``) and the sandbox's
  signed-in-writes rule (``sandbox.SandboxGuard``) leave these paths to it
  (``auth.is_bearer``). CSRF does not apply: a browser never attaches a bearer token by
  itself, and no CORS is granted. ``/api/v1/openapi.json`` is public.
- The routes are thin: each maps its path and body to one ``studio.assistants`` operation
  (``Assistants.call``: scope, schema, rate limit, receipt).
- ``/account/tokens/create`` and ``/account/tokens/revoke`` are ordinary account forms
  (same-origin, CSRF field, live session, writable, via ``routes_account``); a token is
  shown once on the page that created it and can only be revoked by its own account.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Query, Request, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.routing import Route

from studio import api_tokens, assistants, remote_mcp
from studio import brief as brief_v1
from studio.agents import community

PREFIX = "/api/v1"
NO_STORE = {"Cache-Control": "no-store"}
UPLOAD_LIMITS = {PREFIX + "/briefs/voice": 32 * 1024 * 1024}
DESCRIPTION = (
    "The owner's own AI assistant (or a script) drives Muse Studio Lite with a personal "
    "access token made on /account (Assistants & API): `Authorization: Bearer <token>`. "
    "Scopes: `read`, and `draft` (draft and confirm briefs, start runs, draft replies). "
    "**No endpoint signs, approves, launches, spends or changes a budget or status**: "
    "a run ends at a pending approval and a reply draft is one; each comes back with a "
    "`sign_url` the owner opens to sign in the browser. Every call is a receipt "
    "(agent `api:<label>`). The same operations are MCP tools at `/mcp`.")


class Gate:
    """ASGI: the bearer token of every request (except the OpenAPI document)."""

    def __init__(self, inner: Any, svc: assistants.Assistants, transport: str,
                 open_paths: tuple[str, ...] = ()) -> None:
        self.inner = inner
        self.svc = svc
        self.transport = transport
        self.open_paths = open_paths

    def base_url(self, headers: Headers, scheme: str) -> str:
        settings = self.svc.settings
        if settings.public_url:
            return str(settings.public_url)
        secure = settings.cookie_secure or scheme == "https"
        return ("https" if secure else "http") + "://" + headers.get("host", "localhost")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path", "") in self.open_paths:
            await self.inner(scope, receive, send)
            return
        headers = Headers(scope=scope)
        token, status, body = await run_in_threadpool(self.svc.authenticate, headers)
        if token is None:
            extra = ({"Retry-After": str(body["retry_after"])} if status == 429 else
                     {"WWW-Authenticate": 'Bearer realm="muse-studio-lite"'})
            await JSONResponse(body, status, headers={**NO_STORE, **extra})(scope, receive,
                                                                            send)
            return
        principal = assistants.Principal(token, self.transport,
                                         self.base_url(headers, scope.get("scheme", "http")))
        scope.setdefault("state", {})["principal"] = principal
        await self.inner(scope, receive, send)


# -- request bodies (for the OpenAPI document; the operations check again) ----------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftIn(_Body):
    text: str = Field(min_length=1, max_length=brief_v1.MAX_SOURCE,
                      description="What the owner wants to advertise, in their words.")
    lang: Literal["en", "pl"] = "en"


class ResolutionIn(_Body):
    action: Literal["answer", "strike"]
    text: str = Field("", max_length=brief_v1.MAX_FACT,
                      description="The owner's fact (for `answer`).")


class ConfirmIn(_Body):
    resolutions: list[ResolutionIn] = Field(
        max_length=brief_v1.MAX_QUESTIONS,
        description="One per open [FACT] question, in order.")
    brief: dict[str, Any] | None = Field(None, description="The edited Brief v1 "
                                                           "(default: the draft).")


class RunIn(_Body):
    brief_id: str = Field(pattern=r"^b_[0-9a-f]{16}$")
    open_weights: bool = False


class ReplyIn(_Body):
    message: str = Field("", max_length=community.MAX_REPLY,
                         description="The exact reply; empty: the community agent drafts it.")


def build_api(svc: assistants.Assistants) -> FastAPI:
    public = svc.settings.public_url  # an absolute server URL for importers (GPT Actions)
    api = FastAPI(title="Muse Studio Lite API for assistants", version="1",
                  description=DESCRIPTION, openapi_url="/openapi.json", docs_url=None,
                  redoc_url=None, dependencies=[Security(HTTPBearer(auto_error=False))],
                  servers=[{"url": public.rstrip("/") + PREFIX}] if public else None,
                  root_path_in_servers=not public)

    async def do(request: Request, name: str, args: dict[str, Any], ok: int = 200,
                 blob: bytes | None = None) -> Response:
        principal = request.scope["state"]["principal"]
        status, result = await run_in_threadpool(svc.call, principal, name, args, blob)
        headers = dict(NO_STORE)
        if status == 429 and "retry_after" in result:
            headers["Retry-After"] = str(result["retry_after"])
        return JSONResponse(result, ok if status == 200 else status, headers=headers)

    @api.get("/me", summary="This token: account, scopes, expiry, what it may do")
    async def me(request: Request) -> Response:
        return await do(request, "whoami", {})

    @api.get("/briefs", summary="List briefs")
    async def list_briefs(request: Request, limit: int = Query(20, ge=1, le=100)) -> Response:
        return await do(request, "list_briefs", {"limit": limit})

    @api.post("/briefs", status_code=201, summary="Draft a Brief v1 from text (draft scope)")
    async def draft(request: Request, body: DraftIn) -> Response:
        return await do(request, "draft_brief", body.model_dump(), 201)

    @api.post("/briefs/voice", status_code=201,
              summary="Draft a Brief v1 from an audio recording (draft scope)",
              description="Raw audio body (audio/webm, ogg, wav, mp4, mpeg); Muse Voice "
                          "Transcribe, then the same draft as POST /briefs.")
    async def draft_voice(request: Request, lang: Literal["en", "pl"] = "en") -> Response:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        return await do(request, "draft_brief_audio", {"content_type": ctype[:40],
                                                       "lang": lang}, 201,
                        await request.body())

    @api.get("/briefs/{brief_id}", summary="Read one brief with its open [FACT] questions")
    async def read_brief(request: Request, brief_id: str) -> Response:
        return await do(request, "read_brief", {"brief_id": brief_id})

    @api.post("/briefs/{brief_id}/confirm",
              summary="Confirm a drafted brief: resolve every [FACT] question (draft scope)")
    async def confirm(request: Request, brief_id: str, body: ConfirmIn) -> Response:
        args: dict[str, Any] = {"brief_id": brief_id,
                                "resolutions": [r.model_dump() for r in body.resolutions]}
        if body.brief is not None:
            args["brief"] = body.brief
        return await do(request, "confirm_brief", args)

    @api.post("/runs", status_code=202,
              summary="Start the run of a confirmed brief (draft scope); ends awaiting a "
                      "human signature")
    async def start(request: Request, body: RunIn) -> Response:
        return await do(request, "start_run", body.model_dump(), 202)

    @api.get("/runs/{run_id}", summary="Run status: plan, copy, images, reviews, sign_url")
    async def run(request: Request, run_id: str) -> Response:
        return await do(request, "run_status", {"run_id": run_id})

    @api.get("/runs/{run_id}/assets/{name}.png", summary="One rendered image of a run",
             response_class=FileResponse)
    async def asset(request: Request, run_id: str, name: str) -> Response:
        principal = request.scope["state"]["principal"]
        status, result = await run_in_threadpool(svc.call, principal, "run_asset",
                                                 {"run_id": run_id, "name": name})
        if status != 200:
            return JSONResponse(result, status, headers=NO_STORE)
        return FileResponse(result["_path"], media_type="image/png",
                            headers={"Cache-Control": "private, max-age=3600"})

    @api.get("/approvals", summary="Everything waiting for the owner's signature")
    async def approvals(request: Request) -> Response:
        return await do(request, "pending_approvals", {})

    @api.get("/campaigns", summary="Runs and campaigns")
    async def campaigns(request: Request, limit: int = Query(20, ge=1, le=100)) -> Response:
        return await do(request, "campaigns", {"limit": limit})

    @api.get("/insights", summary="Latest insights, recommendations and report")
    async def insights(request: Request) -> Response:
        return await do(request, "insights", {})

    @api.get("/leads", summary="Leads")
    async def leads(request: Request, limit: int = Query(20, ge=1, le=100)) -> Response:
        return await do(request, "leads", {"limit": limit})

    @api.get("/comments", summary="Comments with triage and reply drafts")
    async def comments(request: Request, limit: int = Query(20, ge=1, le=100)) -> Response:
        return await do(request, "comments", {"limit": limit})

    @api.post("/comments/{comment_id}/reply", status_code=201,
              summary="Draft a reply: a pending approval the owner signs (draft scope)")
    async def reply(request: Request, comment_id: str, body: ReplyIn) -> Response:
        args: dict[str, Any] = {"comment_id": comment_id}
        if body.message:
            args["message"] = body.message
        return await do(request, "draft_reply", args, 201)

    return api


def install(app: FastAPI) -> assistants.Assistants:
    """Mount ``/api/v1`` and ``/mcp`` on the studio app and add the token forms."""
    svc = assistants.Assistants(app)
    app.state.assistants = svc
    api = build_api(svc)
    app.mount(PREFIX, Gate(api, svc, "http", open_paths=(PREFIX + "/openapi.json",)))
    mcp_app = Gate(remote_mcp.RemoteMcp(svc), svc, "mcp")
    app.router.routes.append(Route("/mcp", endpoint=mcp_app,
                                   methods=["GET", "POST", "DELETE"]))
    _account_forms(app, svc)
    return svc


def _account_forms(app: FastAPI, svc: assistants.Assistants) -> None:
    def tokens_of(account: Any) -> dict[str, Any]:
        return {"api_tokens": [t.view() for t in svc.tokens.listing(account.id)],
                "token_days": api_tokens.DAY_CHOICES,
                "token_default_days": api_tokens.DEFAULT_DAYS}

    app.state.account_extras = tokens_of

    @app.post("/account/tokens/create")
    async def create_token(request: Request) -> Response:
        got = await app.state.account_posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        try:
            days = int(fields.get("days", api_tokens.DEFAULT_DAYS))
        except ValueError:
            days = 0
        try:
            token, value = svc.tokens.create(account.id, fields.get("label", ""),
                                             fields.get("scope", "read"), days)
        except api_tokens.TokenError as exc:
            return app.state.account_page(request, sid, account, status=400,
                                          errors={"assistants": ("err." + exc.code, "")})
        response: Response = app.state.account_page(
            request, sid, account, extra={"new_token": value, "new_token_id": token.id,
                                          "done": "token_created"})
        return response

    @app.post("/account/tokens/revoke")
    async def revoke_token(request: Request) -> Response:
        got = await app.state.account_posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        if not svc.tokens.revoke(account.id, fields.get("token", "")):
            return app.state.account_page(request, sid, account, status=404,
                                          errors={"assistants": ("err.unknown_token", "")})
        return RedirectResponse("/account?done=token_revoked#assistants", 303,
                                headers=NO_STORE)

