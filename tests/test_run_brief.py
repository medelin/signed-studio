"""``run_brief`` (MCP tool and ``POST /api/v1/briefs/{id}/run``): the assistant runs a
confirmed brief and gets back how its campaign is paid: the receipt of a charge to the
owner's saved PayPal within a mandate the owner signed, or the pay-and-sign link."""

from __future__ import annotations

from tests.test_assistants import BASE, bearer, token
from tests.test_paypal_vault import CHARGE, Studio


def _confirmed_brief(s):
    from studio import demo, replay

    meta = replay.load_set("brief")
    h = bearer(s.value)
    d = s.c.post("/api/v1/briefs", json={"text": meta["draft_text"], "lang": "pl"},
                 headers=h).json()
    res = demo.resolutions(d["brief"]["open_questions"], meta["answers"])
    r = s.c.post(f"/api/v1/briefs/{d['brief_id']}/confirm", json={"resolutions": res},
                 headers=h)
    assert r.status_code == 200, r.text
    return d["brief_id"]


def test_run_brief_over_https_returns_the_mandate_charge_receipt(tmp_path):
    s = Studio(tmp_path)
    s.grant()
    brief_id = _confirmed_brief(s)
    r = s.c.post(f"/api/v1/briefs/{brief_id}/run", json={"wait_s": 100},
                 headers=bearer(s.value))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["outcome"] == "mandate_charge", out
    rec = out["payment"]["receipt"]
    assert rec["amount_minor"] == CHARGE and rec["state"] == "authorized"
    assert any(x.id == rec["receipt_id"] for x in s.ledger.receipts(out["run_id"]))


def test_run_brief_over_mcp_returns_the_pay_and_sign_link_without_a_mandate(tmp_path):
    from studio import assistants

    s = Studio(tmp_path)
    brief_id = _confirmed_brief(s)
    principal = assistants.Principal(s.app.state.assistants.tokens.verify(s.value), "mcp",
                                     BASE)
    status, out = s.app.state.assistants.call(principal, "run_brief",
                                              {"brief_id": brief_id, "wait_s": 100})
    assert status == 200, out
    assert out["outcome"] == "pay_and_sign", out
    assert out["payment"]["pay_and_sign_url"].startswith(BASE + "/studio")
    assert out["payment"]["mandate_reasons"] == ["no_mandate"]
    assert s.orders_made() == []


def test_run_brief_needs_a_draft_token(tmp_path):
    s = Studio(tmp_path, save=False)
    _, read_value = token(s.app, scope="read", label="reader")
    r = s.c.post("/api/v1/briefs/b_0000000000000000/run", json={},
                 headers=bearer(read_value))
    assert r.status_code == 403


