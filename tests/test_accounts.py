"""studio.accounts without the web layer: hashing, policy, TOTP (RFC 6238), recovery codes,
the judge demo account, sessions, deletion and export."""

import base64
import secrets
import sqlite3

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio import accounts, db
from studio.accounts import Accounts, SecretBox

PASSWORD = "Lp4-" + secrets.token_hex(12)  # hex: never contains a user name


@pytest.fixture
def store(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.init(conn)
    return Accounts(conn, SecretBox(Ed25519PrivateKey.generate()))


def enrol(store, account):
    secret = store.totp_begin(account.id, PASSWORD)
    codes = store.totp_confirm(account.id, accounts.totp_code(secret, accounts.totp_step()))
    return secret, codes


# -- passwords --------------------------------------------------------------------------


def test_hash_is_argon2id_and_verifies():
    digest = accounts.hash_password(PASSWORD)
    assert digest.startswith("$argon2id$")
    assert accounts.verify_password(digest, PASSWORD)
    assert not accounts.verify_password(digest, PASSWORD + "x")
    assert not accounts.verify_password("not a hash", PASSWORD)


@pytest.mark.parametrize("bad,error", [
    ("short-Pw1", accounts.WeakPassword), ("x" * 257, accounts.WeakPassword),
    ("password1234", accounts.CommonPassword), ("Password1234", accounts.CommonPassword),
    ("aaaaaaaaaaaaaaab", accounts.CommonPassword), ("ola-ola-ola-2026", accounts.CommonPassword),
    ("line\nbreak-pass-1", accounts.WeakPassword),
])
def test_password_policy(bad, error):
    with pytest.raises(error):
        accounts.check_password_policy(bad, "ola")


def test_weak_password_sentence_names_the_minimum():
    from studio.ui import i18n

    assert str(accounts.MIN_PASSWORD_LEN) in i18n.EN["err.weak_password"]
    assert str(accounts.MIN_PASSWORD_LEN) in i18n.PL["err.weak_password"]


def test_create_and_authenticate(store):
    a = store.create("Ola", PASSWORD, display_name="Ola K.", email="ola@example.com")
    assert a.username == "ola" and a.id.startswith("acct_") and not a.is_demo
    assert store.authenticate("ola", PASSWORD).id == a.id
    assert store.authenticate("OLA", PASSWORD).id == a.id
    assert store.authenticate("ola", "wrong-" + PASSWORD) is None
    assert store.authenticate("nobody", PASSWORD) is None
    assert store.authenticate("", "") is None
    assert store.authenticate("ola", PASSWORD + "y" * 300) is None
    with pytest.raises(accounts.UsernameTaken):
        store.create("ola", PASSWORD)
    with pytest.raises(accounts.UsernameTaken):
        store.create("judge", PASSWORD)
    with pytest.raises(accounts.InvalidUsername):
        store.create("a b", PASSWORD)


def test_unknown_user_costs_one_verification(store, monkeypatch):
    calls = []
    real = accounts.verify_password
    monkeypatch.setattr(accounts, "verify_password", lambda h, p: calls.append(h) or real(h, p))
    store.authenticate("nobody", PASSWORD)
    store.create("ola", PASSWORD)
    store.authenticate("ola", "wrong-password-x")
    assert len(calls) == 2 and all(h.startswith("$argon2id$") for h in calls)


def test_disabled_account_cannot_sign_in(store):
    store.create("ola", PASSWORD)
    store.set_status("ola", "disabled")
    assert store.authenticate("ola", PASSWORD) is None
    store.set_status("ola", "active")
    assert store.authenticate("ola", PASSWORD) is not None


# -- the judge demo account ---------------------------------------------------------------


def test_demo_account_follows_judge_password(store):
    judge = store.seed_demo(PASSWORD)
    assert judge.is_demo and judge.username == "judge"
    assert store.authenticate("judge", PASSWORD).id == judge.id
    other = "Jd7-" + secrets.token_urlsafe(24)
    assert store.seed_demo(other).id == judge.id  # same row, new hash
    assert store.authenticate("judge", PASSWORD) is None
    assert store.authenticate("judge", other) is not None
    assert store.seed_demo("") is None
    assert store.authenticate("judge", other) is None  # no JUDGE_PASSWORD: no judge login
    store.seed_demo(other)
    assert store.authenticate("judge", other) is not None


def test_operator_cannot_touch_the_demo_account(store):
    store.seed_demo(PASSWORD)
    for action in (lambda: store.set_status("judge", "disabled"),
                   lambda: store.reset_password("judge", "Another-pass-123")):
        with pytest.raises(accounts.DemoAccount):
            action()


def test_demo_account_has_no_second_factor_password_change_or_deletion(store):
    judge = store.seed_demo(PASSWORD)
    with pytest.raises(accounts.DemoAccount):
        store.totp_begin(judge.id, PASSWORD)
    with pytest.raises(accounts.DemoAccount):
        store.change_password(judge.id, PASSWORD, "N3w-pass-word-x", "N3w-pass-word-x",
                              keep_sid="")
    with pytest.raises(accounts.DemoAccount):
        store.delete(judge.id, confirm="judge", password=PASSWORD)
    assert store.get(judge.id).status == "active"


# -- TOTP -------------------------------------------------------------------------------

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize("t,code", [(59, "287082"), (1111111109, "081804"),
                                    (1234567890, "005924"), (2000000000, "279037")])
def test_totp_matches_rfc_6238_vectors(t, code):
    assert accounts.totp_code(RFC_SECRET, t // 30) == code


def test_totp_window_and_replay():
    now = 1_000_000.0
    step = accounts.totp_step(now)
    code = accounts.totp_code(RFC_SECRET, step)
    assert accounts.match_totp(RFC_SECRET, code, last_step=0, now=now) == step
    assert accounts.match_totp(RFC_SECRET, code, last_step=step, now=now) is None  # used
    previous = accounts.totp_code(RFC_SECRET, step - 1)
    assert accounts.match_totp(RFC_SECRET, previous, last_step=0, now=now) == step - 1
    far = accounts.totp_code(RFC_SECRET, step - 3)
    assert accounts.match_totp(RFC_SECRET, far, last_step=0, now=now) is None
    assert accounts.match_totp(RFC_SECRET, "12345", last_step=0, now=now) is None


def test_secret_is_sealed_at_rest(store):
    a = store.create("ola", PASSWORD)
    secret, _ = enrol(store, a)
    raw = store.conn.execute("SELECT totp_secret FROM accounts WHERE id = ?", (a.id,)).fetchone()
    assert raw[0].startswith("v1:") and secret not in raw[0]
    other = SecretBox(Ed25519PrivateKey.generate())
    assert other.open(a.id, raw[0]) is None  # another ledger key cannot open it
    assert store.box.open("acct_other", raw[0]) is None  # bound to the account id


def test_enrolment_needs_password_and_a_code(store):
    a = store.create("ola", PASSWORD)
    with pytest.raises(accounts.WrongPassword):
        store.totp_begin(a.id, "nope-nope-nope")
    secret = store.totp_begin(a.id, PASSWORD)
    assert store.get(a.id).totp_pending and not store.get(a.id).totp_enabled
    with pytest.raises(accounts.WrongCode):
        store.totp_confirm(a.id, "000000" if accounts.totp_code(
            secret, accounts.totp_step()) != "000000" else "111111")
    codes = store.totp_confirm(a.id, accounts.totp_code(secret, accounts.totp_step()))
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(c) == 19 and c.count("-") == 3 for c in codes)
    assert store.get(a.id).totp_enabled and store.recovery_codes_left(a.id) == 10
    stored = [r[0] for r in store.conn.execute("SELECT code_sha256 FROM recovery_codes")]
    assert not any(c.replace("-", "") in "".join(stored) for c in codes)  # hashes only


def test_second_factor_codes_work_once(store):
    a = store.create("ola", PASSWORD)
    secret, codes = enrol(store, a)
    # the confirming code's step is used: the same code cannot sign in
    assert not store.verify_second_factor(a.id, accounts.totp_code(secret, accounts.totp_step()))
    following = accounts.totp_code(secret, accounts.totp_step() + 1)
    assert store.verify_second_factor(a.id, following)
    assert not store.verify_second_factor(a.id, following)  # replay
    assert store.verify_second_factor(a.id, codes[0].lower().replace("-", " "))
    assert not store.verify_second_factor(a.id, codes[0])  # a recovery code twice
    assert store.recovery_codes_left(a.id) == 9
    assert not store.verify_second_factor(a.id, "AAAA-BBBB-CCCC-DDDD")


def test_disable_needs_password_and_code(store):
    a = store.create("ola", PASSWORD)
    _, codes = enrol(store, a)
    with pytest.raises(accounts.WrongPassword):
        store.totp_disable(a.id, "nope-nope-nope", codes[0])
    with pytest.raises(accounts.WrongCode):
        store.totp_disable(a.id, PASSWORD, "123456")
    store.totp_disable(a.id, PASSWORD, codes[1])
    assert not store.get(a.id).totp_enabled and store.recovery_codes_left(a.id) == 0


def test_operator_reset_can_clear_the_second_factor(store):
    a = store.create("ola", PASSWORD)
    enrol(store, a)
    store.reset_password("ola", "Fresh-pass-2026x", clear_2fa=True)
    assert not store.get(a.id).totp_enabled
    assert store.authenticate("ola", "Fresh-pass-2026x") is not None


# -- profile ----------------------------------------------------------------------------


def test_profile_validation_and_company_fields(store):
    a = store.create("ola", PASSWORD)
    b = store.update_profile(a.id, {"advertiser_type": "company", "legal_name": "Ola Yarn Ltd",
                                    "country": "United Kingdom", "tax_id": "GB 123 4567 89",
                                    "lang": "pl", "theme": "dark", "palette": "warm"})
    assert (b.legal_name, b.country, b.tax_id, b.lang) == ("Ola Yarn Ltd", "United Kingdom",
                                                           "GB 123 4567 89", "pl")
    c = store.update_profile(a.id, {"advertiser_type": "person", "legal_name": "kept?"})
    assert c.legal_name == "" and c.tax_id == "" and c.country == ""
    for bad in ({"lang": "de"}, {"theme": "neon"}, {"advertiser_type": "ngo"},
                {"email": "not-an-email"}, {"display_name": "x" * 81},
                {"tax_id": "1\u202e2"}, {"display_name": "a\x00b"}):
        with pytest.raises(accounts.InvalidProfile):
            store.update_profile(a.id, bad)


# -- sessions, deletion, export ---------------------------------------------------------


def test_sessions_revoke_with_their_keys(store):
    a = store.create("ola", PASSWORD)
    store.record_session("sid-a", a.id, 2**40, "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0")
    store.record_session("sid-b", a.id, 2**40, "Mozilla/5.0 (iPhone) Safari/605")
    store.conn.execute("INSERT INTO keys (approver, public_key, session_sha256, created_ts, "
                       "expires_ts) VALUES ('k1', zeroblob(32), ?, 0, ?)",
                       (accounts.sha256_hex("sid-b"), 2**40))
    rows = store.sessions(a.id, "sid-a")
    assert [r.user_agent for r in rows] == ["Safari · iOS", "Firefox · Linux"]
    assert [r.current for r in rows] == [False, True]
    assert store.revoke_others(a.id, current_sid="sid-a") == 1
    assert store.session_account("sid-b") is None and store.session_account("sid-a") == a.id
    revoked = store.conn.execute("SELECT revoked_ts FROM keys WHERE approver = 'k1'").fetchone()
    assert revoked[0] is not None


def test_short_agent_never_keeps_the_raw_header():
    ua = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit Chrome/141.0 Safari/537.36 Edg/141.0"
    assert accounts.short_agent(ua) == "Edge · Windows"
    assert accounts.short_agent("") == "?"


def test_delete_erases_personal_fields_and_keeps_a_tombstone(store):
    a = store.create("ola", PASSWORD, display_name="Ola", email="ola@example.com")
    store.update_profile(a.id, {"advertiser_type": "company", "legal_name": "Ola Ltd",
                                "country": "PL", "tax_id": "123"})
    store.record_session("sid-a", a.id, 2**40, "Firefox/140 Linux")
    with pytest.raises(accounts.ConfirmationMismatch):
        store.delete(a.id, confirm="someone", password=PASSWORD)
    with pytest.raises(accounts.WrongPassword):
        store.delete(a.id, confirm="ola", password="wrong-wrong-wrong")
    store.delete(a.id, confirm="ola", password=PASSWORD)
    row = store.conn.execute("SELECT * FROM accounts WHERE id = ?", (a.id,)).fetchone()
    text = " ".join(str(v) for v in row)
    for personal in ("ola", "Ola", "example.com", "Ola Ltd", "$argon2"):
        assert personal not in text.replace(a.id, ""), personal
    assert store.get(a.id).status == "deleted"
    assert store.session_account("sid-a") is None
    assert store.authenticate("ola", PASSWORD) is None
    assert store.create("ola", PASSWORD).id != a.id  # the name is free again


def test_delete_with_two_factor_needs_a_code(store):
    a = store.create("ola", PASSWORD)
    _, codes = enrol(store, a)
    with pytest.raises(accounts.WrongCode):
        store.delete(a.id, confirm="ola", password=PASSWORD, code="")
    store.delete(a.id, confirm="ola", password=PASSWORD, code=codes[3])
    assert store.conn.execute("SELECT count(*) FROM recovery_codes").fetchone()[0] == 0


def test_schema_is_idempotent_and_in_db_init(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    db.init(conn)
    db.init(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "recovery_codes", "account_sessions"} <= tables


def test_challenges_expire_and_die_after_three_failures():
    ch = accounts.Challenges()
    token = ch.open("acct_x", "/", now=0.0)
    assert ch.get(token, now=1.0).account_id == "acct_x"
    assert ch.get(token, now=accounts.Challenges.TTL_S + 1) is None
    token = ch.open("acct_x", "/")
    for _ in range(accounts.Challenges.MAX_FAILURES):
        ch.fail(token)
    assert ch.get(token) is None
    assert ch.get("x" * 200) is None and ch.get(None) is None


def test_challenges_of_an_account_can_be_dropped():
    ch = accounts.Challenges()
    mine, other = ch.open("acct_a", "/"), ch.open("acct_b", "/")
    ch.drop_account("acct_a")
    assert ch.get(mine) is None and ch.get(other) is not None
