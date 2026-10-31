"""studio.executor's schema: the one-campaign-per-item index is a precondition, not a wish."""

import sqlite3

import pytest

from studio import executor


def test_duplicate_campaign_creates_refuse_the_start():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(executor.SCHEMA)
    for approval in ("ap_1", "ap_2"):  # an older database: one item created twice
        conn.execute("INSERT INTO executions (approval_id, action, item_id, run_id, status, "
                     "created_ts, updated_ts) VALUES (?, 'campaign.create', 'item_1', 'r', "
                     "'done', 0, 0)", (approval,))
    with pytest.raises(executor.SchemaError, match="campaign.create"):
        executor.ensure_schema(conn)


def test_the_index_is_created_on_a_clean_database():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    executor.ensure_schema(conn)
    executor.ensure_schema(conn)  # idempotent
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "executions_campaign_once" in names
