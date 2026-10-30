"""Signed mandates (``studio.mandates``): the owner signs limits once; within them a run
started by a listed token creates its campaign PAUSED without a new tap, and ``studio
verify`` proves it. Outside them, and for every other action, nothing changes: the run
waits for the owner's signature. Models are the recorded replay set, the adapter is the
mock."""

import base64
import json
import time
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from studio import cli, db, executor, mandates
from studio.accounts import Accounts
from studio.agents import media_buyer
from studio.approvals import Approval, Keys, Pending, Verified, verify_and_consume
from studio.meta import objectives
from studio.meta.adapter import MOCK_PAGE
from studio.receipts import (
    Receipt,
    _manifest_bytes,
    canonical,
    key_id,
    public_key_raw,
    sha256_hex,
    verify,
)
from tests.forms import csrf
from tests.test_assistants import (
    BASE,
    OTHER_PASSWORD,
    account,
    bearer,
    draft_confirm_run,
    login,
    make_app,
    token,
)
from tests.test_ui_studio import register

DAY = 86400


def limits(**kw):
    out = {"objective": ["traffic"], "countries": "PL", "hosts": "example.com",
           "max_daily_budget": "50", "max_campaigns": "1", "days": "7"}
    out.update(kw)
    return out


def sign(c, key, approver, approval_id):
    data = c.get(f"/api/approvals/{approval_id}/payload",
                 params={"approver": approver}).json()
    sig = key.sign(base64.b64decode(data["message_b64"])).hex()
    return data, c.post(f"/api/approvals/{approval_id}",
                        json={"approver": approver, "signature": sig})


def prepare(c, tok_ids, **kw):
    return c.post("/account/mandates/prepare", follow_redirects=False,
                  data={"csrf": csrf(c), "token": tok_ids, **limits(**kw)})


def grant_http(c, tok_ids, **kw):
    """The owner's whole path: the /account form, then the studio's signing screen."""
    r = prepare(c, tok_ids, **kw)
    assert r.status_code == 303, r.text
    approval_id = r.headers["location"].split("approval=")[1]
    key, approver = register(c)
    data, r = sign(c, key, approver, approval_id)
    assert r.status_code == 200, r.text
    assert r.json()["executed"] is True and r.json()["result"]["active"] is True
    return data, r.json(), key, approver


def body_for(app, acct, tok_ids, now=None, **kw):
    clock = time.time() if now is None else now
    raw = {"objectives_": ["traffic"], "countries": ["PL"], "landing_hosts": ["example.com"],
           "max_daily_budget": 50, "currency": "PLN", "max_campaigns": 2, "days": 7}
    raw.update(kw)
    return mandates.build(acct.id, token_ids=tok_ids, now=clock, **raw)


SESSION = "mandate-test-session"


def grant_direct(app, acct, body, key=None):
    """A mandate the owner really signed (key, nonce, receipt), stored by its executor."""
    conn, ledger = app.state.conn, app.state.ledger
    pending = mandates.request(conn, ledger, acct, body, ceiling=50, currency="PLN",
                               meta_adapter="mock")
    key = key or Ed25519PrivateKey.generate()
    with db.transaction(conn):
        approver = Keys.register(conn, SESSION, public_key_raw(key.public_key()))
    payload = mandates.payload_of(body)
    approval = Approval(pending_id=pending.id, **pending.fields(approver),
                        signature=key.sign(pending.message(approver)).hex())
    with db.transaction(conn):
        verified = verify_and_consume(conn, approval, payload, ledger=ledger,
                                      session_id=SESSION)
    mandates.activate(conn, verified, payload)
    return mandates.get(conn, pending.item_id), key


# -- the whole path: /account → signature → a token's run creates its campaign PAUSED ----


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    app = make_app(tmp_path_factory.mktemp("mandate"))
    tok, value = token(app)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    data, signed, key, approver = grant_http(c, [tok.id])
    d, started = draft_confirm_run(c, bearer(value))
    return app, c, tok, value, data, signed, started


