"""Day 3: the owner's saved PayPal (Vault v3) pays for campaigns under a signed mandate.

Through the app (``PAYPAL_ADAPTER=mock``, replayed models): the owner saves PayPal on
``/account`` (buyer present), signs a mandate whose ``funding`` block names it by SHA-256
with a per-charge cap, a cap per period and the campaign kinds; a run started by the
assistant's token is then paid from the saved PayPal (money held, not taken) and its
campaign created PAUSED, without a new tap. Outside the caps, after revocation or with
the saved PayPal removed, nothing is charged and the run waits for the owner with the
pay-and-sign link. ``studio verify`` accepts the clean log (P-V5).
"""

from __future__ import annotations

import base64
import time

import pytest
from fastapi.testclient import TestClient

from studio import funding, mandates
from studio.approvals import Keys
from studio.paypal.vault import token_sha256
from studio.receipts import (
    PAYPAL_KIND,
    canonical,
    charge_body_problem,
    charge_period,
    mandate_body_problem,
    verify,
)
from tests.forms import csrf
from tests.test_assistants import BASE, account, bearer, draft_confirm_run, login, make_app, token
from tests.test_mandates import limits
from tests.test_ui_studio import register

CHARGE = 22050  # 30.00 PLN a day × 7 days + 5 % fee: what the replayed brief's run costs


def sign(c, key, approver, approval_id):
    data = c.get(f"/api/approvals/{approval_id}/payload",
                 params={"approver": approver}).json()
    sig = key.sign(base64.b64decode(data["message_b64"])).hex()
    return data, c.post(f"/api/approvals/{approval_id}",
                        json={"approver": approver, "signature": sig})


