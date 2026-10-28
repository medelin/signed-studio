"""``/receipts``: every receipt, filter by run and kind, full hashes as tooltips,
copy-as-JSON; visitors without a session get every string redacted."""

import json
import re
import secrets

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.receipts import Receipt
from studio.routes_receipts import receipt_view
from studio.settings import Settings
from tests.forms import csrf

SECRET = secrets.token_urlsafe(32)
PASSWORD = "Jd7-" + secrets.token_urlsafe(24)


@pytest.fixture
def app(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "var"),
                                  "JUDGE_PASSWORD": PASSWORD, "SESSION_SECRET": SECRET,
                                  "COOKIE_SECURE": "1"}))
    led = a.state.ledger
    led.append("run_aaaa", agent="producer", kind="model_call", provider="meta-model-api",
               model="muse-spark-1.3", tier="standard", tier_reason="operator brief",
               tokens_in=120, tokens_out=40, cost_usd=0.0012)
    led.append("run_aaaa", agent="producer", kind="tool_call",
               details={"tool": "run_copywriter", "status": "ok"})
    led.append("run_bbbb", agent="community", kind="model_call", model="muse-spark-1.3")
    return a


def client(app):
    return TestClient(app, base_url="https://testserver")


def test_page_lists_receipts_with_hash_tooltips_and_copyable_json(app):
    with client(app) as c:
        r = c.get("/receipts")
    assert r.status_code == 200
    html = r.text
    for rec in app.state.ledger.receipts():
        assert f'title="hash {rec.hash}' in html  # full hash as a tooltip
        assert rec.id in html
    assert html.count('class="copy-json ') == 3
    assert 'aria-label="Copy receipt' in html
    assert '<script src="/static/receipts.js"' in html
    assert "<script>" not in html  # CSP: no inline script


def test_copied_json_is_the_exported_receipt(app):
    with client(app) as c:
        login = c.post("/login", data={"username": "judge", "password": PASSWORD,
        "csrf": csrf(c)})
        assert login.status_code == 200
        html = c.get("/receipts").text
    blocks = re.findall(r'<pre class="json" id="j-([0-9a-f]+)">(.*?)</pre>', html, re.S)
    assert len(blocks) == 3
    import html as h

    exported = {json.loads(line)["id"]: json.loads(line)
                for line in app.state.ledger.export_jsonl().splitlines()
                if "manifest" not in line}
    for rid, text in blocks:
        assert json.loads(h.unescape(text)) == exported[rid]


def test_filter_by_run_and_kind(app):
    with client(app) as c:
        only_a = c.get("/receipts", params={"run": "run_aaaa"}).text
        tools = c.get("/receipts", params={"kind": "tool_call"}).text
        bad = c.get("/receipts", params={"run": "<script>"})
    assert only_a.count('class="copy-json ') == 2 and "run_bbbb</code></td>" not in only_a
    assert 'option value="run_bbbb"' in only_a  # the run selector lists every run
    assert tools.count('class="copy-json ') == 1
    assert bad.status_code == 200 and "<script>" not in bad.text
    assert "No receipts match" in bad.text


def test_empty_ledger_has_an_empty_state(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v"),
                                  "JUDGE_PASSWORD": PASSWORD, "SESSION_SECRET": SECRET}))
    with client(a) as c:
        html = c.get("/receipts").text
    assert "No receipts yet" in html


def test_visitor_view_redacts_every_string():
    r = Receipt(id="r1", prev="0" * 64, ts=1.0, run_id="run_x", agent="community",
                kind="tool_call", details={"note": "mail jan@example.com or +48 600 100 200",
                                           "nested": [{"who": "anna@example.com"}]})
    visitor = receipt_view(r, full=False)
    text = json.dumps(visitor)
    assert "example.com" not in text and "600 100 200" not in text
    assert "[email]" in text and "[phone]" in text
    assert receipt_view(r, full=True)["details"]["note"].startswith("mail jan@")


def test_navigation_links_the_receipts_page(app):
    with client(app) as c:
        assert 'href="/receipts"' in c.get("/").text


def test_table_scrolls_inside_the_content_width_and_json_is_its_own_row(app):
    with client(app) as c:
        html = c.get("/receipts").text
    assert '<div class="table-scroll" role="region"' in html
    assert html.count('<tr class="receipt-json">') == 3
    css = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "studio" / "ui" / "static" / "style.css").read_text()
    assert ".table-scroll { max-width: 100%; overflow-x: auto;" in css
