"""Orders v2 and Payments v2: create, get, authorize, capture, reauthorize, void, refund.

The order body is built from a ``Quote`` and the ad's content hash only (``order_body``),
never from model text: ``purchase_units[0].custom_id`` is the ad's ``content_sha256`` (64
hex characters; PayPal allows 127), so PayPal's own record of the payment names the exact
bytes the owner signs. ``invoice_id`` is the funding id (unique per funding, so PayPal
refuses a second payment for it), and the amount carries a breakdown with two items:
"Ad budget" and "Studio fee".

Each call returns a small parsed result with the PayPal id, status and amount in minor
units, plus ``body_sha256`` (the SHA-256 of PayPal's raw answer) for the receipt. Parsing
is strict: an answer without the expected id, status or amount raises ``PayPalError``
(``UNEXPECTED_RESPONSE``) rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from studio.funding import INTENT, FundingError, Quote, format_amount, parse_amount
from studio.paypal.client import PayPalClient, PayPalError, Reply, path_id

BRAND = "Signed Studio"
SOFT_DESCRIPTOR = "SIGNEDSTUDIO"  # what the buyer's statement shows (max 22 characters)


@dataclass(frozen=True)
class Result:
    """One PayPal object after a call: ``kind`` is order, authorization, capture or
    refund."""

    kind: str
    id: str
    status: str
    amount_minor: int
    currency: str
    body_sha256: str
    custom_id: str = ""
    invoice_id: str = ""
    approve_url: str = ""
    expiration_time: str = ""
    authorization_id: str = ""  # an order's first authorization (after authorize)
    order_id: str = ""  # the order an authorization belongs to (vault charges)


def _money(minor: int, currency: str) -> dict[str, str]:
    return {"currency_code": currency, "value": format_amount(minor, currency)}


def order_body(q: Quote, *, content_sha256: str, invoice_id: str,
               reference_id: str) -> dict[str, Any]:
    """The ``POST /v2/checkout/orders`` body for one funding (``intent: AUTHORIZE``)."""
    items = [{"name": "Ad budget", "quantity": "1", "category": "DIGITAL_GOODS",
              "description": f"{q.days} day(s) of ads",
              "unit_amount": _money(q.budget_minor, q.currency)}]
    if q.fee_minor:
        items.append({"name": "Studio fee", "quantity": "1", "category": "DIGITAL_GOODS",
                      "description": f"{q.fee_pct.normalize():f} % of the ad budget",
                      "unit_amount": _money(q.fee_minor, q.currency)})
    return {
        "intent": INTENT,
        "purchase_units": [{
            "reference_id": reference_id,
            "custom_id": content_sha256,
            "invoice_id": invoice_id,
            "description": "Ad budget and studio fee (Signed Studio)",
            "soft_descriptor": SOFT_DESCRIPTOR,
            "amount": {**_money(q.total_minor, q.currency),
                       "breakdown": {"item_total": _money(q.total_minor, q.currency)}},
            "items": items,
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": BRAND, "shipping_preference": "NO_SHIPPING",
            "user_action": "CONTINUE"}}},
    }


def vault_order_body(q: Quote, *, content_sha256: str, invoice_id: str, reference_id: str,
                     vault_id: str) -> dict[str, Any]:
    """The order for a customer-not-present charge of the owner's saved PayPal (Vault v3):
    the same purchase unit as ``order_body`` (``custom_id`` = the ad's hash, items "Ad
    budget" and "Studio fee", ``intent: AUTHORIZE``), paid from ``vault_id``. No buyer is
    present: PayPal holds the money at once, or answers that it cannot."""
    body = order_body(q, content_sha256=content_sha256, invoice_id=invoice_id,
                      reference_id=reference_id)
    body["payment_source"] = {"paypal": {"vault_id": path_id(vault_id)}}
    return body


def charge_vault(client: PayPalClient, body: dict[str, Any], *, request_id: str) -> Result:
    """Create the vault order and return its authorization (the money held). PayPal
    usually completes an ``AUTHORIZE`` order paid from a vault token at creation; when it
    answers ``APPROVED`` instead, the order is authorized under a request id derived from
    this one. Any other status (the buyer would have to act) is an error: no buyer is
    present to act."""
    reply = client.post("/v2/checkout/orders", body, request_id=request_id)
    order = _order(reply)
    if order.status == "APPROVED":
        reply = client.post(f"/v2/checkout/orders/{path_id(order.id)}/authorize", None,
                            request_id=request_id[:-6] + "-auth0")
        order = _order(reply)
    if order.status != "COMPLETED":
        raise PayPalError(422, "UNPROCESSABLE_ENTITY", "PAYER_ACTION_REQUIRED")
    auths = (_unit(reply.json).get("payments") or {}).get("authorizations")
    if not isinstance(auths, list) or len(auths) != 1 or not isinstance(auths[0], dict):
        raise _bad("not one authorization")
    auth = _payment("authorization", auths[0], reply.body_sha256)
    return Result(**{**auth.__dict__, "custom_id": auth.custom_id or order.custom_id,
                     "invoice_id": auth.invoice_id or order.invoice_id,
                     "order_id": order.id})


def _bad(what: str) -> PayPalError:
    """PayPal answered with success but the body is not what was expected: the step may
    well have happened, so the outcome is ``unknown`` (never a refusal)."""
    return PayPalError(200, "UNEXPECTED_RESPONSE", what.upper().replace(" ", "_"),
                       unknown=True)


def _str(data: Any, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise _bad(f"no {key}")
    return value


def _amount(data: Any) -> tuple[int, str]:
    amount = data.get("amount") if isinstance(data, dict) else None
    if not isinstance(amount, dict):
        raise _bad("no amount")
    currency = _str(amount, "currency_code")
    try:
        return parse_amount(amount.get("value"), currency), currency
    except FundingError:
        raise _bad("bad amount") from None


def _unit(order: dict[str, Any]) -> dict[str, Any]:
    units = order.get("purchase_units")
    if not isinstance(units, list) or len(units) != 1 or not isinstance(units[0], dict):
        raise _bad("not one purchase unit")
    return units[0]


def _link(obj: dict[str, Any], rel: str) -> str:
    for link in obj.get("links") or []:
        if isinstance(link, dict) and link.get("rel") in (rel, "payer-action"):
            href = link.get("href")
            if isinstance(href, str) and href.startswith("https://"):
                return href
    return ""


def _order(reply: Reply) -> Result:
    data = reply.json
    unit = _unit(data)
    minor, currency = _amount(unit)
    auths = (unit.get("payments") or {}).get("authorizations") or []
    auth_id = auths[0].get("id", "") if auths and isinstance(auths[0], dict) else ""
    return Result("order", path_id(_str(data, "id")), _str(data, "status"), minor, currency,
                  reply.body_sha256, custom_id=str(unit.get("custom_id") or ""),
                  invoice_id=str(unit.get("invoice_id") or ""),
                  approve_url=_link(data, "approve"),
                  authorization_id=auth_id if isinstance(auth_id, str) else "")


def _payment(kind: str, data: dict[str, Any], body_sha256: str) -> Result:
    minor, currency = _amount(data)
    return Result(kind, path_id(_str(data, "id")), _str(data, "status"), minor, currency,
                  body_sha256, custom_id=str(data.get("custom_id") or ""),
                  invoice_id=str(data.get("invoice_id") or ""),
                  expiration_time=str(data.get("expiration_time") or ""))


def create(client: PayPalClient, body: dict[str, Any], *, request_id: str) -> Result:
    return _order(client.post("/v2/checkout/orders", body, request_id=request_id))


def get(client: PayPalClient, order_id: str) -> Result:
    return _order(client.get(f"/v2/checkout/orders/{path_id(order_id)}"))


def authorize(client: PayPalClient, order_id: str, *, request_id: str) -> Result:
    """Hold the approved order's money; the result is the new authorization."""
    reply = client.post(f"/v2/checkout/orders/{path_id(order_id)}/authorize", None,
                        request_id=request_id)
    order = _order(reply)
    auths = (_unit(reply.json).get("payments") or {}).get("authorizations")
    if not isinstance(auths, list) or len(auths) != 1 or not isinstance(auths[0], dict):
        raise _bad("not one authorization")
    auth = _payment("authorization", auths[0], reply.body_sha256)
    return Result(**{**auth.__dict__, "custom_id": auth.custom_id or order.custom_id,
                     "invoice_id": auth.invoice_id or order.invoice_id})


def capture(client: PayPalClient, authorization_id: str, *, amount_minor: int, currency: str,
            invoice_id: str, request_id: str, mock_response: str | None = None) -> Result:
    """Take ``amount_minor`` of the authorization, as its final capture.
    ``mock_response``: a sandbox-simulated refusal (``client.MOCK_CODES``)."""
    body = {"amount": _money(amount_minor, currency), "invoice_id": invoice_id,
            "final_capture": True}
    reply = client.post(f"/v2/payments/authorizations/{path_id(authorization_id)}/capture",
                        body, request_id=request_id, mock_response=mock_response)
    return _payment("capture", reply.json, reply.body_sha256)


def reauthorize(client: PayPalClient, authorization_id: str, *, amount_minor: int,
                currency: str, request_id: str) -> Result:
    reply = client.post(
        f"/v2/payments/authorizations/{path_id(authorization_id)}/reauthorize",
        {"amount": _money(amount_minor, currency)}, request_id=request_id)
    return _payment("authorization", reply.json, reply.body_sha256)


def void(client: PayPalClient, authorization_id: str, *, request_id: str) -> Result:
    """Release the hold. PayPal answers 200 with the voided authorization, or 204."""
    reply = client.post(f"/v2/payments/authorizations/{path_id(authorization_id)}/void",
                        None, request_id=request_id)
    if not reply.json:
        return Result("authorization", authorization_id, "VOIDED", 0, "", reply.body_sha256)
    return _payment("authorization", reply.json, reply.body_sha256)


def refund(client: PayPalClient, capture_id: str, *, amount_minor: int, currency: str,
           invoice_id: str, request_id: str) -> Result:
    body = {"amount": _money(amount_minor, currency), "invoice_id": invoice_id}
    reply = client.post(f"/v2/payments/captures/{path_id(capture_id)}/refund", body,
                        request_id=request_id)
    return _payment("refund", reply.json, reply.body_sha256)
