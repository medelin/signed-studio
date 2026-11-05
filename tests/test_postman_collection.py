"""The Postman collection: v2.1, reproducible from the code, one request per assistant-API
operation, and variables only (never a token or a PayPal credential)."""

import json
import re
from pathlib import Path

from scripts import postman_collection as pc

COMMITTED = (Path(__file__).resolve().parent.parent / "docs" / "postman"
             / "signed-studio.postman_collection.json")


def walk(items):
    for it in items:
        if "item" in it:
            yield from walk(it["item"])
        else:
            yield it


def test_collection_is_v21_and_up_to_date():
    built = pc.render()
    doc = json.loads(built)
    assert doc["info"]["schema"] == (
        "https://schema.getpostman.com/json/collection/v2.1.0/collection.json")
    assert COMMITTED.read_text() == built, "run `make postman`"
    assert pc.render() == built  # deterministic


def test_every_api_op_has_a_request():
    spec = pc.openapi()
    want = sorted(f"{m.upper()} {p}" for p, ops in spec["paths"].items() for m in ops)
    assert any(w == "GET /paypal/transactions" for w in want)
    assert any(w.startswith("POST /paypal/tools/") for w in want)
    assert any(w.endswith("/refund-proposal") for w in want)
    doc = json.loads(pc.render())
    api = next(f for f in doc["item"] if f["name"].startswith("Signed Studio"))
    got = sorted(it["_op"] for it in walk(api["item"]))
    assert got == want
    paypal = next(f for f in doc["item"] if f["name"].startswith("PayPal sandbox"))
    urls = " ".join(it["request"]["url"]["raw"] for it in walk(paypal["item"]))
    for part in ("/v1/oauth2/token", "/v2/checkout/orders", "/authorize", "/capture",
                 "/void", "/refund", "/v3/vault/setup-tokens", "/v1/reporting/transactions"):
        assert part in urls
    assert all(u["request"]["url"]["raw"].startswith("{{paypal_base}}")
               for u in walk(paypal["item"]))


def test_no_secret_values():
    text = COMMITTED.read_text()
    assert "msl_tok_" not in text
    for m in re.finditer(r"Bearer\s+(\S+)", text):
        assert re.fullmatch(r"\{\{\w+\}\}", m.group(1).rstrip('"\\,')), m.group(0)
    doc = json.loads(text)
    assert {v["key"] for v in doc["variable"]} >= {"base_url", "token", "paypal_base"}
    for v in doc["variable"]:
        if v["key"] != "paypal_base" and v["key"] != "base_url":
            assert v["value"] == ""
    assert next(v for v in doc["variable"] if v["key"] == "paypal_base")["value"] == \
        "https://api-m.sandbox.paypal.com"
    assert "api-m.paypal.com" not in text
    for name in ("paypal_client_id", "paypal_client_secret"):
        assert "{{" + name + "}}" in text