def test_the_signing_screen_shows_every_limit_and_the_grant_receipt_carries_the_body(flow):
    app, c, tok, value, data, signed, started = flow
    m = data["content"]["mandate"]
    assert data["fields"]["action"] == "mandate.grant"
    assert m["scope"] == ["campaign.create"] and m["token_ids"] == [tok.id]
    assert (m["objectives"], m["countries"], m["landing_hosts"]) == (
        ["traffic"], ["PL"], ["example.com"])
    assert m["max_daily_budget"] == 50 and m["currency"] == "PLN" and m["max_campaigns"] == 1
    assert 7 * DAY - 60 < m["valid_until"] - time.time() <= 7 * DAY
    (grant,) = [r for r in app.state.ledger.receipts(data["fields"]["item_id"])
                if r.kind == "approval"]
    assert grant.details["mandate"] == m and grant.agent == "human"
    assert grant.output_sha256 == sha256_hex(canonical(mandates.payload_of(m)))


def test_a_run_inside_the_mandate_creates_its_campaign_paused_without_a_tap(flow):
    app, c, tok, value, data, signed, started = flow
    run_id = started["run_id"]
    r = c.get(f"/api/v1/runs/{run_id}", headers=bearer(value)).json()
    assert r["mandate"]["fits"] is True and r["mandate"]["executed"] is True, r["mandate"]
    assert r["mandate"]["result"]["status"] == "PAUSED"
    assert r["status"] == "approved" and r["pending_approvals"] == []
    conn = app.state.conn
    row = conn.execute("SELECT status, approver, signature FROM approvals WHERE run_id = ? "
                       "AND action = 'campaign.create'", (run_id,)).fetchone()
    mid = data["fields"]["item_id"]
    m = mandates.get(conn, mid)
    assert row == ("approved", "mandate:" + mid, m.sha256)
    assert m.used == 1
    (camp,) = conn.execute("SELECT status FROM campaigns WHERE run_id = ?",
                           (run_id,)).fetchall()
    assert camp[0] == "PAUSED"
    assert not [x for x in app.state.adapter.calls if "activate" in str(x)]
    (approval,) = [x for x in app.state.ledger.receipts(run_id) if x.kind == "approval"]
    assert approval.agent == "mandate" and "sig" not in approval.details
    assert approval.details["by_mandate"] == mid
    assert approval.details["mandate_sha256"] == m.sha256
    assert approval.details["checked"] == {"objective": "traffic", "countries": ["PL"],
                                           "hosts": ["example.com"], "daily_budget": 30.0,
                                           "currency": "PLN", "n_of_max": [1, 1]}
    (write,) = [x for x in app.state.ledger.receipts(run_id) if x.kind == "external_write"]
    assert write.details["approval_id"] == approval.details["pending_id"]


def test_the_api_lists_the_mandates_and_whether_this_token_is_listed(flow):
    app, c, tok, value, data, *_ = flow
    r = c.get("/api/v1/mandates", headers=bearer(value))
    assert r.status_code == 200
    (m,) = r.json()["mandates"]
    assert m["lists_this_token"] is True and m["status"] == "used_up" and m["used"] == 1
    assert r.json()["can_grant"] is False
    _, other = token(app, label="Other")
    (m,) = c.get("/api/v1/mandates", headers=bearer(other)).json()["mandates"]
    assert m["lists_this_token"] is False


def test_verify_passes_on_the_clean_chain_with_approver_signatures(flow, tmp_path):
    app, c, *_ = flow
    path = tmp_path / "receipts.jsonl"
    path.write_text(app.state.ledger.export_jsonl())
    keys = Keys.registry(app.state.conn)
    report = verify(path, app.state.ledger.public_key, approver_keys=keys)
    assert report.ok, report.errors
    ledger_key = c.get("/api/ledger-key").json()["public_key"]
    keys_file = tmp_path / "approver-keys.json"
    keys_file.write_text(json.dumps({a: public_key_raw(k).hex() for a, k in keys.items()}))
    assert cli.main(["verify", str(path), "--key", ledger_key, "--approver-keys",
                     str(keys_file)]) == 0


