"""The account export of the PayPal edition: mandates, saved PayPal (reference, SHA-256,
status, dates), fundings and mandate charges, and never a PayPal token id or setup token id."""

import json
from decimal import Decimal

from studio import funding, mandates
from studio.paypal import vault
from tests.test_account_pages import app, client, login  # noqa: F401 - fixtures

TOKEN_ID = "9XQ12345TOKENIDSECRET"
SETUP_TOKEN_ID = "5CZ67890SETUPTOKENSECRET"


def _money_rows(app, account_id):  # noqa: F811
    """One mandate, one saved PayPal and one mandate charge, by SQL."""
    conn = app.state.conn
    mandates.ensure_schema(conn)
    vault.ensure_schema(conn)
    funding.ensure_schema(conn)
    body = {"valid_until": 4_000_000_000, "max_campaigns": 3, "token_ids": []}
    with conn:
        conn.execute("INSERT INTO mandates (id, account_id, body, sha256, approval_id, "
                     "created_ts) VALUES ('mdt_1', ?, ?, ?, 'apr_1', 1.0)",
                     (account_id, json.dumps(body), "a" * 64))
        conn.execute("INSERT INTO paypal_vault (id, account_id, setup_token_id, token_id, "
                     "token_sha256, status, created_ts, updated_ts) "
                     "VALUES ('vlt_1', ?, ?, ?, ?, 'active', 2.0, 3.0)",
                     (account_id, SETUP_TOKEN_ID, TOKEN_ID, "b" * 64))
        conn.execute("INSERT INTO mandate_charges (approval_id, mandate_id, period, "
                     "amount_minor, currency, vault_sha256, status, funding_id, created_ts, "
                     "updated_ts) VALUES ('apr_2', 'mdt_1', 1, 500, 'EUR', ?, 'done', "
                     "'fund_1', 4.0, 5.0)", ("b" * 64,))
    return conn


def test_export_carries_mandates_saved_paypal_fundings_and_charges_but_no_token_id(
        app, client, monkeypatch):  # noqa: F811
    login(client)
    account = app.state.accounts.by_username("ola")
    conn = _money_rows(app, account.id)
    f = funding.create(conn, run_id="run_1", item_id="itm_1", content_sha256="c" * 64,
                       q=funding.quote("EUR", 1000, 5, Decimal("10")), now=6.0)
    monkeypatch.setattr("studio.paypal.agent_tools.owned_funding_ids", lambda c, a: [f.id])
    r = client.get("/account/export.json")
    assert r.status_code == 200
    data = json.loads(r.text)
    assert [m["mandate_id"] for m in data["mandates"]] == ["mdt_1"]
    [saved] = data["saved_paypal"]
    assert saved["vault_ref"] == "vlt_1" and saved["token_sha256"] == "b" * 64
    assert saved["status"] == "active" and saved["created_ts"] == 2.0
    assert [x["funding_id"] for x in data["fundings"]] == [f.id]
    assert data["fundings"][0]["state"] == "created"
    assert [c["approval_id"] for c in data["mandate_charges"]] == ["apr_2"]
    assert TOKEN_ID not in r.text and SETUP_TOKEN_ID not in r.text


def test_export_without_the_paypal_stores_is_unchanged(app):  # noqa: F811
    a = app.state.accounts.by_username("ola")
    data = app.state.accounts.export(a.id, [])
    assert not {"mandates", "saved_paypal", "fundings", "mandate_charges"} & set(data)


def test_export_keeps_only_known_keys_of_saved_paypal(app):  # noqa: F811
    a = app.state.accounts.by_username("ola")
    data = app.state.accounts.export(a.id, [], saved_paypal=[
        {"vault_ref": "vlt_1", "status": "active", "token_sha256": "b" * 64,
         "created_ts": 1.0, "token_id": TOKEN_ID, "setup_token_id": SETUP_TOKEN_ID}])
    dumped = json.dumps(data)
    assert TOKEN_ID not in dumped and SETUP_TOKEN_ID not in dumped
    assert data["saved_paypal"][0]["vault_ref"] == "vlt_1"
