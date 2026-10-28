"""Attack suite (day 3 fix round): what the media buyer leaves behind when Meta answers
badly, and how a signed activation reaches every object (ad → ad set → campaign)."""

import pytest

from studio import db
from studio.agents import media_buyer
from studio.approvals import Keys
from studio.meta.adapter import AdapterError
from studio.meta.mock import MockAdapter
from studio.receipts import verify
from tests.approve import approve
from tests.attack.test_paused_only import PNG, RUN, payload


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    assets = tmp_path / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, tmp_path


class NoPreview(MockAdapter):
    def preview(self, ad_id):
        raise AdapterError("Graph API returned no preview", code="bad_response")


def _execution(conn, approval_id):
    return conn.execute("SELECT status FROM executions WHERE approval_id = ?",
                        (approval_id,)).fetchone()[0]


def _verify(conn, ledger, tmp_path):
    export = tmp_path / "receipts.jsonl"
    export.write_text(ledger.export_jsonl())
    return verify(export, ledger.public_key, approver_keys=Keys.registry(conn))


def test_failed_preview_after_five_objects_is_not_fatal(env):
    conn, ledger, data = env
    mock = NoPreview()
    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_np1", p, RUN)
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    assert out["status"] == "PAUSED" and out["preview"] is None
    assert out["preview_error"] == "bad_response"
    (w,) = [r for r in ledger.receipts(RUN) if r.kind == "external_write"]
    assert w.details["approval_id"] == v.pending_id and w.details["status"] == "ok"
    assert media_buyer.campaign(conn, "cmp_np1")["status"] == "PAUSED"
    assert _execution(conn, v.pending_id) == "done"
    assert _verify(conn, ledger, data).ok


def test_write_receipt_and_campaign_row_exist_before_the_preview(env):
    conn, ledger, data = env
    seen = {}

    class Watch(MockAdapter):
        def preview(self, ad_id):
            seen["writes"] = [r for r in ledger.receipts(RUN) if r.kind == "external_write"]
            seen["row"] = media_buyer.campaign(conn, "cmp_np2")
            return super().preview(ad_id)

    p = payload()
    v = approve(conn, ledger, "campaign.create", "cmp_np2", p, RUN)
    media_buyer.create(conn, ledger, Watch(), v, p, data)
    assert len(seen["writes"]) == 1 and seen["row"]["status"] == "PAUSED"