def test_activation_is_never_by_mandate(flow):
    app, c, tok, value, data, signed, started = flow
    conn, ledger = app.state.conn, app.state.ledger
    (item,) = conn.execute("SELECT id FROM campaigns WHERE run_id = ?",
                           (started["run_id"],)).fetchone()
    pending, payload = media_buyer.request_activation(conn, ledger, item, 50)
    acct = app.state.accounts.get(tok.account_id)
    out = mandates.execute(conn, ledger, pending.id, tok.id, account=acct,
                           meta_adapter="mock", executor=None)
    assert out == {"fits": False, "reasons": ["action"]}
    assert conn.execute("SELECT status FROM approvals WHERE id = ?",
                        (pending.id,)).fetchone()[0] == "pending"
    # a forged mandate approval for an activation is refused by the executor itself
    forged = Verified(pending_id=pending.id, run_id=pending.run_id, action="campaign.activate",
                      item_id=pending.item_id, content_sha256=pending.content_sha256,
                      approver="mandate:" + data["fields"]["item_id"], signature="0" * 64)
    with db.transaction(conn):
        conn.execute("INSERT INTO nonces (nonce, consumed_ts) VALUES (?, ?)",
                     (pending.nonce, int(time.time())))
        conn.execute("UPDATE approvals SET status = 'approved', approver = ?, signature = ? "
                     "WHERE id = ?", (forged.approver, forged.signature, pending.id))
    with pytest.raises(executor.ExecutionRefused) as exc:
        executor.authorize(conn, forged, "campaign.activate", payload)
    assert exc.value.code == "mandate_wrong_action"
    # and an approver that is neither a human key nor a mandate is refused too
    odd = replace(forged, approver="robot:x")
    conn.execute("UPDATE approvals SET approver = 'robot:x' WHERE id = ?", (pending.id,))
    with pytest.raises(executor.ExecutionRefused) as exc:
        executor.authorize(conn, odd, "campaign.activate", payload)
    assert exc.value.code == "not_approved"


def test_a_second_run_past_max_campaigns_waits_for_the_owner_with_reasons(flow):
    app, c, tok, value, *_ = flow
    _, started = draft_confirm_run(c, bearer(value))
    r = c.get(f"/api/v1/runs/{started['run_id']}", headers=bearer(value)).json()
    assert r["status"] == "awaiting_approval"
    assert r["mandate"]["fits"] is False and "count" in r["mandate"]["reasons"]
    assert r["pending_approvals"] and r["sign_url"]


# -- outside the mandate: each limit, with a real run's pending campaign ----------------------


@pytest.fixture(scope="module")
def waiting(tmp_path_factory):
    """A token run without a mandate: its campaign.create waits for the owner."""
    app = make_app(tmp_path_factory.mktemp("mandate-out"))
    tok, value = token(app)
    c = TestClient(app, base_url=BASE)
    _, started = draft_confirm_run(c, bearer(value))
    conn = app.state.conn
    (pid, raw) = conn.execute(
        "SELECT a.id, p.payload FROM approvals a JOIN pending_payloads p ON "
        "p.approval_id = a.id WHERE a.run_id = ? AND a.action = 'campaign.create'",
        (started["run_id"],)).fetchone()
    return app, tok, value, started, pid, json.loads(raw)


def _mandate(body, used=0, revoked=None):
    return mandates.Mandate("mdt_x", body["account_id"], body, "0" * 64, "a", 0.0, revoked,
                            used)


