"""``studio account …``: the operator creates, lists, disables and resets accounts; there is
no other way to get one. Passwords never come from argv."""

import io
import secrets

import pytest

from studio import cli, db
from studio.accounts import Accounts

PASSWORD = "Cli-" + secrets.token_hex(12)


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "var"
    data.mkdir()
    conn = db.connect(data / "studio.db")
    db.init(conn)
    conn.close()
    monkeypatch.setenv("STUDIO_DATA_DIR", str(data))
    monkeypatch.delenv("JUDGE_PASSWORD", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    return tmp_path / "missing.env", data


def run(env, *argv, stdin=""):
    import sys

    old = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        return cli.main(["account", "--env-file", str(env[0]), *argv])
    finally:
        sys.stdin = old


def store(env):
    conn = db.connect(env[1] / "studio.db")
    return Accounts(conn)


def test_create_list_disable_enable_reset(env, capsys):
    assert run(env, "create", "ola", "--display-name", "Ola", "--password-stdin",
               stdin=PASSWORD + "\n") == 0
    assert "created ola (acct_" in capsys.readouterr().out
    assert store(env).authenticate("ola", PASSWORD) is not None
    assert run(env, "list") == 0
    out = capsys.readouterr().out
    assert "ola" in out and "active" in out and PASSWORD not in out and "$argon2" not in out
    assert run(env, "disable", "ola") == 0
    assert store(env).authenticate("ola", PASSWORD) is None
    assert run(env, "enable", "ola") == 0
    new = "New-" + secrets.token_hex(10)
    assert run(env, "reset-password", "ola", "--clear-2fa", "--password-stdin",
               stdin=new + "\n") == 0
    assert store(env).authenticate("ola", new) is not None


def test_create_refuses_weak_taken_and_reserved(env, capsys):
    assert run(env, "create", "ola", "--password-stdin", stdin="password1234\n") == 3
    assert "too easy" in capsys.readouterr().err
    assert run(env, "create", "judge", "--password-stdin", stdin=PASSWORD + "\n") == 3
    run(env, "create", "ola", "--password-stdin", stdin=PASSWORD + "\n")
    assert run(env, "create", "ola", "--password-stdin", stdin=PASSWORD + "\n") == 3


def test_demo_account_is_not_managed_here(env, capsys):
    store(env).seed_demo("Jd7-" + secrets.token_urlsafe(24))
    assert run(env, "disable", "judge") == 3
    assert "JUDGE_PASSWORD" in capsys.readouterr().err
    assert run(env, "reset-password", "judge", "--password-stdin", stdin=PASSWORD) == 3


def test_password_is_never_an_argument():
    parser_help = io.StringIO()
    with pytest.raises(SystemExit):
        import contextlib
        with contextlib.redirect_stdout(parser_help):
            cli.main(["account", "create", "--help"])
    assert "--password-stdin" in parser_help.getvalue()
    assert "--password " not in parser_help.getvalue()


def test_unknown_account_and_missing_database(env, tmp_path, monkeypatch):
    assert run(env, "disable", "nobody") == 3
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "nowhere"))
    assert run(env, "list") == 3
