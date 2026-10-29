"""Personal access tokens follow the account's credentials: a password change or reset, a
two-step change, a status change or "sign out other sessions" kills every token; the
shared demo (judge) account can neither mint nor use one; minting needs the current
password and the two-step code. (Separate from ``test_assistants.py``, whose module-wide
tripwires forbid account changes.)"""

import re

import pytest
from fastapi.testclient import TestClient

from studio.accounts import Accounts
from tests.forms import csrf as csrf_of
from tests.test_assistants import BASE, OTHER_PASSWORD, bearer, login, make_app, token


def _me(c, value):
    return c.get("/api/v1/me", headers=bearer(value)).status_code


def test_a_password_change_kills_every_token(tmp_path):
    app = make_app(tmp_path)
    _, value = token(app)
    c = TestClient(app, base_url=BASE)
    assert _me(c, value) == 200
    login(c, app)
    page = c.get("/account").text
    form = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    new = "New-" + OTHER_PASSWORD
    r = c.post("/account/password", data={"csrf": form, "current": OTHER_PASSWORD,
                                           "new": new, "confirm": new})
    assert r.status_code in (200, 303), r.text
    assert _me(c, value) == 401
    _, fresh = token(app)  # a token made after the change works
    assert _me(c, fresh) == 200


def test_signing_out_other_sessions_revokes_every_token(tmp_path):
    app = make_app(tmp_path)
    tok, value = token(app)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    form = re.search(r'name="csrf" value="([^"]+)"', c.get("/account").text).group(1)
    r = c.post("/account/sessions/revoke-others", data={"csrf": form},
               follow_redirects=False)
    assert r.status_code == 303
    assert _me(c, value) == 401
    assert app.state.assistants.tokens.get(tok.account_id, tok.id).revoked_ts is not None


def test_status_changes_and_operator_resets_kill_tokens(tmp_path):
    app = make_app(tmp_path)
    store = Accounts(app.state.conn)
    _, value = token(app)
    c = TestClient(app, base_url=BASE)
    store.set_status("owner", "disabled")
    assert _me(c, value) == 401
    store.set_status("owner", "active")  # re-enabled: the old token stays dead
    assert _me(c, value) == 401
    _, value = token(app)
    store.reset_password("owner", "Reset-" + OTHER_PASSWORD)
    assert _me(c, value) == 401


def test_the_shared_demo_account_cannot_mint_or_use_tokens(tmp_path):
    app = make_app(tmp_path)
    judge = Accounts(app.state.conn).by_username("judge")
    with pytest.raises(Exception, match="token_demo"):
        app.state.assistants.tokens.create(judge.id, "judge bot", "draft", 30)
    c = TestClient(app, base_url=BASE)
    login(c, app, "judge")
    page = c.get("/account?lang=en").text
    assert "Tokens are off for the shared judge account" in page
    assert 'action="/account/tokens/create"' not in page
    form = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    r = c.post("/account/tokens/create", data={"csrf": form, "label": "x", "scope": "draft",
                                               "days": "90",
                                               "password": app.state.judge_password})
    assert r.status_code == 403
    assert "/try" in r.text
    r = c.post("/account/tokens/revoke", data={"csrf": form, "token": "tok_000000000000"})
    assert r.status_code == 403
    assert app.state.conn.execute("SELECT count(*) FROM api_tokens").fetchone()[0] == 0
    pl = c.get("/account?lang=pl").text
    assert "Tokeny są wyłączone dla wspólnego konta jury" in pl
    # a token row smuggled in for the demo account (or one made before) is refused anyway
    other, value = token(app)
    with app.state.conn.lock:
        app.state.conn.execute("UPDATE api_tokens SET account_id = ?, credential_gen = "
                               "(SELECT credential_gen FROM accounts WHERE id = ?) "
                               "WHERE id = ?", (judge.id, judge.id, other.id))
        app.state.conn.commit()
    assert _me(c, value) == 401


def test_minting_needs_the_current_password_and_the_two_step_code(tmp_path):
    from studio import accounts as acc

    app = make_app(tmp_path)
    c = TestClient(app, base_url=BASE)
    login(c, app)
    form = re.search(r'name="csrf" value="([^"]+)"', c.get("/account").text).group(1)
    base = {"csrf": form, "label": "bot", "scope": "read", "days": "30"}
    assert c.post("/account/tokens/create", data=base).status_code == 400
    assert c.post("/account/tokens/create", data={**base, "password": "wrong-" + OTHER_PASSWORD}
                  ).status_code == 400
    assert app.state.conn.execute("SELECT count(*) FROM api_tokens").fetchone()[0] == 0
    store = app.state.accounts
    owner = store.by_username("owner")
    store.totp_begin(owner.id, OTHER_PASSWORD)
    pending = store.totp_pending_secret(owner.id)
    step = acc.totp_step()
    store.totp_confirm(owner.id, acc.totp_code(pending, step))
    with TestClient(app, base_url=BASE) as two:  # sign in with both steps
        r = two.post("/login", data={"username": "owner", "password": OTHER_PASSWORD,
                                     "csrf": csrf_of(two)})
        assert r.json().get("two_factor") is True
        r = two.post("/login/2fa", data={"code": acc.totp_code(pending, step + 1),
                                         "csrf": csrf_of(two)})
        assert r.status_code == 200, r.text
        form = re.search(r'name="csrf" value="([^"]+)"', two.get("/account").text).group(1)
        for code in ("", "000000"):
            r = two.post("/account/tokens/create", data={**base, "csrf": form,
                                                         "password": OTHER_PASSWORD,
                                                         "code": code})
            assert r.status_code == 400, code
    assert app.state.conn.execute("SELECT count(*) FROM api_tokens").fetchone()[0] == 0
