"""Export and import routes of the web UI (``studio.exports`` does the work).

- ``GET /leads/export?format=csv|json|vcf`` (session): every lead as a download, masked
  exactly like ``/leads`` for the signed-in account (a trial or demo account gets
  initials and ``[email]`` / ``[phone]``, in every format).
- ``POST /leads/import`` (session, same-origin, writable, the ``csrf`` form field,
  multipart ``file`` + ``format``): only for an account that sees personal data; the
  page shows the counts and the line numbers of rejected rows. Never through a token:
  no bearer token can write personal data in.
- ``GET /report/statement?month=YYYY-MM&format=csv|json|hledger&profile=…`` (session):
  the monthly statement; another account's API runs are never in it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from studio import auth, exports
from studio.receipts import Ledger
from studio.routes_inbox import personal
from studio.settings import Settings

Guard = Callable[[Request], "JSONResponse | str"]
NO_STORE = {"Cache-Control": "no-store"}
# multipart overhead on top of the file itself (studio.app.UPLOAD_LIMITS)
IMPORT_BODY_LIMIT = exports.MAX_IMPORT_BYTES + 64 * 1024


def download(body: str, media_type: str, filename: str) -> Response:
    return Response(body.encode(), media_type=media_type, headers={
        **NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff"})


def account_of(app: FastAPI, request: Request) -> Any:
    from studio.app import COOKIE

    sessions = getattr(app.state, "sessions", None)
    return sessions.account_of(request.cookies.get(COOKIE)) if sessions else None


def foreign_runs(app: FastAPI, account: Any) -> set[str]:
    """Runs of briefs another account drafted through its token (``studio.assistants``)."""
    svc = getattr(app.state, "assistants", None)
    if svc is None or account is None:
        return set()
    return svc.foreign_runs(account.id)  # type: ignore[no-any-return]


def statement_view(app: FastAPI, conn: Any, request: Request, month: str,
                   preview: int = 20) -> dict[str, Any]:
    """What ``/report`` shows of the statement: the month, its first rows and totals."""
    rows = exports.statement(conn, month,
                             exclude_runs=foreign_runs(app, account_of(app, request)))
    return {"month": month, "rows": rows[:preview], "more": max(0, len(rows) - preview),
            "totals": exports.totals(rows), "profiles": tuple(exports.PROFILES),
            "columns": exports.STATEMENT_COLUMNS}


def register(app: FastAPI, settings: Settings, ledger: Ledger, conn: Any,
             write_guard: Guard, session_of: Callable[[Request], str | None]) -> None:

    @app.get("/leads/export")
    def leads_export(request: Request, format: str = "csv") -> Response:  # noqa: A002
        if session_of(request) is None:
            return JSONResponse({"error": "login_required"}, 401, headers=NO_STORE)
        if format not in exports.LEAD_FORMATS:
            return JSONResponse({"error": "invalid_format"}, 400, headers=NO_STORE)
        rows = exports.leads_rows(conn, masked=not personal(app, request))
        return download(*exports.lead_file(rows, format))

    @app.post("/leads/import")
    async def leads_import(request: Request) -> Response:
        guard = write_guard(request)
        if isinstance(guard, JSONResponse):
            return guard
        page = app.state.leads_page
        try:
            form = await request.form(max_files=1, max_fields=4)
        except Exception:  # noqa: BLE001 - a malformed multipart body
            return page(request, 400, {"import_error": "unreadable"})
        if not auth.tokens_match(request.cookies.get(auth.CSRF_COOKIE),
                                 str(form.get(auth.CSRF_FIELD) or "")):
            return JSONResponse({"error": "csrf"}, 403, headers=NO_STORE)
        if not personal(app, request):  # trial / demo: the form is not even shown
            return page(request, 403, {"import_error": "import_not_allowed"})
        kind = str(form.get("format") or "")
        upload = form.get("file")
        if kind not in exports.LEAD_FORMATS:
            return page(request, 400, {"import_error": "unknown_format"})
        if upload is None or isinstance(upload, str):
            return page(request, 400, {"import_error": "empty"})
        data = await upload.read(exports.MAX_IMPORT_BYTES + 1)
        if not data:
            return page(request, 400, {"import_error": "empty"})
        try:
            result = exports.import_leads(conn, data, kind, ledger=ledger)
        except exports.ImportRefused as exc:
            return page(request, 413 if exc.code == "too_large" else 400,
                        {"import_error": exc.code})
        return page(request, 200, {"imported": result.view()})

    @app.get("/report/statement")
    def statement(request: Request, month: str = "", format: str = "csv",  # noqa: A002
                  profile: str = "generic") -> Response:
        if session_of(request) is None:
            return JSONResponse({"error": "login_required"}, 401, headers=NO_STORE)
        month = month or exports.current_month()
        if exports.MONTH.fullmatch(month) is None:
            return JSONResponse({"error": "invalid_month"}, 400, headers=NO_STORE)
        if format not in exports.STATEMENT_FORMATS:
            return JSONResponse({"error": "invalid_format"}, 400, headers=NO_STORE)
        if profile not in exports.PROFILES:
            return JSONResponse({"error": "unknown_profile"}, 400, headers=NO_STORE)
        rows = exports.statement(conn, month,
                                 exclude_runs=foreign_runs(app, account_of(app, request)))
        return download(*exports.statement_file(month, rows, format, profile))
