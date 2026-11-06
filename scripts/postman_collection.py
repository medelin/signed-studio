"""Build the Postman v2.1 collection for Signed Studio.

    .venv/bin/python scripts/postman_collection.py          # writes the committed file
    .venv/bin/python scripts/postman_collection.py --check  # exit 1 if it is out of date

Two folders: the assistant API (one request per operation of ``/api/v1/openapi.json``,
read from the app built in memory) and the PayPal sandbox calls the studio itself makes.
Variables only, never values. The output is deterministic: sorted keys, ids derived from
names with uuid5, no timestamps."""

import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "postman" / "signed-studio.postman_collection.json"
SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
NS = uuid.UUID("5d1c6a38-6c1e-4b6c-9a53-0a5c0d4f5e11")
SANDBOX = "https://api-m.sandbox.paypal.com"  # == studio.paypal.SANDBOX_BASE; never a live URL
PREFIX = "/api/v1"
API_FOLDER = "Signed Studio API for assistants"
PAYPAL_FOLDER = "PayPal sandbox (what the studio calls)"


def uid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def openapi() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from studio.settings import Settings

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings.load({
            "STUDIO_DATA_DIR": tmp, "JUDGE_PASSWORD": "Pm-Build-0nly-" + "Zq7" * 6,
            "SESSION_SECRET": "postman-build-only-" + "y" * 24, "STUDIO_REPLAY": "1",
            "STUDIO_REPLAY_SET": "brief", "COOKIE_SECURE": "1"}))
        with TestClient(app, base_url="https://testserver") as c:
            return c.get(PREFIX + "/openapi.json").json()


def resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict) and "$ref" in node:
        cur: Any = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        return resolve(spec, cur, depth)
    return node


def example(spec: dict[str, Any], schema: Any, depth: int = 0) -> Any:
    schema = resolve(spec, schema)
    if not isinstance(schema, dict) or depth > 4:
        return None
    if "example" in schema:
        return schema["example"]
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            opts = [o for o in schema[key] if resolve(spec, o).get("type") != "null"]
            return example(spec, opts[0], depth + 1) if opts else None
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = schema.get("properties", {})
        req = schema.get("required", [])
        return {k: example(spec, props[k], depth + 1) for k in sorted(req) if k in props}
    if kind == "array":
        return []
    if kind == "string":
        return schema.get("enum", [""])[0]
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


def api_item(spec: dict[str, Any], path: str, method: str, op: dict[str, Any]) -> dict[str, Any]:
    name = op.get("summary") or f"{method.upper()} {path}"
    segs = [s for s in path.strip("/").split("/")]
    pvars = [{"key": p["name"], "value": "", "description": p.get("description", "")}
             for p in op.get("parameters", []) if p["in"] == "path"]
    query = [{"key": p["name"], "value": "", "disabled": True,
              "description": p.get("description", "")}
             for p in op.get("parameters", []) if p["in"] == "query"]
    pseg = ["{{base_url}}", *PREFIX.strip("/").split("/"),
            *[":" + s[1:-1] if s.startswith("{") else s for s in segs]]
    raw = "/".join(pseg)
    if query:
        raw += "?" + "&".join(f"{q['key']}=" for q in query)
    headers: list[dict[str, str]] = []
    request: dict[str, Any] = {
        "method": method.upper(), "header": headers,
        "url": {"raw": raw, "host": ["{{base_url}}"], "path": pseg[1:], "query": query,
                "variable": pvars},
        "description": op.get("description", "")}
    body = op.get("requestBody", {}).get("content", {})
    if "application/json" in body:
        headers.append({"key": "Content-Type", "value": "application/json"})
        ex = example(spec, body["application/json"].get("schema", {}))
        request["body"] = {"mode": "raw", "raw": json.dumps(ex or {}, indent=2, sort_keys=True),
                           "options": {"raw": {"language": "json"}}}
    elif body:
        ctype = sorted(body)[0]
        headers.append({"key": "Content-Type", "value": ctype})
        request["body"] = {"mode": "file"}
    return {"id": uid("api", method, path), "name": name, "request": request, "response": [],
            "_op": f"{method.upper()} {path}"}


