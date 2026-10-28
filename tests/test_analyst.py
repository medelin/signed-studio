"""Analyst (D3-5): packer budget, screenshot part, evidence refs, recorded 1M-context call."""

import json

import httpx
import pytest
import respx

from studio import envelope
from studio.agents import analyst
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT, load, mount
from tests.forms import csrf

INSIGHTS = json.loads((ROOT / "fixtures/meta/insights.SYNTHETIC.json").read_text())
SHOT = (ROOT / "fixtures/images/insights-chart.SYNTHETIC.png").read_bytes()
CASSETTE = load("analyst_report")
EXPORT = (ROOT / CASSETTE["ledger"]).read_text()


def _export(n, pad=0):
    return "\n".join(json.dumps({"id": f"{i:032x}", "kind": "model_call", "sig": "ab" * 64,
                                 "details": {"x": "y" * pad}}) for i in range(n))


def test_packer_stays_under_900k_tokens_and_drops_the_oldest():
    big = _export(4000, pad=1000)  # ~4 MB of receipts, far over the budget
    p = analyst.pack(big, INSIGHTS)
    assert p.est_tokens < 900_000 and p.dropped > 0
    assert p.receipt_ids[-1] == f"{3999:032x}"  # the newest receipts are kept
    assert f"{0:032x}" not in p.text and '"sig"' not in p.text
    small = analyst.pack(_export(3), INSIGHTS)
    assert small.dropped == 0 and len(small.receipt_ids) == 3
    assert small.text.count(envelope.BEGIN) == 1  # insights are external data


def test_estimate_is_conservative():
    assert analyst.estimate_tokens("x" * 3000) == 1000


def _fake(report):
    return {"id": "resp_1", "model": "muse-spark-1.3", "status": "completed",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(report)}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 100}}


REPORT = {"summary": "s", "kpis": [{"name": "CTR", "value": "1.4%"}], "wins": ["w"],
          "losses": ["l"], "recommendations": ["a", "b", "c"],
          "evidence_refs": [f"{1:032x}", "made-up-id"]}


def test_screenshot_part_present_and_unknown_refs_are_not_evidence():
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_fake(REPORT)))
        out = analyst.report(client, _export(3), INSIGHTS, SHOT, run_id="an_1")
    body = json.loads(route.calls[0].request.content)
    parts = body["input"][1]["content"]
    assert [p["type"] for p in parts] == ["input_text", "input_text", "input_image",
                                          "input_text"]
    assert parts[2]["image_url"].startswith("data:image/png;base64,")
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}
    assert body["text"]["format"]["type"] == "json_schema" and "tools" not in body
    assert out.report.evidence_refs == [f"{1:032x}"] and out.unverified_refs == ["made-up-id"]
    (r,) = ledger.receipts("an_1")
    assert r.envelope and r.details["envelope_sources"] == ["insights", "screenshot"]
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_fake(REPORT)))
        analyst.report(client, _export(3), INSIGHTS, None, run_id="an_2")
    assert [p["type"] for p in json.loads(route.calls[0].request.content)["input"][1][
        "content"]] == ["input_text"]


def test_budget_cap_refuses_before_sending():
    client = MuseClient(FAKE_KEY, ledger=Ledger(), budget=Budget(5.0))
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_fake(REPORT)))
        with pytest.raises(analyst.AnalystBudget):
            analyst.report(client, _export(2000, pad=600), INSIGHTS, run_id="an_3",
                           max_cost_usd=0.10)
    assert not route.called


def test_recorded_report_reads_the_ledger_and_the_screenshot_in_one_prompt():
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    with respx.mock() as router:
        sent = mount(router, "analyst_report", BASE_URL)
        out = analyst.report(client, EXPORT, INSIGHTS, SHOT, run_id="an_rec",
                             summary=CASSETTE["summary"], max_cost_usd=0.5)
    assert len(sent) == 1 and sent[0]["model"] == "muse-spark-1.3"
    d = out.as_dict()
    assert d["input_tokens"] > 10_000 and d["context_tokens"] == 1_048_576
    assert d["screenshot"] and len(out.report.recommendations) <= 3
    assert out.report.evidence_refs and not out.unverified_refs
    assert set(out.report.evidence_refs) <= set(out.packed.receipt_ids)