class Studio:
    """One app with PayPal mocked, the owner signed in and a draft token."""

    def __init__(self, tmp, save=True):
        self.app = make_app(tmp, PAYPAL_ADAPTER="mock")
        self.tmp = tmp
        self.tok, self.value = token(self.app)
        self.c = TestClient(self.app, base_url=BASE)
        login(self.c, self.app)
        self.acct = account(self.app)
        self.funder = self.app.state.funder
        self.key, self.approver = register(self.c)
        self.ref = self.save_paypal() if save else ""

    @property
    def conn(self):
        return self.app.state.conn

    @property
    def ledger(self):
        return self.app.state.ledger

    def save_paypal(self):
        r = self.c.post("/account/paypal/vault/start", data={"csrf": csrf(self.c)},
                        follow_redirects=False)
        assert r.status_code == 303, r.text
        r = self.c.get(r.headers["location"], follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(
            "/account?paypal=saved"), r.text
        (saved,) = [v for v in self.funder.vault.of_account(self.acct.id)
                    if v.status == "active"]
        return saved.id

    def grant(self, pay=True, **kw):
        fields = {"csrf": csrf(self.c), "token": [self.tok.id],
                  **limits(max_campaigns="3", **kw)}
        if pay:
            fields.update({"pay": "1", "vault_ref": self.ref, "max_charge": "300",
                           "funding_cap": "500", "period_days": "7", "kind": "link"})
            fields.update({k: v for k, v in kw.items() if k in (
                "max_charge", "funding_cap", "period_days", "kind", "vault_ref")})
        r = self.c.post("/account/mandates/prepare", data=fields, follow_redirects=False)
        assert r.status_code == 303, r.text
        approval_id = r.headers["location"].split("approval=")[1]
        data, r = sign(self.c, self.key, self.approver, approval_id)
        assert r.status_code == 200 and r.json()["executed"] is True, r.text
        return data["content"]["mandate"], r.json()["result"]["mandate_id"]

    def run(self):
        d, started = draft_confirm_run(self.c, bearer(self.value))
        return self.c.get(f"/api/v1/runs/{started['run_id']}",
                          headers=bearer(self.value)).json()

    def orders_made(self):
        return [p for m, p in self.funder.fake.calls if m == "POST"
                and p == "/v2/checkout/orders"]

    def verify(self):
        path = self.tmp / "receipts.jsonl"
        path.write_text(self.ledger.export_jsonl())
        return verify(path, self.ledger.public_key, approver_keys=Keys.registry(self.conn))


@pytest.fixture(scope="module")
def paid(tmp_path_factory):
    s = Studio(tmp_path_factory.mktemp("vault-paid"))
    body, mid = s.grant()
    run = s.run()
    return s, body, mid, run


# -- saving PayPal ---------------------------------------------------------------------


def test_saving_paypal_keeps_a_reference_and_no_payer_data(paid):
    s, *_ = paid
    row = s.conn.execute("SELECT * FROM paypal_vault WHERE id = ?", (s.ref,)).fetchone()
    text = repr(row)
    assert "@" not in text and "MOCKPAYER" not in text and "Sandbox" not in text
    token_id = s.conn.execute("SELECT token_id FROM paypal_vault WHERE id = ?",
                              (s.ref,)).fetchone()[0]
    export = s.ledger.export_jsonl()
    assert token_id not in export  # receipts carry its SHA-256 only
    assert token_sha256(token_id) in export
    assert "sb-buyer@" not in export and "MOCKPAYER" not in export
    steps = [r.details["step"] for r in s.ledger.receipts(s.ref) if r.kind == PAYPAL_KIND]
    assert steps == ["paypal.vault_setup", "paypal.vault_save"]


def test_the_vault_routes_need_a_session_and_csrf(paid):
    s, *_ = paid
    anon = TestClient(s.app, base_url=BASE)
    assert anon.post("/account/paypal/vault/start", data={"csrf": "x"},
                     follow_redirects=False).status_code in (401, 403)
    assert anon.get("/account/paypal/vault/return?vault_ref=" + s.ref,
                    follow_redirects=False).status_code == 401
    r = s.c.post("/account/paypal/vault/start", data={"csrf": "wrong"},
                 follow_redirects=False)
    assert r.status_code == 403


def test_another_account_cannot_finish_or_delete_this_saved_paypal(tmp_path):
    s = Studio(tmp_path)
    other = TestClient(s.app, base_url=BASE)
    login(other, s.app, username="other")
    r = other.get(f"/account/paypal/vault/return?vault_ref={s.ref}", follow_redirects=False)
    assert r.status_code == 404
    r = other.post("/account/paypal/vault/delete",
                   data={"csrf": csrf(other), "vault_ref": s.ref}, follow_redirects=False)
    assert r.status_code == 404
    assert s.funder.vault.get(s.ref).status == "active"


# -- the signed mandate's funding block -----------------------------------------------------


def test_the_mandate_signs_the_saved_paypal_by_hash_and_both_caps(paid):
    s, body, mid, run = paid
    f = body["funding"]
    assert f == {"schema": "mandate-funding/v1", "method": "paypal_vault",
                 "vault_sha256": s.funder.vault.get(s.ref).token_sha256,
                 "max_charge_minor": 30000, "funding_cap_minor": 50000, "period_days": 7,
                 "period_start": f["period_start"], "kinds": ["link"]}
    assert abs(f["period_start"] - time.time()) < 120
    assert mandate_body_problem(body) is None


@pytest.mark.parametrize("change, problem", [
    ({"max_charge_minor": 0}, "max_charge_minor"),
    ({"max_charge_minor": 60000}, "max_charge_minor"),  # above the period cap
    ({"period_days": 31}, "period_days"),
    ({"kinds": ["link", "video"]}, "kinds"),
    ({"kinds": ["message", "link"]}, "kinds"),  # not sorted: not the canonical form
    ({"vault_sha256": "abc"}, "vault"),
    ({"method": "card"}, "funding"),
    ({"extra": 1}, "funding"),
])
def test_a_malformed_funding_block_cannot_be_signed(paid, change, problem):
    s, body, *_ = paid
    bad = {**body, "funding": {**body["funding"], **change}}
    assert mandate_body_problem(bad) == problem


def test_the_mandate_form_refuses_a_saved_paypal_that_is_not_the_accounts(tmp_path):
    s = Studio(tmp_path)
    fields = {"csrf": csrf(s.c), "token": [s.tok.id], **limits(), "pay": "1",
              "vault_ref": "vlt_0000000000000000", "max_charge": "300",
              "funding_cap": "500", "period_days": "7", "kind": "link"}
    r = s.c.post("/account/mandates/prepare", data=fields, follow_redirects=False)
    assert r.status_code == 422


# -- a run inside the mandate is paid from the saved PayPal --------------------------------


def test_a_run_inside_the_mandate_is_paid_from_the_saved_paypal_and_created_paused(paid):
    s, body, mid, run = paid
    m = run["mandate"]
    assert m["fits"] is True and m["executed"] is True
    assert m["result"]["status"] == "PAUSED"
    charge = m["charge"]
    assert charge["state"] == "authorized" and charge["amount_minor"] == CHARGE
    f = funding.get(s.conn, charge["funding_id"])
    assert f.state == "authorized" and f.total_minor == CHARGE and f.captured_minor == 0
    assert m["result"]["funding"]["paid_by"] == "mandate"
    # the payment section for the assistant: the receipt of the charge, not a link
    pay = run["payment"]
    assert pay["outcome"] == "mandate_charge" and pay["campaign_status"] == "PAUSED"
    assert pay["receipt"]["receipt_id"] == charge["receipt_id"]
    (rec,) = [r for r in s.ledger.receipts(run["run_id"]) if r.kind == PAYPAL_KIND
              and r.details.get("step") == "paypal.vault_charge"]
    assert rec.id == charge["receipt_id"] and rec.details["ok"] is True
    assert rec.details["custom_id_sha256"] == f.content_sha256  # PayPal names the ad
    assert rec.details["mandate_id"] == mid
    assert rec.details["vault_sha256"] == body["funding"]["vault_sha256"]


def test_the_approval_receipt_declares_the_charge_and_the_period(paid):
    s, body, mid, run = paid
    (ap,) = [r for r in s.ledger.receipts(run["run_id"]) if r.kind == "approval"
             and r.details.get("by_mandate") == mid]
    c = ap.details["charge"]
    assert charge_body_problem(c) is None
    assert c == {"schema": "charge/v1", "amount_minor": CHARGE, "currency": "PLN",
                 "period": charge_period(body["funding"], ap.ts),
                 "vault_sha256": body["funding"]["vault_sha256"]}
    row = mandates.charge_of(s.conn, ap.details["pending_id"])
    assert row["status"] == "done" and row["amount_minor"] == CHARGE


def test_the_paid_log_verifies_and_activation_still_needs_the_owner(paid):
    s, body, mid, run = paid
    report = s.verify()
    assert report.ok, report.errors
    item = run["mandate"]["result"]["campaign_item_id"]
    r = s.c.post(f"/api/campaigns/{item}/activate")
    assert r.status_code == 201, r.text
    assert r.json()["content"]["paypal"]["amount_minor"] == CHARGE
    data, r = sign(s.c, s.key, s.approver, r.json()["approval_id"])
    assert r.status_code == 200 and r.json()["executed"] is True, r.text
    f = funding.get(s.conn, run["mandate"]["charge"]["funding_id"])
    assert f.state == "captured" and f.captured_minor == CHARGE
    report = s.verify()
    assert report.ok, report.errors


# -- outside the mandate: nothing is charged, the owner gets the pay-and-sign link ---------


def _waits_with(run, reason):
    assert run["status"] == "awaiting_approval", run["status"]
    assert reason in run["mandate"]["reasons"], run["mandate"]
    pay = run["payment"]
    assert pay["outcome"] == "pay_and_sign" and "/studio" in pay["pay_and_sign_url"]
    assert reason in pay["mandate_reasons"]


def test_a_charge_above_the_per_charge_cap_is_refused_before_paypal(tmp_path):
    s = Studio(tmp_path)
    s.grant(max_charge="200")  # 220.50 PLN needed
    _waits_with(s.run(), "max_charge")
    assert s.orders_made() == []


def test_a_mandate_without_funding_pays_nothing_and_the_owner_pays_and_signs(tmp_path):
    s = Studio(tmp_path)
    s.grant(pay=False)
    _waits_with(s.run(), "no_funding")
    assert s.orders_made() == []


def test_a_campaign_kind_the_mandate_does_not_pay_for_is_refused(tmp_path):
    s = Studio(tmp_path)
    s.grant(kind="lead_form")
    _waits_with(s.run(), "kind")


def test_a_removed_saved_paypal_cannot_be_charged(tmp_path):
    s = Studio(tmp_path)
    s.grant()
    r = s.c.post("/account/paypal/vault/delete", data={"csrf": csrf(s.c), "vault_ref": s.ref},
                 follow_redirects=False)
    assert r.status_code == 303
    assert s.funder.vault.get(s.ref).status == "deleted"
    assert s.funder.fake.vault_tokens and set(s.funder.fake.vault_tokens.values()) == {
        "DELETED"}
    _waits_with(s.run(), "vault")
    assert s.orders_made() == []


def test_charges_of_one_period_never_pass_its_cap(tmp_path):
    s = Studio(tmp_path)
    s.grant(funding_cap="400")  # one charge of 220.50 fits, a second does not
    first = s.run()
    assert first["mandate"]["fits"] is True and first["payment"]["outcome"] == "mandate_charge"
    second = s.run()
    _waits_with(second, "funding_cap")
    assert len(s.orders_made()) == 1
    assert s.verify().ok


def test_a_pending_charge_with_an_unknown_outcome_keeps_its_amount_reserved(tmp_path):
    s = Studio(tmp_path)
    body, mid = s.grant(funding_cap="400")
    # PayPal answers 500 to every attempt of the vault order: the outcome is unknown
    s.funder.fake.fail_next(500, 500, 500, 500)
    first = s.run()
    assert first["mandate"]["charge_error"] in ("INTERNAL_SERVER_ERROR", "PayPalError")
    assert first["status"] == "awaiting_approval"  # reopened for the owner
    assert first["payment"]["outcome"] == "pay_and_sign"
    (row,) = s.conn.execute("SELECT status, amount_minor FROM mandate_charges").fetchall()
    assert row == ("pending", CHARGE)  # still reserved: PayPal may have acted
    _waits_with(s.run(), "funding_cap")


def test_a_definite_refusal_reopens_the_run_for_the_owner_and_frees_the_amount(tmp_path):
    s = Studio(tmp_path)
    s.grant()
    s.funder.fake.vault_tokens.update({k: "DELETED" for k in s.funder.fake.vault_tokens})
    run = s.run()  # the studio still thinks it is saved; PayPal refuses the token
    assert run["mandate"]["charge_error"]
    assert run["status"] == "awaiting_approval"
    assert run["mandate"]["reopened_approval_id"] == run["payment"]["approval_id"]
    (row,) = s.conn.execute("SELECT status FROM mandate_charges").fetchall()
    assert row == ("refused",)
    assert s.conn.execute("SELECT count(*) FROM campaigns WHERE run_id = ?",
                          (run["run_id"],)).fetchone()[0] == 0
    assert s.verify().ok


def test_revocation_while_paypal_answers_voids_the_hold_and_creates_nothing(tmp_path):
    s = Studio(tmp_path)
    body, mid = s.grant()
    real = s.funder.money.charge_vault

    def revoke_meanwhile(*args, **kw):
        step = real(*args, **kw)
        assert mandates.revoke(s.conn, s.ledger, s.acct, mid)
        return step

    s.funder.money.charge_vault = revoke_meanwhile
    run = s.run()
    assert run["mandate"]["charge_error"] == "mandate_revoked"
    (fid,) = s.conn.execute("SELECT funding_id FROM mandate_charges").fetchone()
    assert funding.get(s.conn, fid).state == "voided"
    assert s.conn.execute("SELECT count(*) FROM campaigns WHERE run_id = ?",
                          (run["run_id"],)).fetchone()[0] == 0
    report = s.verify()
    assert report.ok, report.errors


def test_a_campaign_not_created_after_the_charge_has_its_hold_voided(tmp_path):
    s = Studio(tmp_path)
    s.grant()

    def broken(verified, payload):
        raise RuntimeError("meta down")

    s.app.state.executors["campaign.create"] = broken
    run = s.run()
    assert run["mandate"]["execution_error"] == "RuntimeError"
    (fid,) = s.conn.execute("SELECT funding_id FROM mandate_charges").fetchone()
    assert funding.get(s.conn, fid).state == "voided"
    assert s.verify().ok


# -- the buyer path stays apart from the mandate path ---------------------------------------


def test_a_vault_charge_cannot_stand_in_for_a_buyers_signed_order(paid):
    from studio.approvals import Verified
    from studio.paypal.hooks import FundingRefused

    s, body, mid, run = paid
    f = funding.get(s.conn, run["mandate"]["charge"]["funding_id"])
    block = {"schema": "funding/v1", "item_id": f.item_id, "content_sha256": f.content_sha256,
             "order_id": f.order_id, "intent": "AUTHORIZE", "currency": f.currency,
             "amount_minor": f.total_minor, "budget_minor": f.budget_minor,
             "fee_minor": f.fee_minor, "payee": ""}
    v = Verified(pending_id="0" * 32, run_id=f.run_id, action="campaign.create",
                 item_id=f.item_id, content_sha256="0" * 64, approver="ed25519:x",
                 signature="", funding_sha256=funding.funding_sha256(block))
    with pytest.raises(FundingRefused):
        s.funder.for_create(v, {"funding": block})
    assert canonical(block)  # the block is well-formed; only its origin is wrong


# -- the Vault module against the fake PayPal (no app) -----------------------------------------


def _vault(tmp_path):
    from studio import db
    from studio.paypal.client import PayPalClient
    from studio.paypal.mock import FakePayPal
    from studio.paypal.vault import Vault

    conn = db.connect(tmp_path / "v.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    fake = FakePayPal()
    client = PayPalClient("mock-client", "mock-secret", transport=fake.transport(),
                          sleep=lambda s: None)
    return Vault(conn, ledger, client), fake


def test_finishing_before_the_buyer_approved_saves_nothing(tmp_path):
    from studio.paypal.vault import VaultError

    v, fake = _vault(tmp_path)
    ref, url = v.start("acct_0123456789abcdef", return_url="https://x.example/r",
                       cancel_url="https://x.example/c")
    assert url.startswith("https://www.sandbox.paypal.com/")
    with pytest.raises(VaultError) as exc:
        v.finish("acct_0123456789abcdef", ref)
    assert exc.value.code == "not_approved_yet" and v.get(ref).status == "setup"
    with pytest.raises(VaultError):  # another account cannot finish it
        v.finish("acct_fedcba9876543210", ref)
    fake.approve_setup(v.setup_token_of("acct_0123456789abcdef", ref))
    with pytest.raises(VaultError):  # PayPal passing back another setup token
        v.finish("acct_0123456789abcdef", ref, "7OTHERSETUPTOKEN1")
    saved = v.finish("acct_0123456789abcdef", ref)
    assert saved.status == "active" and v.finish("acct_0123456789abcdef", ref) == saved
    assert v.active("acct_0123456789abcdef", saved.token_sha256) is not None
    assert v.active("acct_fedcba9876543210", saved.token_sha256) is None


def test_deleting_stops_charges_even_when_paypal_is_down(tmp_path):
    v, fake = _vault(tmp_path)
    ref, _ = v.start("acct_0123456789abcdef", return_url="https://x.example/r",
                     cancel_url="https://x.example/c")
    fake.approve_setup(v.setup_token_of("acct_0123456789abcdef", ref))
    saved = v.finish("acct_0123456789abcdef", ref)
    fake.fail_next(400)  # PayPal refuses the DELETE
    assert v.delete("acct_0123456789abcdef", ref).status == "deleted"
    assert v.active("acct_0123456789abcdef", saved.token_sha256) is None  # local first


def test_the_recorded_vault_cassette_parses():
    """fixtures/cassettes/paypal/sandbox_vault.json (scripts/paypal_record_vault.py): a
    setup token the sandbox buyer approved, the vault token, a charge with no buyer
    present, its void and the token's deletion."""
    import json as _json
    from pathlib import Path

    import httpx

    from studio.paypal import orders, vault
    from studio.paypal.client import PayPalClient

    path = Path(__file__).resolve().parents[1] / "fixtures/cassettes/paypal/sandbox_vault.json"
    if not path.exists():
        pytest.skip("not recorded yet: needs the sandbox buyer's approval")
    data = _json.loads(path.read_text())
    assert data["_provenance"] == "recorded"
    text = _json.dumps(data)
    assert '"payer"' not in text and "access_token" not in text and "@" not in text.replace(
        "sb-buyer@personal.example.com", "").replace("sb-business@business.example.com", "")
    script = list(data["interactions"])

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        i = script.pop(0)
        assert (request.method, request.url.path) == (i["request"]["method"],
                                                      i["request"]["path"])
        return httpx.Response(i["response"]["status"], json=i["response"].get("json"))

    c = PayPalClient("id", "secret", transport=httpx.MockTransport(handler))
    first = data["interactions"][0]
    sid, status, approve, _ = vault.create_setup_token(c, first["request"]["json"],
                                                       request_id="ss-1")
    assert status == "PAYER_ACTION_REQUIRED" and approve
    assert vault.get_setup_token(c, sid)[0] == "APPROVED"
    token, _ = vault.create_payment_token(c, sid, request_id="ss-2")
    assert token == "VAULT-ID"
    charge = next(i for i in data["interactions"] if i["request"]["path"] ==
                  "/v2/checkout/orders")
    held = orders.charge_vault(c, charge["request"]["json"], request_id="ss-3" + "0" * 48)
    assert held.kind == "authorization" and held.status == "CREATED" and held.order_id
    assert held.custom_id == charge["request"]["json"]["purchase_units"][0]["custom_id"]
