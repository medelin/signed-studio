"""``/account`` (profile, password, two-step sign-in, sessions, export, deletion) and the
login's second step ``/login/2fa``.

Every form is a plain server-rendered HTML form (no JavaScript needed). Every ``POST`` is
same-origin (``Origin`` here, ``Sec-Fetch-Site`` in the Gate), carries the CSRF token as the
``csrf`` field (checked here: ``auth.FORM_CHECKED_PREFIXES``), needs a live session and is
refused in read-only mode. Forms that check a password or a code also count against the
login limiter (a stolen session cookie must not become a password oracle). Responses are
``Cache-Control: no-store``: recovery codes and the enrolment QR must not be cached.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from studio import accounts, auth, db, notify, prefs, whatsapp
from studio.accounts import Account, AccountError, Accounts, AccountSessions, SecretBox
from studio.receipts import Ledger
from studio.settings import Settings
from studio.ui import i18n

CHALLENGE_COOKIE = "studio_2fa"
DONE = frozenset({"profile", "password", "2fa_started", "2fa_cancelled", "2fa_off",
                  "session_revoked", "sessions_revoked", "notify"})
SECTIONS = ("profile", "password", "two-factor", "sessions", "notifications", "export", "delete")
NO_STORE = {"Cache-Control": "no-store"}
MAX_FIELDS = 20


def install(app: FastAPI, settings: Settings, conn: Any) -> None:
    """Accounts on ``app.state``: the store, the judge demo account seeded from
    ``JUDGE_PASSWORD``, account-bound sessions around ``app.state.sessions`` and the pending
    second steps."""
    box = SecretBox(db.load_or_create_ledger_key(settings.data_dir / "ledger.key"))
    store = Accounts(conn, box)
    store.seed_demo(settings.judge_password)
    store.revoke_all_sessions()  # sessions live in process memory: earlier rows are dead
    app.state.accounts = store
    app.state.sessions = AccountSessions(app.state.sessions, store)
    app.state.challenges = accounts.Challenges()


def login_enabled(app: FastAPI, settings: Settings) -> bool:
    return settings.judge_login_enabled or bool(app.state.accounts.any_login())


def _secure(request: Request, settings: Settings) -> bool:
    return settings.cookie_secure or request.url.scheme == "https"


def _pref_cookies(response: Response, account: Account, secure: bool) -> None:
    """The account's language and display defaults become the viewer's preference cookies
    (``studio.prefs``), as if chosen in the header."""
    for name, value in (("lang", account.lang), ("theme", account.theme),
                        ("palette", account.palette)):
        if value:
            response.set_cookie(name, value, max_age=prefs.YEAR_S, samesite="lax",
                                secure=secure, path="/")


def start_session(app: FastAPI, request: Request, response: Response, account: Account) -> None:
    """A new session for ``account`` (never the one the browser brought: no fixation)."""
    from studio.app import COOKIE, SESSION_TTL_S

    settings: Settings = app.state.settings
    old = request.cookies.get(COOKIE)
    if old:
        app.state.sessions.revoke(old)  # its row and its approver keys too
    value = app.state.sessions.issue(account_id=account.id,
                                     user_agent=request.headers.get("user-agent", ""))
    secure = _secure(request, settings)
    response.set_cookie(COOKIE, value, max_age=SESSION_TTL_S, httponly=True,
                        samesite="strict", secure=secure, path="/")
    response.delete_cookie(CHALLENGE_COOKIE, path="/login", httponly=True, samesite="strict",
                           secure=secure)
    _pref_cookies(response, account, secure)
    app.state.notifier.security("signin", account)


def second_step(app: FastAPI, request: Request, account: Account, nxt: str, html: bool
                ) -> Response:
    """The password was right and the account has two-step sign-in: no session yet, only a
    short-lived challenge cookie for ``/login/2fa``."""
    token = app.state.challenges.open(account.id, nxt,
                                      gen=app.state.accounts.credential_gen(account.id))
    response: Response = (RedirectResponse("/login/2fa", 303) if html
                          else JSONResponse({"two_factor": True, "next": "/login/2fa"}))
    response.set_cookie(CHALLENGE_COOKIE, token, max_age=int(accounts.Challenges.TTL_S),
                        httponly=True, samesite="strict",
                        secure=_secure(request, app.state.settings), path="/login")
    return response


def _fields(raw: bytes) -> dict[str, str] | None:
    try:
        form = parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=MAX_FIELDS)
    except (ValueError, UnicodeDecodeError):
        return None
    return {k: v[0] for k, v in form.items()}


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             templates: Jinja2Templates, session_of: Callable[[Request], str | None]) -> None:
    from studio.app import COOKIE, LOGIN_BODY_TIMEOUT_S, _same_origin, client_ip

    store: Accounts = app.state.accounts
    templates.env.globals["qr_svg"] = accounts.qr_svg
    templates.env.globals["format_secret"] = accounts.format_secret

    def base(request: Request, logged_in: bool) -> dict[str, Any]:
        return {"seq": ledger.seq(), "epoch": ledger.epoch, "logged_in": logged_in,
                "login_enabled": login_enabled(app, settings), "read_only": settings.read_only,
                "noindex": True}

    def current(request: Request) -> tuple[str, Account] | None:
        cookie = request.cookies.get(COOKIE)
        sid = app.state.sessions.check(cookie)
        account = app.state.sessions.account_of(cookie) if sid else None
        return (sid, account) if sid and account else None

    def page(request: Request, sid: str, account: Account, *, status: int = 200,
             errors: dict[str, tuple[str, str]] | None = None, codes: list[str] | None = None,
             values: dict[str, str] | None = None, whatsapp_code: str = "") -> Response:
        done = request.query_params.get("done", "")
        pending = store.totp_pending_secret(account.id) if account.totp_pending else None
        uri = accounts.provisioning_uri(pending, account.username) if pending else ""
        response = templates.TemplateResponse(request, "account.html", {
            **base(request, True), "account": account, "errors": errors or {},
            "done": done if done in DONE else "", "codes": codes or [],
            "pending_secret": pending or "", "pending_uri": uri,
            "sessions": store.sessions(account.id, sid),
            "codes_left": store.recovery_codes_left(account.id),
            "values": values or account.profile(), "langs": i18n.LANGS,
            "themes": prefs.THEMES, "palettes": prefs.PALETTES,
            "advertiser_types": accounts.ADVERTISER_TYPES, "sections": SECTIONS,
            "min_password": accounts.MIN_PASSWORD_LEN,
            "notify_prefs": notify.preferences(conn, account.id),
            "whatsapp_prefs": whatsapp.preferences(conn, account.id),
            "whatsapp_enabled": settings.whatsapp.enabled,
            "whatsapp_code": whatsapp_code,
            "whatsapp_studio_number": settings.whatsapp.display_number,
            "whatsapp_code_minutes": whatsapp.CODE_TTL_S // 60,
        }, status_code=status, headers=NO_STORE)
        return response

    def done(key: str) -> Response:
        anchor = {"profile": "profile", "password": "password", "2fa_started": "two-factor",
                  "2fa_cancelled": "two-factor", "2fa_off": "two-factor",
                  "session_revoked": "sessions", "sessions_revoked": "sessions",
                  "notify": "notifications"}[key]
        return RedirectResponse(f"/account?done={key}#{anchor}", 303, headers=NO_STORE)

    async def posted(request: Request) -> tuple[str, Account, dict[str, str]] | Response:
        """Same-origin, CSRF field, live session, writable; the form fields."""
        if not _same_origin(request):
            return JSONResponse({"error": "cross_origin"}, 403)
        fields = _fields(await request.body())
        if fields is None:
            return JSONResponse({"error": "invalid_form"}, 400)
        if not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                 fields.get(auth.CSRF_FIELD)):
            return JSONResponse({"error": "csrf"}, 403)
        me = current(request)
        if me is None:
            return JSONResponse({"error": "login_required"}, 401)
        if settings.read_only:
            return JSONResponse({"error": "read_only"}, 503)
        return me[0], me[1], fields

    def limited(request: Request) -> tuple[str, bool]:
        """Reserve a password/code attempt in the login limiter (the client's prefix)."""
        ip = client_ip(request, settings.trusted_proxies)
        return ip, bool(app.state.limiter.reserve(ip))

    def refused(request: Request, sid: str, account: Account, form: str, ip: str) -> Response:
        r = page(request, sid, account, status=429,
                 errors={form: ("err.too_many_attempts", "")})
        r.headers["Retry-After"] = str(app.state.limiter.retry_after(ip))
        return r

    # -- pages -----------------------------------------------------------------------------

    @app.get("/account", response_class=HTMLResponse)
    def account_page(request: Request) -> Response:
        me = current(request)
        if me is None:
            return RedirectResponse("/login?next=/account", 303)
        return page(request, *me)

    @app.get("/account/export.json")
    def export(request: Request) -> Response:
        me = current(request)
        if me is None:
            return JSONResponse({"error": "login_required"}, 401)
        sid, account = me
        data = store.export(account.id, ledger.receipts(), sid)
        app.state.notifier.security("export_ready", account)
        body = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True)
        return Response(body, media_type="application/json", headers={
            **NO_STORE, "Content-Disposition":
                f'attachment; filename="muse-studio-account-{account.username}.json"'})

    # -- forms -----------------------------------------------------------------------------

    @app.post("/account/profile")
    async def save_profile(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        values = {k: fields.get(k, "") for k in Account.PROFILE}
        try:
            updated = store.update_profile(account.id, values)
        except AccountError as exc:
            return page(request, sid, account, status=400, values=values,
                        errors={"profile": ("err." + exc.code, str(exc))})
        response = done("profile")
        _pref_cookies(response, updated, _secure(request, settings))
        return response

    @app.post("/account/password")
    async def change_password(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        ip, ok = limited(request)
        if not ok:
            return refused(request, sid, account, "password", ip)
        try:
            store.change_password(account.id, fields.get("current", ""),
                                  fields.get("new", ""), fields.get("confirm", ""),
                                  keep_sid=sid)
        except AccountError as exc:
            app.state.limiter.settle(ip, not isinstance(exc, accounts.WrongPassword))
            field = "current" if isinstance(exc, accounts.WrongPassword) else "new"
            return page(request, sid, account, status=400,
                        errors={"password": ("err." + exc.code, field)})
        app.state.limiter.settle(ip, True)
        app.state.challenges.drop_account(account.id)
        app.state.notifier.security("password_changed", account)
        return done("password")

    @app.post("/account/2fa/start")
    async def totp_start(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        ip, ok = limited(request)
        if not ok:
            return refused(request, sid, account, "two_factor", ip)
        try:
            store.totp_begin(account.id, fields.get("password", ""))
        except AccountError as exc:
            app.state.limiter.settle(ip, not isinstance(exc, accounts.WrongPassword))
            return page(request, sid, account, status=400,
                        errors={"two_factor": ("err." + exc.code, "")})
        app.state.limiter.settle(ip, True)
        return done("2fa_started")

    @app.post("/account/2fa/confirm")
    async def totp_confirm(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        ip, ok = limited(request)
        if not ok:
            return refused(request, sid, account, "two_factor", ip)
        try:
            codes = store.totp_confirm(account.id, fields.get("code", ""))
        except AccountError as exc:
            app.state.limiter.settle(ip, not isinstance(exc, accounts.WrongCode))
            return page(request, sid, account, status=400,
                        errors={"two_factor": ("err." + exc.code, "")})
        app.state.limiter.settle(ip, True)
        fresh = store.get(account.id)
        assert fresh is not None
        app.state.notifier.security("two_factor_on", fresh)
        return page(request, sid, fresh, codes=codes)

    @app.post("/account/2fa/cancel")
    async def totp_cancel(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        _, account, _ = got
        store.totp_cancel(account.id)
        return done("2fa_cancelled")

    @app.post("/account/2fa/disable")
    async def totp_disable(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        ip, ok = limited(request)
        if not ok:
            return refused(request, sid, account, "two_factor", ip)
        try:
            store.totp_disable(account.id, fields.get("password", ""), fields.get("code", ""))
        except AccountError as exc:
            app.state.limiter.settle(ip, not isinstance(exc, (
                accounts.WrongPassword, accounts.WrongCode, accounts.SecondFactorLocked)))
            return page(request, sid, account, status=400,
                        errors={"two_factor": ("err." + exc.code, "")})
        app.state.limiter.settle(ip, True)
        app.state.challenges.drop_account(account.id)
        app.state.notifier.security("two_factor_off", account)
        return done("2fa_off")

    @app.post("/account/sessions/revoke")
    async def revoke_one(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        if not store.revoke_session(account.id, fields.get("ref", ""), current_sid=sid):
            return page(request, sid, account, status=404,
                        errors={"sessions": ("err.unknown_session", "")})
        return done("session_revoked")

    @app.post("/account/sessions/revoke-others")
    async def revoke_others(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, _ = got
        store.revoke_others(account.id, current_sid=sid)
        return done("sessions_revoked")

    @app.post("/account/notifications")
    async def save_notifications(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        _, account, fields = got
        if account.is_demo:  # the shared demo account never sends mail
            return JSONResponse({"error": "demo_account"}, 403)
        try:  # the number first: an invalid one changes nothing
            whatsapp.set_preferences(conn, account.id, number=fields.get("whatsapp_number", ""),
                                     on="whatsapp" in fields)
        except ValueError:
            sid, _, _ = got
            return page(request, sid, account, status=422,
                        errors={"notifications": ("account.whatsapp.invalid", "")})
        notify.set_preferences(conn, account.id, campaigns="campaigns" in fields,
                               leads="leads" in fields)
        prefs = whatsapp.preferences(conn, account.id)
        if prefs["number"] and not prefs["verified"]:  # pending: show a code to send
            sid, _, _ = got
            return page(request, sid, account,
                        whatsapp_code=whatsapp.issue_code(conn, account.id))
        return done("notify")

    @app.post("/account/whatsapp/code")
    async def whatsapp_code(request: Request) -> Response:
        """A new one-time code for the pending number (shown once, on this page)."""
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, _ = got
        if account.is_demo:
            return JSONResponse({"error": "demo_account"}, 403)
        try:
            code = whatsapp.issue_code(conn, account.id)
        except ValueError:
            return done("notify")
        return page(request, sid, account, whatsapp_code=code)

    @app.post("/account/delete")
    async def delete(request: Request) -> Response:
        got = await posted(request)
        if isinstance(got, Response):
            return got
        sid, account, fields = got
        ip, ok = limited(request)
        if not ok:
            return refused(request, sid, account, "delete", ip)
        try:
            store.delete(account.id, confirm=fields.get("confirm", ""),
                         password=fields.get("password", ""), code=fields.get("code", ""))
        except AccountError as exc:
            app.state.limiter.settle(ip, not isinstance(exc, (
                accounts.WrongPassword, accounts.WrongCode, accounts.SecondFactorLocked)))
            return page(request, sid, account, status=400,
                        errors={"delete": ("err." + exc.code, "")})
        app.state.limiter.settle(ip, True)
        app.state.challenges.drop_account(account.id)
        app.state.notifier.security("deletion_requested", account)  # address read before erasure
        app.state.sessions.revoke(request.cookies.get(COOKIE))
        response = templates.TemplateResponse(request, "account_deleted.html",
                                              base(request, False), headers=NO_STORE)
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict",
                               secure=_secure(request, settings))
        return response

    # -- the login's second step -------------------------------------------------------------

    def second_html(request: Request, error: str = "", status: int = 200,
                    headers: dict[str, str] | None = None) -> Response:
        return templates.TemplateResponse(request, "login_2fa.html", {
            **base(request, False), "error": error,
        }, status_code=status, headers={**NO_STORE, **(headers or {})})

    @app.get("/login/2fa", response_class=HTMLResponse)
    def second_page(request: Request) -> Response:
        if app.state.challenges.get(request.cookies.get(CHALLENGE_COOKIE)) is None:
            return RedirectResponse("/login", 303)
        return second_html(request)

    @app.post("/login/2fa")
    async def second_submit(request: Request) -> Response:
        html = "text/html" in request.headers.get("accept", "")

        def fail(code: str, status: int, retry: int | None = None) -> Response:
            headers = {"Retry-After": str(retry)} if retry else None
            if html:  # one generic sentence whatever went wrong
                return second_html(request, i18n.text(request.state.lang, "login.2fa_failed"),
                                   status, headers)
            return JSONResponse({"error": code}, status, headers=headers)

        if not _same_origin(request):
            return fail("cross_origin", 403)
        ip = client_ip(request, settings.trusted_proxies)
        if not app.state.limiter.reserve(ip):  # reserved before the body is awaited
            return fail("too_many_attempts", 429, app.state.limiter.retry_after(ip))
        try:
            raw = await asyncio.wait_for(request.body(), LOGIN_BODY_TIMEOUT_S)
        except TimeoutError:
            return fail("body_timeout", 408)
        fields = _fields(raw) or {}
        if not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                 fields.get(auth.CSRF_FIELD)):
            app.state.limiter.settle(ip, False)
            return fail("csrf", 403)
        token = request.cookies.get(CHALLENGE_COOKIE)
        challenge = app.state.challenges.get(token)
        ok = challenge is not None and store.verify_second_factor(
            challenge.account_id, fields.get("code", ""), gen=challenge.gen)
        if not app.state.limiter.settle(ip, ok):
            return fail("too_many_attempts", 429, app.state.limiter.retry_after(ip))
        if not ok or challenge is None:
            app.state.challenges.fail(token)
            return fail("invalid_code", 401)
        app.state.challenges.close(token)
        account = store.get(challenge.account_id)
        if account is None or account.status != "active":
            return fail("invalid_code", 401)
        nxt = auth.safe_next(challenge.next)
        response: Response = (RedirectResponse(nxt, 303) if html
                              else JSONResponse({"ok": True}))
        start_session(app, request, response, account)
        return response
