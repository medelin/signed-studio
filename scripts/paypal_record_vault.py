"""Record a real PayPal sandbox Vault v3 setup and a mandate-style charge into
``fixtures/cassettes/paypal/sandbox_vault.json``. Sandbox money only.

Saving a PayPal needs the sandbox **buyer** (a Personal sandbox account) to approve it in
a browser, so this runs in two halves:

    .venv/bin/python scripts/paypal_record_vault.py --start
        creates a setup token, prints the approval URL and stops. The setup token id and
        the answers recorded so far are kept outside the repo
        (``~/.local/state/muse-pay/vault-record.json``).

    .venv/bin/python scripts/paypal_record_vault.py --finish [--wait MINUTES]
        after the buyer approved: reads the setup token (APPROVED), exchanges it for a
        vault (payment) token, charges it with an ``AUTHORIZE`` order (no buyer present;
        ``custom_id`` = a made-up ad hash, the same body ``studio.paypal.orders`` builds),
        voids that authorization (the sandbox money goes back), deletes the vault token,
        and writes the cassette.

The OAuth exchange is never recorded. Answers are scrubbed like ``paypal_record.py``:
payer, shipping and payment-source identities, the payee, customer ids and debug ids are
placeholders; the vault token id (deleted at the end anyway) is replaced everywhere by
``VAULT-ID``; the file is checked for the client id, the secret and the token before it
is written.
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
sys.path.insert(0, str(ROOT / "scripts"))

from paypal_record import say, scrub  # noqa: E402

from studio.funding import quote  # noqa: E402
from studio.paypal import orders, vault  # noqa: E402
from studio.paypal.client import PayPalClient, PayPalError, request_id  # noqa: E402
from studio.settings import read_env_file  # noqa: E402

OUT = ROOT / "fixtures" / "cassettes" / "paypal" / "sandbox_vault.json"
STATE = Path.home() / ".local" / "state" / "muse-pay" / "vault-record.json"
VAULT_ID = "VAULT-ID"  # a valid PayPal path id, so the replay accepts it
RETURN = "https://example.com/account/paypal/vault/return"
CANCEL = "https://example.com/account?paypal=cancelled"


def recorder(interactions: list[dict[str, Any]]) -> Any:
    def hook(response: httpx.Response) -> None:
        req = response.request
        if req.url.path == "/v1/oauth2/token":
            return
        if req.method == "GET" and interactions and interactions[-1]["request"] == {
                "method": "GET", "path": req.url.path}:
            interactions.pop()  # polling: keep only the last read
        response.read()
        body = json.loads(req.content) if req.content else None
        try:
            answer = scrub(response.json()) if response.content else None
        except ValueError:
            answer = {"_non_json": True}
        if isinstance(answer, dict) and isinstance(answer.get("customer"), dict):
            answer["customer"] = {"id": "SCRUBBEDCUSTOMER"}
        interactions.append({"request": {"method": req.method, "path": req.url.path,
                                         **({"json": body} if body is not None else {})},
                             "response": {"status": response.status_code,
                                          **({"json": answer} if answer is not None else {})}})
    return hook


def client_for(env: dict[str, str], interactions: list[dict[str, Any]]) -> PayPalClient:
    client = PayPalClient(env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"])
    client._http.event_hooks["response"] = [recorder(interactions)]
    return client


def start(env: dict[str, str]) -> int:
    interactions: list[dict[str, Any]] = []
    client = client_for(env, interactions)
    tag = secrets.token_hex(6)
    try:
        sid, status, approve, _ = vault.create_setup_token(
            client, vault.setup_body(RETURN, CANCEL), request_id=request_id("vault-rec", tag))
    except PayPalError as exc:
        say(f"PayPal refused the setup token: {exc}")
        say("Is Vault (\"Save payment methods\") enabled for the sandbox REST app?")
        return 1
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.write_text(json.dumps({"tag": tag, "setup_token_id": sid,
                                 "interactions": interactions}))
    STATE.chmod(0o600)
    say(f"setup token {sid}: {status}")
    say("STOP: a sandbox buyer must approve saving PayPal. Open this URL in a browser and "
        "log in as the Personal sandbox account:")
    say(f"  {approve}")
    say("PayPal then redirects to example.com (that page does not matter). Afterwards run:")
    say("  .venv/bin/python scripts/paypal_record_vault.py --finish")
    return 0


def finish(env: dict[str, str], wait: float) -> int:
    saved = json.loads(STATE.read_text())
    interactions: list[dict[str, Any]] = saved["interactions"]
    client = client_for(env, interactions)
    tag, sid = saved["tag"], saved["setup_token_id"]
    deadline = time.monotonic() + wait * 60
    while True:
        status, _ = vault.get_setup_token(client, sid)
        if status == "APPROVED" or time.monotonic() > deadline:
            break
        say(f"setup token {sid}: {status}, waiting for the buyer …")
        time.sleep(10)
    if status != "APPROVED":
        say(f"the buyer has not approved yet ({status}); run --finish again later")
        return 2
    token, _ = vault.create_payment_token(client, sid, request_id=request_id("vault-rec",
                                                                              tag, "save"))
    say("vault token created (id not printed)")
    q = quote("GBP", 1500, 3, Decimal(5))
    body = orders.vault_order_body(q, content_sha256=secrets.token_hex(32),
                                   invoice_id=f"fund_vrec{tag}", reference_id=f"rec_{tag}",
                                   vault_id=token)
    held = orders.charge_vault(client, body, request_id=request_id("vault-rec", tag, "charge"))
    say(f"charged without the buyer: order {held.order_id}, authorization {held.id} "
        f"{held.status} {held.amount_minor} {held.currency}")
    void = orders.void(client, held.id, request_id=request_id("vault-rec", tag, "void"))
    say(f"authorization voided: {void.status}")
    vault.delete_payment_token(client, token)
    say("vault token deleted")
    access = client.access_token()
    doc = {"_provenance": "recorded",
           "_note": "Recorded from api-m.sandbox.paypal.com by "
                    "scripts/paypal_record_vault.py: a Vault v3 setup token approved by the "
                    "sandbox buyer in a browser, exchanged for a vault token, an AUTHORIZE "
                    "order paid from it with no buyer present, its authorization voided, "
                    "and the vault token deleted (sandbox money only). OAuth not recorded; "
                    "payer, payment_source identities, payee, customer and debug ids "
                    f"replaced by placeholders; the vault token id replaced by {VAULT_ID}.",
           "interactions": interactions}
    text = json.dumps(doc, indent=1).replace(token, VAULT_ID) + "\n"
    for secret in (env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"], access, token):
        if secret and secret in text:
            say("refusing to write: a credential or the vault id appears in the recording")
            return 1
    OUT.write_text(text)
    STATE.unlink()
    say(f"wrote {OUT.relative_to(ROOT)} ({len(interactions)} interactions)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", type=Path,
                    default=Path.home() / ".config" / "muse-pay" / "paypal-sandbox.env")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true", help="create the setup token, print "
                      "the buyer's approval URL and stop")
    mode.add_argument("--finish", action="store_true", help="after the buyer approved: "
                      "save, charge, void, delete and write the cassette")
    ap.add_argument("--wait", type=float, default=2.0, help="--finish: minutes to wait")
    args = ap.parse_args()
    env = read_env_file(args.env)
    return start(env) if args.start else finish(env, args.wait)


if __name__ == "__main__":
    raise SystemExit(main())
