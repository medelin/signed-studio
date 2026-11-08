"""The A2A agent card and ``/agentic``: public, honest, stable, no secrets, CSP unchanged."""

import json
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import routes_agents
from studio.app import CSP, create_app
from studio.assistants import OPS
from studio.settings import Settings
from studio.ui import i18n

SCHEMA = Path(__file__).parent / "schemas" / "agent_card.schema.json"
SECRETISH = re.compile(r"msl_tok_[A-Za-z0-9_]{8,}|BEGIN [A-Z ]*PRIVATE|sk-[A-Za-z0-9]{16,}"
                       r"|[A-Za-z0-9_\-]{40,}")


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings.load({
        "STUDIO_DATA_DIR": str(tmp_path / "v"), "STUDIO_REQUIRE_LOGIN": "1",
        "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
        "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _validate(card, schema, path="card"):
    """The small subset of JSON Schema the file uses (no extra dependency)."""
    if "const" in schema:
        assert card == schema["const"], path
    kind = schema.get("type")
    if kind == "object":
        assert isinstance(card, dict), path
        for k in schema.get("required", []):
            assert k in card, f"{path}.{k} missing"
        for k, sub in schema.get("properties", {}).items():
            if k in card:
                _validate(card[k], sub, f"{path}.{k}")
    elif kind == "array":
        assert isinstance(card, list), path
        assert len(card) >= schema.get("minItems", 0), path
        for i, item in enumerate(card):
            _validate(item, schema.get("items", {}), f"{path}[{i}]")


def test_card_is_public_and_valid(client):
    r = client.get("/.well-known/agent-card.json")  # no session, login required elsewhere
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    card = r.json()
    _validate(card, json.loads(SCHEMA.read_text()))
    assert card["extensions"]["payments"]["pays"] == "only inside a mandate the owner signed"
    assert card["url"].endswith("/mcp")
    assert card["extensions"]["interfaces"]["openapi"].endswith("/api/v1/openapi.json")
    assert "max-age" in r.headers["cache-control"]


def test_card_skills_are_real_tools_with_tags(client):
    card = client.get("/.well-known/agent-card.json").json()
    ids = {s["id"] for s in card["skills"]}
    assert ids == {"draft_brief", "confirm_brief", "run_brief", "paypal_transactions",
                   "propose_refund"}
    assert ids <= set(OPS), "the card promises a tool that does not exist"
    for s in card["skills"]:
        assert "paypal" in s["tags"] or "signed-approval" in s["tags"]
    run = next(s for s in card["skills"] if s["id"] == "run_brief")
    assert "mandate" in run["tags"]
    assert "mandate_charge" in run["description"] and "pay_and_sign" in run["description"]


def test_card_has_no_secret_looking_strings(client):
    text = client.get("/.well-known/agent-card.json").text
    assert not SECRETISH.search(text.replace("https://testserver", ""))


def test_etag_is_the_content_hash_and_stable(client):
    a = client.get("/.well-known/agent-card.json")
    b = client.get("/.well-known/agent-card.json")
    assert a.headers["etag"] == b.headers["etag"] and a.content == b.content
    body, etag = routes_agents.card_bytes("https://testserver")
    assert a.headers["etag"] == etag and a.content == body
    r = client.get("/.well-known/agent-card.json", headers={"If-None-Match": etag})
    assert r.status_code == 304 and not r.content


@pytest.mark.parametrize("path,lang", [("/agentic", "en"), ("/pl/agentic", "pl"),
                                       ("/agentic?lang=pl", "pl")])
def test_agentic_page_renders_without_a_session(client, path, lang):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 200
    assert f'<html lang="{lang}" ' in r.text
    catalog = i18n.CATALOGS[lang]
    assert catalog["agentic.title"] in r.text
    for target in ("/.well-known/agent-card.json", "/mcp", "/api/v1/openapi.json",
                   "/receipts"):
        assert target in r.text
    assert "<script>" not in r.text  # no inline script (CSP)


def test_csp_is_unchanged_on_the_new_routes(client):
    for path in ("/agentic", "/pl/agentic", "/.well-known/agent-card.json"):
        assert client.get(path).headers["content-security-policy"] == CSP