def test_report_route_runs_once_and_redacts_for_visitors(tmp_path):
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from tests.test_ui_studio import login, settings

    a = create_app(settings(tmp_path))
    rep = {**REPORT, "summary": "Lead wrote from lead.one@example.com, call 600 100 200"}
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(
                                  lambda req: httpx.Response(200, json=_fake(rep))))
    with TestClient(a, base_url="https://testserver") as c:
        assert c.get("/report").status_code == 200
        assert c.post("/api/reports", json={}).status_code == 401
        login(c)
        assert c.post("/api/reports", content=b"x", headers={
            "content-type": "text/plain", "x-csrf-token": csrf(c)}).status_code == 415
        r = c.post("/api/reports", content=SHOT, headers={"content-type": "image/png"})
        assert r.status_code == 200, r.text
        assert r.json()["screenshot"] is True
        assert "lead.one@example.com" in c.get("/api/reports/latest").text
    with TestClient(a, base_url="https://testserver") as anon:
        latest = anon.get("/api/reports/latest").text + anon.get("/report").text
    assert "lead.one@example.com" not in latest and "600 100 200" not in latest
    assert "Read 1,000 tokens in one prompt" in anon.get("/report").text
    kinds = [x.kind for x in a.state.ledger.receipts() if x.agent == "analyst"]
    assert kinds == ["model_call", "tool_call"]


def _jpeg_with_exif():
    import io

    from PIL import Image
    img = Image.new("RGB", (64, 48), (200, 30, 30))
    exif = Image.Exif()
    exif[0x013B] = "Operator Name Secret"  # Artist
    exif[0x010F] = "PhoneMaker"  # Make
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _png_with_text():
    import io

    from PIL import Image, PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "operator@example.com GPS 50.06,19.94")
    buf = io.BytesIO()
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


def test_screenshot_sits_inside_the_envelope_it_declares():
    client = MuseClient(FAKE_KEY, ledger=Ledger(), budget=Budget(1.0))
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_fake(REPORT)))
        analyst.report(client, _export(3), INSIGHTS, SHOT, run_id="an_4")
    parts = json.loads(route.calls[0].request.content)["input"][1]["content"]
    assert [p["type"] for p in parts] == ["input_text", "input_text", "input_image",
                                          "input_text"]
    assert parts[1]["text"].startswith(envelope.opening("screenshot"))
    assert envelope.END not in parts[1]["text"]  # still open when the image comes
    assert parts[3]["text"] == envelope.END


@pytest.mark.parametrize("shot, secret", [(_jpeg_with_exif(), b"Operator Name Secret"),
                                          (_png_with_text(), b"operator@example.com")])
def test_screenshot_metadata_is_removed_before_the_model(shot, secret):
    import base64
    import io

    from PIL import Image
    assert secret in shot
    client = MuseClient(FAKE_KEY, ledger=Ledger(), budget=Budget(1.0))
    with respx.mock() as router:
        route = router.post(url__startswith=BASE_URL).mock(
            return_value=httpx.Response(200, json=_fake(REPORT)))
        analyst.report(client, _export(3), INSIGHTS, shot, run_id="an_5")
    parts = json.loads(route.calls[0].request.content)["input"][1]["content"]
    url = parts[2]["image_url"]
    assert url.startswith("data:image/png;base64,")
    sent = base64.b64decode(url.split(",", 1)[1])
    assert secret not in sent
    img = Image.open(io.BytesIO(sent))
    assert not img.getexif() and not getattr(img, "text", {})


def test_report_route_tells_a_bad_screenshot_from_an_oversized_context(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from studio.app import create_app
    from tests.test_ui_studio import login, settings

    a = create_app(settings(tmp_path))
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0),
                              transport=httpx.MockTransport(
                                  lambda req: httpx.Response(200, json=_fake(REPORT))))
    with TestClient(a, base_url="https://testserver") as c:
        login(c)
        bad = c.post("/api/reports", content=b"\x89PNG\r\n\x1a\nnot really",
                     headers={"content-type": "image/png"})
        assert bad.status_code == 400 and bad.json()["error"] == "invalid_screenshot"

        def too_big(*args, **kwargs):
            raise analyst.ContextTooLarge("insights and summary alone exceed the budget")

        monkeypatch.setattr(analyst, "report", too_big)
        r = c.post("/api/reports", content=SHOT, headers={"content-type": "image/png"})
        assert r.status_code == 413 and r.json()["error"] == "context_too_large"
