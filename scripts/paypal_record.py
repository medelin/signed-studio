"""Record real PayPal sandbox answers into ``fixtures/cassettes/paypal/sandbox.json``.

    .venv/bin/python scripts/paypal_record.py [--env ~/.config/muse-pay/paypal-sandbox.env]

Moves no money: it creates one ``AUTHORIZE`` order (nobody approves it, so it expires),
reads it back, and records PayPal's ``ORDER_NOT_APPROVED`` refusal to authorize it. The
OAuth exchange is not recorded (it holds the access token). Answers are scrubbed: any
``payer`` block is dropped, the ``payee`` (the sandbox business account) and ``debug_id``
are replaced by placeholders, and the file is checked for the
client id, the secret and the token before it is written.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
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

OUT = ROOT / "fixtures" / "cassettes" / "paypal" / "sandbox.json"


PAYEE = {"email_address": "sb-business@business.example.com", "merchant_id": "SANDBOXMERCH01"}


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("scrubbed" if k == "debug_id" else PAYEE if k == "payee" else scrub(v))
                for k, v in value.items() if k != "payer"}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", type=Path,
                    default=Path.home() / ".config" / "muse-pay" / "paypal-sandbox.env")
    env = read_env_file(ap.parse_args().env)
    interactions: list[dict[str, Any]] = []

    def hook(response: httpx.Response) -> None:
        req = response.request
        if req.url.path == "/v1/oauth2/token":
            return
        response.read()
        body = json.loads(req.content) if req.content else None
        interactions.append({"request": {"method": req.method, "path": req.url.path,
                                         **({"json": body} if body is not None else {})},
                             "response": {"status": response.status_code,
                                          "json": scrub(response.json())}})

    client = PayPalClient(env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"])
    client._http.event_hooks["response"] = [hook]
    tag = secrets.token_hex(6)
    content = secrets.token_hex(32)
    body = orders.order_body(quote("GBP", 1500, 3, Decimal(5)), content_sha256=content,
                             invoice_id=f"fund_rec{tag}", reference_id=f"rec_{tag}")
    created = orders.create(client, body, request_id=request_id("record", tag, "create"))
    orders.get(client, created.id)
    try:
        orders.authorize(client, created.id, request_id=request_id("record", tag, "auth"))
    except PayPalError as exc:
        print(f"authorize refused as expected: {exc.issue}")
    token = client.access_token()
    doc = {"_provenance": "recorded",
           "_note": "Recorded from api-m.sandbox.paypal.com by scripts/paypal_record.py: one "
                    "AUTHORIZE order nobody approved (no money moved), read back, and the "
                    "ORDER_NOT_APPROVED refusal. OAuth not recorded; payer and payee blocks "
                    "and debug_ids replaced by placeholders.",
           "interactions": interactions}
    text = json.dumps(doc, indent=1) + "\n"
    for secret in (env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"], token):
        if secret and secret in text:
            print("refusing to write: a credential appears in the recording", file=sys.stderr)
            return 1
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(interactions)} interactions, "
          f"order {created.id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