@pytest.mark.parametrize(("change", "reason"), [
    ({"countries": ["DE"]}, "countries"),
    ({"max_daily_budget": 10}, "daily_budget"),
    ({"objectives_": ["leads"]}, "objective"),
    ({"landing_hosts": ["shop.example.org"]}, "landing_hosts"),
])
def test_fits_names_every_limit_the_campaign_breaks(waiting, change, reason):
    app, tok, value, started, pid, payload = waiting
    acct = app.state.accounts.get(tok.account_id)
    m = _mandate(body_for(app, acct, [tok.id], **change))
    assert mandates.fits(m, payload, token_id=tok.id) == [reason]


def test_fits_checks_token_expiry_revocation_count_and_currency(waiting):
    app, tok, value, started, pid, payload = waiting
    acct = app.state.accounts.get(tok.account_id)
    body = body_for(app, acct, [tok.id], max_campaigns=2)
    assert mandates.fits(_mandate(body), payload, token_id=tok.id) == []
    assert mandates.fits(_mandate(body), payload, token_id="tok_000000000000") == ["token"]
    assert mandates.fits(_mandate(body), payload, token_id=tok.id,
                         now=body["valid_until"]) == ["expired"]
    assert mandates.fits(_mandate(body, revoked=1.0), payload, token_id=tok.id) == ["revoked"]
    assert mandates.fits(_mandate(body, used=2), payload, token_id=tok.id) == ["count"]
    assert mandates.fits(_mandate({**body, "currency": "EUR"}), payload,
                         token_id=tok.id) == ["currency"]
    active = {**payload, "campaign": {**payload["campaign"], "status": "ACTIVE"}}
    assert "not_paused" in mandates.fits(_mandate(body), active, token_id=tok.id)
    elsewhere = {**payload, "ad": {**payload["ad"], "primary_text": "see https://evil.test/x"}}
    assert "landing_hosts" in mandates.fits(_mandate(body), elsewhere, token_id=tok.id)
    assert mandates.fits(_mandate(body), {**payload, "action": "campaign.activate"},
                         token_id=tok.id) == ["action"]


@pytest.mark.parametrize("case", ["country", "budget", "other_token", "revoked", "expired"])
def test_outside_the_mandate_the_run_waits_and_nothing_changes(waiting, case):
    app, tok, value, started, pid, payload = waiting
    conn, ledger = app.state.conn, app.state.ledger
    acct = app.state.accounts.get(tok.account_id)
    conn.execute("DELETE FROM mandates")  # this case's mandate only
    other, _ = token(app, label="U " + case)
    kw = {"country": {"countries": ["DE"]}, "budget": {"max_daily_budget": 10}}.get(case, {})
    m, _ = grant_direct(app, acct, body_for(app, acct, [tok.id], **kw))
    use, now = tok.id, None
    if case == "other_token":
        use = other.id
    if case == "revoked":
        assert mandates.revoke(conn, ledger, acct, m.id)
    if case == "expired":
        now = m.body["valid_until"] + 1
    out = mandates.execute(conn, ledger, pid, use, account=acct, meta_adapter="mock",
                           executor=None, now=now)
    want = {"country": "countries", "budget": "daily_budget", "other_token": "no_mandate",
            "revoked": "revoked", "expired": "approval_expired"}[case]
    assert out["fits"] is False and want in out["reasons"], out
    assert conn.execute("SELECT status FROM approvals WHERE id = ?", (pid,)).fetchone()[0] \
        == "pending"
    assert mandates.get(conn, m.id).used == 0
    assert not [r for r in ledger.receipts(started["run_id"]) if r.kind == "approval"]


def test_another_accounts_token_cannot_use_its_mandate_on_this_run(waiting):
    app, tok, value, started, pid, payload = waiting
    conn, ledger = app.state.conn, app.state.ledger
    stranger = account(app, "stranger")
    theirs, _ = token(app, username="stranger", label="Theirs")
    grant_direct(app, stranger, body_for(app, stranger, [theirs.id]))
    out = mandates.execute(conn, ledger, pid, theirs.id, account=stranger,
                           meta_adapter="mock", executor=None)
    assert out == {"fits": False, "reasons": ["run_not_yours"]}
    out = mandates.execute(conn, ledger, pid, theirs.id,
                           account=app.state.accounts.get(tok.account_id),
                           meta_adapter="mock", executor=None)
    assert out == {"fits": False, "reasons": ["token"]}
    assert conn.execute("SELECT status FROM approvals WHERE id = ?", (pid,)).fetchone()[0] \
        == "pending"


