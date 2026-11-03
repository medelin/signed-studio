"""Record real PayPal sandbox answers into ``fixtures/cassettes/paypal/``.

    .venv/bin/python scripts/paypal_record.py [--env FILE]          # sandbox.json
    .venv/bin/python scripts/paypal_record.py --full [--env FILE]   # sandbox_full.json

Default: moves no money. It creates one ``AUTHORIZE`` order (nobody approves it, so it
expires), reads it back, and records PayPal's ``ORDER_NOT_APPROVED`` refusal.

``--full``: the whole money loop, which needs a person to approve two orders as the
sandbox **buyer** (a Personal sandbox account) in a browser. It creates order A and
order B, prints both approval URLs, and polls PayPal until each one is APPROVED (up to
``--wait`` minutes). Then A is authorized, captured and partly refunded, and B is
authorized and voided. Sandbox money only.

The OAuth exchange is never recorded (it holds the access token). Answers are scrubbed:
``payer`` and ``shipping`` blocks are dropped, ``payment_source`` identities, the
``payee`` (the sandbox business account) and ``debug_id`` are replaced by placeholders,
and the file is checked for the client id, the secret and the token before it is written.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.funding import quote  # noqa: E402
from studio.paypal import orders  # noqa: E402
from studio.paypal.client import PayPalClient, PayPalError, request_id  # noqa: E402
from studio.settings import read_env_file  # noqa: E402

OUT = ROOT / "fixtures" / "cassettes" / "paypal"
PAYEE = {"email_address": "sb-business@business.example.com", "merchant_id": "SANDBOXMERCH01"}
SOURCE = {"email_address": "sb-buyer@personal.example.com", "account_id": "SANDBOXBUYER01",
          "account_status": "VERIFIED", "name": {"given_name": "Sandbox", "surname": "Buyer"},
          "address": {"country_code": "PL"}}
DROP = frozenset({"payer", "shipping"})


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in DROP:
                continue
            if k == "debug_id":
                out[k] = "scrubbed"
            elif k == "payee":
                out[k] = PAYEE
            elif k == "payment_source" and isinstance(v, dict):
                out[k] = {name: SOURCE if name == "paypal" else {} for name in v}
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def say(text: str) -> None:
    print(text, flush=True)


def wait_approved(client: PayPalClient, order_id: str, minutes: float) -> None:
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        status = orders.get(client, order_id).status
        if status == "APPROVED":
            say(f"order {order_id}: APPROVED by the buyer")
            return
        time.sleep(5)
    raise SystemExit(f"order {order_id} was not approved within {minutes} minutes")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", type=Path,
                    default=Path.home() / ".config" / "muse-pay" / "paypal-sandbox.env")
    ap.add_argument("--full", action="store_true",
                    help="authorize/capture/refund/void: a person approves two orders")
    ap.add_argument("--wait", type=float, default=30.0, help="minutes to wait for approval")
    args = ap.parse_args()
    env = read_env_file(args.env)
    interactions: list[dict[str, Any]] = []

    def hook(response: httpx.Response) -> None:
        req = response.request
        if req.url.path == "/v1/oauth2/token":
            return
        if req.method == "GET" and interactions and interactions[-1]["request"] == {
                "method": "GET", "path": req.url.path}:
            interactions.pop()  # polling: keep only the last read
        response.read()
        body = json.loads(req.content) if req.content else None
        answer = scrub(response.json()) if response.content else None
        interactions.append({"request": {"method": req.method, "path": req.url.path,
                                         **({"json": body} if body is not None else {})},
                             "response": {"status": response.status_code,
                                          **({"json": answer} if answer is not None else {})}})

    client = PayPalClient(env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"])
    client._http.event_hooks["response"] = [hook]
    tag = secrets.token_hex(6)

    def create(name: str) -> orders.Result:
        q = quote("GBP", 1500, 3, Decimal(5))
        body = orders.order_body(q, content_sha256=secrets.token_hex(32),
                                 invoice_id=f"fund_rec{tag}{name}", reference_id=f"rec_{tag}")
        return orders.create(client, body, request_id=request_id("record", tag, name))

    if not args.full:
        created = create("a")
        orders.get(client, created.id)
        try:
            orders.authorize(client, created.id, request_id=request_id("record", tag, "auth"))
        except PayPalError as exc:
            say(f"authorize refused as expected: {exc.issue}")
        name, note = "sandbox.json", (
            "one AUTHORIZE order nobody approved (no money moved), read back, and the "
            "ORDER_NOT_APPROVED refusal")
    else:
        a, b = create("a"), create("b")
        say("Approve BOTH orders as the sandbox buyer (Personal account), in a browser:")
        say(f"  order A {a.id}: {a.approve_url}")
        say(f"  order B {b.id}: {b.approve_url}")
        say(f"waiting up to {args.wait:g} minutes …")
        wait_approved(client, a.id, args.wait)
        wait_approved(client, b.id, args.wait)
        auth_a = orders.authorize(client, a.id, request_id=request_id("record", tag, "auth-a"))
        say(f"A authorized: {auth_a.id} {auth_a.status} {auth_a.amount_minor}")
        cap = orders.capture(client, auth_a.id, amount_minor=auth_a.amount_minor,
                             currency=auth_a.currency, invoice_id=f"fund_rec{tag}a",
                             request_id=request_id("record", tag, "capture-a"))
        say(f"A captured: {cap.id} {cap.status} {cap.amount_minor}")
        ref = orders.refund(client, cap.id, amount_minor=1449, currency=cap.currency,
                            invoice_id=f"fund_rec{tag}a-r1",
                            request_id=request_id("record", tag, "refund-a"))
        say(f"A refunded: {ref.id} {ref.status} {ref.amount_minor}")
        auth_b = orders.authorize(client, b.id, request_id=request_id("record", tag, "auth-b"))
        void = orders.void(client, auth_b.id, request_id=request_id("record", tag, "void-b"))
        say(f"B authorized then voided: {auth_b.id} {void.status}")
        name, note = "sandbox_full.json", (
            "two AUTHORIZE orders approved by the sandbox buyer in a browser; A authorized, "
            "captured and partly refunded, B authorized and voided (sandbox money only)")
    token = client.access_token()
    doc = {"_provenance": "recorded",
           "_note": f"Recorded from api-m.sandbox.paypal.com by scripts/paypal_record.py: "
                    f"{note}. OAuth not recorded; payer, shipping, payment_source identities, "
                    "payee and debug_ids replaced by placeholders.",
           "interactions": interactions}
    text = json.dumps(doc, indent=1) + "\n"
    for secret in (env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"], token):
        if secret and secret in text:
            say("refusing to write: a credential appears in the recording")
            return 1
    out = OUT / name
    out.write_text(text)
    say(f"wrote {out.relative_to(ROOT)} ({len(interactions)} interactions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