def pp(name: str, method: str, path: str, *, body: Any = None, auth: str = "bearer",
       headers: dict[str, str] | None = None, query: dict[str, str] | None = None,
       form: dict[str, str] | None = None, desc: str = "") -> dict[str, Any]:
    raw = "{{paypal_base}}" + path
    if query:
        raw += "?" + "&".join(f"{k}={v}" for k, v in sorted(query.items()))
    hdr = [{"key": k, "value": v} for k, v in sorted((headers or {}).items())]
    if name == "Capture authorization":
        hdr.append({"key": "PayPal-Mock-Response", "disabled": True,
                    "value": '{"mock_application_codes":"TRANSACTION_REFUSED"}'})
    req: dict[str, Any] = {
        "method": method, "header": hdr, "description": desc,
        "url": {"raw": raw, "host": ["{{paypal_base}}"],
                "path": [s for s in path.split("/") if s],
                "query": [{"key": k, "value": v} for k, v in sorted((query or {}).items())]}}
    if auth == "bearer":
        req["auth"] = {"type": "bearer", "bearer": [
            {"key": "token", "value": "{{paypal_access_token}}", "type": "string"}]}
    elif auth == "basic":
        req["auth"] = {"type": "basic", "basic": [
            {"key": "username", "value": "{{paypal_client_id}}", "type": "string"},
            {"key": "password", "value": "{{paypal_client_secret}}", "type": "string"}]}
    if form is not None:
        req["body"] = {"mode": "urlencoded",
                       "urlencoded": [{"key": k, "value": v} for k, v in sorted(form.items())]}
    elif body is not None:
        hdr.append({"key": "Content-Type", "value": "application/json"})
        req["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2, sort_keys=True),
                       "options": {"raw": {"language": "json"}}}
    item: dict[str, Any] = {"id": uid("paypal", name), "name": name, "request": req,
                            "response": []}
    if name == "OAuth token":
        item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": [
            "pm.collectionVariables.set('paypal_access_token', pm.response.json().access_token);"
        ]}}]
    return item


def paypal_items() -> list[dict[str, Any]]:
    money = {"currency_code": "EUR", "value": "10.00"}
    rid = {"PayPal-Request-Id": "{{$guid}}"}
    return [
        pp("OAuth token", "POST", "/v1/oauth2/token", auth="basic",
           form={"grant_type": "client_credentials"},
           desc="Sandbox client credentials -> access token (saved to paypal_access_token)."),
        pp("Create order (AUTHORIZE)", "POST", "/v2/checkout/orders", headers=rid, body={
            "intent": "AUTHORIZE", "purchase_units": [{
                "reference_id": "default", "custom_id": "{{content_sha256}}",
                "invoice_id": "{{invoice_id}}", "amount": {**money, "breakdown": {
                    "item_total": money}}}],
            "payment_source": {"paypal": {"experience_context": {
                "shipping_preference": "NO_SHIPPING", "user_action": "CONTINUE"}}}},
           desc="A person approves it as the sandbox buyer; nothing moves before that."),
        pp("Authorize order", "POST", "/v2/checkout/orders/{{order_id}}/authorize",
           headers=rid),
        pp("Capture authorization", "POST",
           "/v2/payments/authorizations/{{authorization_id}}/capture", headers=rid,
           body={"amount": money, "final_capture": True, "invoice_id": "{{invoice_id}}"},
           desc="Enable the PayPal-Mock-Response header to simulate a decline."),
        pp("Void authorization", "POST", "/v2/payments/authorizations/{{authorization_id}}/void",
           headers=rid),
        pp("Refund capture", "POST", "/v2/payments/captures/{{capture_id}}/refund",
           headers=rid, body={"amount": money, "invoice_id": "{{invoice_id}}"}),
        pp("Vault setup token", "POST", "/v3/vault/setup-tokens", headers=rid, body={
            "payment_source": {"paypal": {"usage_type": "MERCHANT", "usage_pattern":
                                          "IMMEDIATE", "experience_context": {
                                              "return_url": "{{base_url}}/vault/return",
                                              "cancel_url": "{{base_url}}/vault/cancel"}}}}),
        pp("Transaction Search", "GET", "/v1/reporting/transactions", query={
            "end_date": "2026-01-02T00:00:00-0000", "fields": "transaction_info",
            "start_date": "2026-01-01T00:00:00-0000"},
           desc="Lags up to ~3 h behind the money movement."),
    ]


def build() -> dict[str, Any]:
    spec = openapi()
    ops = [api_item(spec, p, m, op) for p in sorted(spec["paths"])
           for m, op in sorted(spec["paths"][p].items())]
    return {
        "info": {"_postman_id": uid("collection"), "name": "Signed Studio",
                 "description": "The assistant API (/api/v1) and the PayPal sandbox calls "
                                "Signed Studio makes. Variables only; set them in an environment.",
                 "schema": SCHEMA},
        "auth": {"type": "bearer", "bearer": [
            {"key": "token", "value": "{{token}}", "type": "string"}]},
        "item": [
            {"id": uid("folder", "api"), "name": API_FOLDER, "item": ops},
            {"id": uid("folder", "paypal"), "name": PAYPAL_FOLDER, "item": paypal_items(),
             "auth": {"type": "noauth"}}],
        "variable": [
            {"key": "base_url", "value": "https://pay.udun.eu"},
            {"key": "token", "value": ""},
            {"key": "paypal_base", "value": SANDBOX},
            {"key": "paypal_client_id", "value": ""},
            {"key": "paypal_client_secret", "value": ""},
            {"key": "paypal_access_token", "value": ""},
            {"key": "order_id", "value": ""}, {"key": "content_sha256", "value": ""},
            {"key": "invoice_id", "value": ""}, {"key": "authorization_id", "value": ""},
            {"key": "capture_id", "value": ""}]}


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT}")