def test_an_expired_mandate_does_not_fit_even_within_the_approval_window(waiting):
    app, tok, value, started, pid, payload = waiting
    acct = app.state.accounts.get(tok.account_id)
    body = body_for(app, acct, [tok.id])
    m = _mandate({**body, "valid_until": int(time.time()) - 1})
    assert mandates.fits(m, payload, token_id=tok.id) == ["expired"]


# -- who may grant, and what can be signed ------------------------------------------------


def test_request_validation_caps_days_budget_and_tokens(tmp_path):
    app = make_app(tmp_path)
    acct = account(app)
    tok, _ = token(app)
    conn, ledger = app.state.conn, app.state.ledger

    def problems(body):
        with pytest.raises(mandates.MandateError) as exc:
            mandates.request(conn, ledger, acct, body, ceiling=50, currency="PLN",
                             meta_adapter="mock")
        return exc.value.fields

    now = time.time()
    body = body_for(app, acct, [tok.id], now=now)
    assert problems({**body, "valid_until": int(now) + 31 * DAY}) == ["valid_until"]
    assert problems(body_for(app, acct, [tok.id], days=31)) == ["valid_until"]
    assert problems(body_for(app, acct, [tok.id], max_daily_budget=51)) == ["max_daily_budget"]
    assert problems(body_for(app, acct, [tok.id], max_campaigns=21)) == ["max_campaigns"]
    assert problems(body_for(app, acct, [tok.id], currency="EUR")) == ["currency"]
    foreign, _ = token(app, username="someone", label="Theirs")
    assert problems(body_for(app, acct, [foreign.id])) == ["token_ids"]
    app.state.assistants.tokens.revoke(acct.id, tok.id)
    assert problems(body_for(app, acct, [tok.id])) == ["token_ids"]
    assert problems(body_for(app, acct, [])) == ["token_ids"]
    assert problems(body_for(app, acct, [tok.id], landing_hosts=["https://x.pl/"])) == [
        "landing_hosts"]
    assert conn.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


def test_the_judge_and_trial_accounts_cannot_grant_one_on_a_live_adapter(tmp_path):
    app = make_app(tmp_path)
    store = Accounts(app.state.conn)
    judge = store.by_username("judge")
    acct = account(app)
    trial = store.set_tier(acct.username, "trial")
    for who in (judge, trial):
        assert not mandates.may_grant(who, "graph")
        with pytest.raises(mandates.MandateError) as exc:
            mandates.request(app.state.conn, app.state.ledger, who, {}, ceiling=50,
                             currency="PLN", meta_adapter="graph")
        assert exc.value.code == "mandate_not_allowed"
        out = mandates.execute(app.state.conn, app.state.ledger, "x", "tok_x", account=who,
                               meta_adapter="graph", executor=None)
        assert out == {"fits": False, "reasons": ["account_not_allowed"]}
    full = store.set_tier(acct.username, "full")
    assert mandates.may_grant(full, "graph") and mandates.may_grant(trial, "mock")


def test_the_account_page_shows_the_block_to_the_owner_and_not_to_the_judge(tmp_path):
    app = make_app(tmp_path)
    token(app)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    page = c.get("/account").text
    assert 'id="mandates"' in page and "/account/mandates/prepare" in page
    j = TestClient(app, base_url=BASE)
    login(j, app, "judge")
    assert 'id="mandates"' not in j.get("/account").text
    r = j.post("/account/mandates/prepare", data={"csrf": csrf(j), **limits()})
    assert r.status_code == 403


