"""Proof without an account.

- ``GET /trust``: the ledger key, receipt count, newest hash, the last self-check and the
  test counts (``studio.trust``). Public, no secrets.
- ``GET /verify/{hash}``: does a receipt with this hash exist in the chain and is it signed?
  Answers with the hash, the step name and the time, nothing else. Rate-limited per client
  prefix with the login's limiter class (its own instance, so lookups never lock logins).
- ``GET /receipts/{id}/print``: one receipt as a printable page with a QR code pointing at
  ``/verify/<hash>``. Same access rule as ``/receipts``: a signed-in session sees the whole
  receipt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from studio.accounts import qr_svg
from studio.receipts import Ledger
from studio.routes_receipts import receipt_view, utc
from studio.settings import Settings
from studio.trust import Trust, eval_summary, is_hash
from studio.ui import i18n

REPO_URL = "https://github.com/medelin/signed-studio"
VIDEO_URL = "https://youtu.be/NfhqFX9LYE8"
VERIFY_CMD = "studio verify receipts.jsonl --server https://{host}"


def register(app: FastAPI, settings: Settings, ledger: Ledger, templates: Jinja2Templates,
             session_of: Callable[[Request], str | None],
             approver_keys: Callable[[], Mapping[str, Any]],
             client_ip: Callable[[Request], str]) -> Trust:
    from studio.app import LoginLimiter  # lazy: studio.app imports this module

    trust = Trust(ledger, approver_keys)
    lookups = LoginLimiter(max_attempts=60, window_s=60)  # per prefix, per minute
    app.state.trust = trust

    def base(request: Request) -> dict[str, Any]:
        return {"seq": ledger.seq(), "epoch": ledger.epoch,
                "logged_in": session_of(request) is not None,
                "login_enabled": settings.judge_login_enabled,
                "read_only": settings.read_only}

    def origin(request: Request) -> str:
        return settings.public_url.rstrip("/") or str(request.base_url).rstrip("/")

    @app.get("/trust", response_class=HTMLResponse)
    def trust_page(request: Request) -> Response:
        check = trust.self_check()
        host = origin(request)
        return templates.TemplateResponse(request, "trust.html", {
            **base(request), "key": trust.key_hex(), "stats": trust.stats(),
            "check": check, "checked_at": utc(check.at) + " UTC",
            "summary": eval_summary(), "repo_url": REPO_URL, "video_url": VIDEO_URL,
            "command": VERIFY_CMD.format(host=host.split("://", 1)[-1])})

    @app.get("/verify/{digest}")
    def verify_hash(request: Request, digest: str) -> Response:
        ip = client_ip(request)
        if not lookups.reserve(ip):
            return JSONResponse({"error": "too_many_attempts"}, 429,
                                headers={"Retry-After": str(lookups.retry_after(ip))})
        digest = digest.lower()
        receipt = trust.find(digest) if is_hash(digest) else None
        answer: dict[str, Any] = {"hash": digest, "found": receipt is not None}
        if receipt is not None:
            ok = trust.signed(receipt)
            answer.update(signed=ok, step=f"{receipt.agent}/{receipt.kind}",
                          time=datetime.fromtimestamp(receipt.ts, UTC).isoformat())
            status = 200
        else:
            status = 400 if not is_hash(digest) else 404
        if "text/html" not in request.headers.get("accept", ""):
            return JSONResponse(answer, status, headers={"Cache-Control": "no-store"})
        state = ("found" if answer.get("signed") else "unsigned" if receipt is not None
                 else "unknown" if is_hash(digest) else "bad_hash")
        response = templates.TemplateResponse(request, "verify.html", {
            **base(request), "noindex": True, "answer": answer, "state": state,
            "when": utc(receipt.ts) if receipt else ""}, status_code=status)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/receipts/{receipt_id}/print", response_class=HTMLResponse)
    def print_receipt(request: Request, receipt_id: str) -> Response:
        if session_of(request) is None:
            return RedirectResponse(f"/login?next=/receipts/{receipt_id}/print", 303)
        receipt = next((r for r in ledger.receipts() if r.id == receipt_id), None)
        if receipt is None:
            return templates.TemplateResponse(request, "404.html", base(request),
                                              status_code=404)
        view = receipt_view(receipt, full=True)
        fields = [(k, v) for k, v in view.items() if k not in ("hash", "sig", "prev")]
        url = f"{origin(request)}/verify/{receipt.hash}"
        return templates.TemplateResponse(request, "receipt_print.html", {
            **base(request), "noindex": True, "r": view, "fields": fields, "url": url,
            "qr": qr_svg(url, i18n.text(request.state.lang, "print.qr_label")),
            "key": trust.key_hex(), "when": utc(receipt.ts)})

    return trust
