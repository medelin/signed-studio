"""This edition sells no plans: no Stripe state is ever used, and only an operator grants
the ``full`` tier (security review, 3 Nov 2026)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from studio import db
from studio.accounts import Accounts

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("table", db.LEGACY_BILLING_TABLES)
def test_a_database_with_stripe_billing_state_refuses_to_start(tmp_path, table):
    conn = db.connect(tmp_path / "studio.db")
    conn.execute(f"CREATE TABLE {table} (account_id TEXT, livemode INTEGER, "
                 "access_until REAL)")
    conn.execute(f"INSERT INTO {table} VALUES ('acct_0123456789abcdef', 1, 1.0)")  # noqa: S608
    with pytest.raises(db.LegacyBillingState, match="fresh database"):
        db.init(conn)


def test_an_empty_legacy_table_is_harmless(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    conn.execute("CREATE TABLE billing (account_id TEXT)")
    db.init(conn)


def test_the_app_will_not_start_on_a_meta_sandbox_database(tmp_path):
    from studio.app import create_app
    from tests.test_ui_studio import settings

    s = settings(tmp_path)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(s.data_dir / "studio.db")
    conn.execute("CREATE TABLE billing (account_id TEXT, livemode INTEGER, "
                 "access_until REAL)")
    conn.execute("INSERT INTO billing VALUES ('acct_0123456789abcdef', 1, 1.0)")
    conn.close()
    with pytest.raises(db.LegacyBillingState):
        create_app(s)


def test_an_expired_paid_account_cannot_survive_into_this_edition(tmp_path):
    """A base-project database where billing had made an account ``full`` until a past
    ``access_until`` is refused at start instead of serving that account as ``full``."""
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    acct = Accounts(conn).create("paid", "a long enough password 1!")
    conn.execute("CREATE TABLE billing (account_id TEXT PRIMARY KEY, livemode INTEGER, "
                 "access_until REAL, status TEXT)")
    conn.execute("INSERT INTO billing VALUES (?, 1, 1.0, 'canceled')", (acct.id,))
    with pytest.raises(db.LegacyBillingState):
        db.init(conn)


def test_only_operator_commands_write_the_tier():
    writers = []
    for path in (ROOT / "studio").rglob("*.py"):
        text = path.read_text()
        if re.search(r"UPDATE accounts SET[^\"]*\btier\b", text):
            writers.append(path.relative_to(ROOT).as_posix())
    assert writers == ["studio/accounts.py"]
    assert not (ROOT / "studio" / "billing.py").exists()
    assert "access_until" not in "".join(p.read_text() for p in (ROOT / "studio").rglob("*.py")
                                         if p.name != "db.py")