def test_the_form_refuses_bad_limits_and_needs_csrf(tmp_path):
    app = make_app(tmp_path)
    tok, _ = token(app)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    r = prepare(c, [tok.id], max_daily_budget="500", days="45")
    assert r.status_code == 422 and "daily budget" in r.text and "days valid" in r.text
    r = c.post("/account/mandates/prepare", data={"csrf": "nope", "token": [tok.id],
                                                  **limits()})
    assert r.status_code == 403
    assert app.state.conn.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0


def test_only_the_mandates_own_account_can_sign_it(tmp_path):
    app = make_app(tmp_path)
    tok, _ = token(app)
    owner = TestClient(app, base_url=BASE)
    login(owner, app)
    r = prepare(owner, [tok.id])
    approval_id = r.headers["location"].split("approval=")[1]
    other = TestClient(app, base_url=BASE)
    login(other, app, "intruder")
    key, approver = register(other)
    _, r = sign(other, key, approver, approval_id)
    assert r.status_code == 403 and r.json()["error"] == "mandate_not_yours"
    assert app.state.conn.execute("SELECT status FROM approvals WHERE id = ?",
                                  (approval_id,)).fetchone()[0] == "pending"
    assert app.state.conn.execute("SELECT count(*) FROM mandates").fetchone()[0] == 0


def test_revoking_on_the_account_page_leaves_a_receipt(tmp_path):
    app = make_app(tmp_path)
    tok, _ = token(app)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    data, *_ = grant_http(c, [tok.id])
    mid = data["fields"]["item_id"]
    r = c.post("/account/mandates/revoke", data={"csrf": csrf(c), "mandate": mid},
               follow_redirects=False)
    assert r.status_code == 303 and "#mandates" in r.headers["location"]
    assert mandates.get(app.state.conn, mid).status() == "revoked"
    (rev,) = [x for x in app.state.ledger.receipts(mid) if x.kind == "mandate.revoke"]
    assert rev.details == {"mandate_id": mid, "mandate_sha256": mandates.get(
        app.state.conn, mid).sha256}
    assert c.post("/account/mandates/revoke", data={"csrf": csrf(c), "mandate": mid}
                  ).status_code == 404


def test_no_api_or_mcp_operation_can_make_sign_or_revoke_a_mandate():
    from studio import assistants

    assert assistants.OPS["mandates"].scope == "read"
    assert not [n for n in assistants.OPS if "mandate" in n and n != "mandates"]


# -- studio verify: the five tamper cases ----------------------------------------------------


class Chain:
    """A real chain: a signed mandate (max 1 campaign) and one campaign approved by it."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.conn = db.connect(tmp / "studio.db")
        db.init(self.conn)
        self.key_path = tmp / "ledger.key"
        self.ledger = db.open_ledger(self.conn, self.key_path)
        store = Accounts(self.conn)
        self.acct = store.create("owner", OTHER_PASSWORD)
        from studio import api_tokens

        tokens = api_tokens.Tokens(self.conn, api_tokens.derive_key(
            db.load_or_create_ledger_key(self.key_path)))
        self.tok, _ = tokens.create(self.acct.id, "Assistant", "draft", 30)
        app = type("A", (), {})()
        app.state = type("S", (), {"conn": self.conn, "ledger": self.ledger})()
        body = mandates.build(self.acct.id, token_ids=[self.tok.id], objectives_=["traffic"],
                              countries=["PL"], landing_hosts=["example.com"],
                              max_daily_budget=50, currency="PLN", max_campaigns=1, days=7)
        self.mandate, self.owner_key = grant_direct(app, self.acct, body)
        self.payload = {
            "action": "campaign.create", "run_id": "run_m1", "brief_sha256": "b" * 64,
            "brief": {"goal": "traffic"},
            "delivery": objectives.delivery("traffic", page_id=MOCK_PAGE),
            "campaign": {"objective": "OUTCOME_TRAFFIC", "status": "PAUSED",
                         "daily_budget_minor": 3000, "currency": "PLN"},
            "targeting": {"geo_locations": {"countries": ["PL"]}},
            "link": "https://example.com/"}
        self.ledger.append("run_m1", agent="producer", kind="model_call")
        with db.transaction(self.conn):  # the run of a brief this account's token confirmed
            self.conn.execute("INSERT INTO briefs (id, run_id, text, source, created_ts) "
                              "VALUES ('b_00000000000000aa', 'run_m1', '', 'text', ?)",
                              (time.time(),))
            self.conn.execute("INSERT INTO api_briefs (brief_id, account_id, token_id, "
                              "created_ts) VALUES ('b_00000000000000aa', ?, ?, ?)",
                              (self.acct.id, self.tok.id, time.time()))
        pending = Pending.create("campaign.create", "cmp_m1", self.payload,
                                 ledger=self.ledger, run_id="run_m1")
        with db.transaction(self.conn):
            pending.save(self.conn)
            self.conn.execute("INSERT INTO pending_payloads (approval_id, run_id, payload, "
                              "created_ts) VALUES (?, ?, ?, ?)",
                              (pending.id, "run_m1", canonical(self.payload).decode(),
                               time.time()))
        out = mandates.execute(self.conn, self.ledger, pending.id, self.tok.id,
                               account=self.acct, meta_adapter="mock", executor=None)
        assert out["fits"] is True, out

    def lines(self):
        return [json.loads(x) for x in self.ledger.export_jsonl().splitlines()]

    def find(self, lines, pred):
        return next(i for i, x in enumerate(lines) if "manifest" not in x and pred(x))

    def resign(self, lines):
        """What a dishonest server with the ledger key can do: re-link, re-hash and
        re-sign every receipt and the manifest after an edit."""
        key = db.load_or_create_ledger_key(self.key_path)
        heads, counts, out, last = {}, {}, [], 0.0
        for x in lines:
            if "manifest" in x:
                continue
            x = {**x, "prev": heads.get(x["run_id"], "0" * 64)}
            r = Receipt(**{**x, "hash": "", "sig": ""})
            digest = r.compute_hash()
            x.update(hash=digest, sig=key.sign(bytes.fromhex(digest)).hex())
            heads[x["run_id"]] = digest
            counts[x["run_id"]] = counts.get(x["run_id"], 0) + 1
            last = max(last, x["ts"])
            out.append(x)
        body = {"seq": self.ledger.seq(), "ts": last + 1, "epoch": key_id(key.public_key()),
                "runs": {r: {"head": heads[r], "count": counts[r]} for r in heads}}
        out.append({"manifest": {**body, "sig": key.sign(_manifest_bytes(body)).hex()}})
        return out

    def check(self, lines):
        path = self.tmp / "export.jsonl"
        path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
        return verify(path, self.ledger.public_key, approver_keys=Keys.registry(self.conn))


@pytest.fixture
def chain(tmp_path):
    return Chain(tmp_path)


def _is_mandate_approval(x):
    return x["kind"] == "approval" and "by_mandate" in x["details"]


def test_the_clean_mandate_chain_verifies(chain):
    report = chain.check(chain.lines())
    assert report.ok, report.errors
    assert chain.check(chain.resign(chain.lines())).ok  # the re-signing helper is neutral


def test_tamper_mandate_edited_after_signing(chain):
    lines = chain.lines()
    i = chain.find(lines, lambda x: x["kind"] == "approval"
                   and x["details"].get("action") == "mandate.grant")
    lines[i]["details"]["mandate"]["max_daily_budget"] = 500
    report = chain.check(chain.resign(lines))
    assert not report.ok
    assert any(lines[i]["id"] in e and "not the one the owner signed" in e
               for e in report.errors), report.errors


def test_tamper_execution_after_revoke(chain):
    lines = chain.lines()
    i = chain.find(lines, _is_mandate_approval)
    ts = lines[i]["ts"]
    revoke = {**lines[i], "id": "f" * 32, "run_id": chain.mandate.id, "agent": "owner",
              "kind": "mandate.revoke", "ts": ts - 0.5, "output_sha256": chain.mandate.sha256,
              "details": {"mandate_id": chain.mandate.id,
                          "mandate_sha256": chain.mandate.sha256}}
    lines.insert(i, revoke)
    report = chain.check(chain.resign(lines))
    assert not report.ok
    assert any(lines[i + 1]["id"] in e and "after it was revoked" in e
               for e in report.errors), report.errors


def test_tamper_execution_after_expiry(chain):
    lines = chain.lines()
    i = chain.find(lines, _is_mandate_approval)
    lines[i]["ts"] = chain.mandate.body["valid_until"] + 60
    report = chain.check(chain.resign(lines))
    assert any(lines[i]["id"] in e and "after it expired" in e
               for e in report.errors), report.errors


def test_tamper_budget_above_the_mandate(chain):
    lines = chain.lines()
    i = chain.find(lines, _is_mandate_approval)
    lines[i]["details"]["checked"]["daily_budget"] = 75.0
    report = chain.check(chain.resign(lines))
    assert any(lines[i]["id"] in e and "daily budget above the mandate" in e
               for e in report.errors), report.errors


def test_tamper_more_campaigns_than_the_mandate_allows(chain):
    lines = chain.lines()
    i = chain.find(lines, _is_mandate_approval)
    second = json.loads(json.dumps(lines[i]))
    second.update(id="e" * 32, run_id="run_m2", ts=lines[i]["ts"] + 1)
    second["details"].update(chain_head="0" * 64, nonce="n_" + "e" * 32,
                             pending_id="d" * 32, item_id="cmp_m2")
    second["details"]["checked"]["n_of_max"] = [2, 1]
    lines.insert(i + 1, second)
    report = chain.check(chain.resign(lines))
    assert any("e" * 32 in e and "more campaigns than the mandate allows" in e
               for e in report.errors), report.errors


def test_tamper_a_mandate_approval_for_another_action_or_without_a_grant(chain):
    lines = chain.lines()
    i = chain.find(lines, _is_mandate_approval)
    lines[i]["details"]["action"] = "campaign.activate"
    report = chain.check(chain.resign(lines))
    assert any("cannot approve 'campaign.activate'" in e for e in report.errors)
    lines = chain.lines()
    g = chain.find(lines, lambda x: x["kind"] == "approval"
                   and x["details"].get("action") == "mandate.grant")
    lines.pop(g)
    report = chain.check(chain.resign(lines))
    assert any("without an earlier signed mandate.grant" in e for e in report.errors)


def test_the_executor_refuses_a_revoked_or_tampered_mandate(chain):
    conn = chain.conn
    row = conn.execute("SELECT id, run_id, item_id, content_sha256, approver, signature "
                       "FROM approvals WHERE action = 'campaign.create'").fetchone()
    v = Verified(pending_id=row[0], run_id=row[1], action="campaign.create", item_id=row[2],
                 content_sha256=row[3], approver=row[4], signature=row[5])
    conn.execute("UPDATE mandates SET body = ? WHERE id = ?",
                 (canonical({**chain.mandate.body, "max_daily_budget": 500}).decode(),
                  chain.mandate.id))
    with pytest.raises(executor.ExecutionRefused) as exc:
        executor.authorize(conn, v, "campaign.create", chain.payload)
    assert exc.value.code == "mandate_tampered"
    conn.execute("UPDATE mandates SET body = ?, revoked_ts = 1 WHERE id = ?",
                 (canonical(chain.mandate.body).decode(), chain.mandate.id))
    with pytest.raises(executor.ExecutionRefused) as exc:
        executor.authorize(conn, v, "campaign.create", chain.payload)
    assert exc.value.code == "mandate_revoked"
    assert conn.execute("SELECT count(*) FROM executions").fetchone()[0] == 0
