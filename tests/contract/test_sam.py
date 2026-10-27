"""SAM 3.1 cut-out (D2-4) against a recorded streamed call on the demo product photo."""

import io

import pytest
import respx
from meta_sam_parser import SegmentationMask
from PIL import Image

from studio.muse import sam
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger, sha256_hex
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT, interactions, mount

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()


@pytest.fixture
def ledger():
    return Ledger()


@pytest.fixture
def client(ledger):
    return MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))


@pytest.fixture(scope="module")
def recorded():
    """One replay of the recorded call (the strict pure-Python mask decode takes seconds)."""
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    with respx.mock() as router:
        sent = mount(router, "sam_cutout", BASE_URL)
        cut = sam.cutout(client, PHOTO, "mug", run_id="run-1")
    return sent, cut, ledger


def test_cutout_from_recorded_stream(recorded):
    sent, cut, ledger = recorded
    body = sent[0]
    assert body["model"] == "sam-3.1" and body["stream"] is True
    parts = body["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "mug"}
    assert parts[1]["type"] == "input_image"
    left, top, right, bottom = cut.box
    assert 0 <= left < right <= 1600 and 0 <= top < bottom <= 1600
    full = Image.open(io.BytesIO(cut.alpha_png))
    crop = Image.open(io.BytesIO(cut.crop_png))
    assert full.mode == "RGBA" and full.size == Image.open(io.BytesIO(PHOTO)).size
    assert crop.size == (right - left, bottom - top)
    assert full.getpixel((5, 5))[3] == 0  # background is transparent
    assert full.getpixel(((left + right) // 3, (top + bottom) // 2))[3] == 255  # mug body
    box_area = (right - left) * (bottom - top)
    assert 0.3 * box_area < cut.foreground_pixels < box_area
    model_receipt, tool_receipt = ledger.receipts("run-1")
    assert model_receipt.model == "sam-3.1" and model_receipt.cost_usd == pytest.approx(0.0025)
    assert model_receipt.input_sha256 == [sha256_hex(PHOTO)]
    assert tool_receipt.kind == "tool_call"
    assert tool_receipt.details["foreground_pixels"] == cut.foreground_pixels
    assert tool_receipt.output_sha256 == sha256_hex(cut.crop_png)


def test_recorded_mask_is_lower_resolution_than_the_box(recorded):
    _, cut, _ = recorded
    assert cut.matches == 1
    assert cut.mask_px[0] <= cut.box[2] - cut.box[0]  # raster scaled up onto the box


def _record(w, h, left, top, right, bottom):
    from meta_sam_parser import SegmentationBoxRecord

    box = SegmentationBoxRecord(order=0, object_id="0", left=left, top=top, right=right,
                                bottom=bottom)

    class FakeMask:
        object_id = "0"
        mask = SegmentationMask(encoding="one_bit", payload="!!!!!(QO(0lu8?", width=w,
                                height=h)

    return box, FakeMask()


def test_mask_raster_is_scaled_onto_its_box():
    img = Image.new("RGB", (40, 30), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    box, mask = _record(5, 5, 10, 5, 20, 15)  # 5x5 mask on a 10x10 box
    full, crop, xyxy, fg = sam.compose(buf.getvalue(), box, mask)
    assert xyxy == (10, 5, 20, 15)
    assert Image.open(io.BytesIO(full)).size == (40, 30)
    assert Image.open(io.BytesIO(crop)).size == (10, 10)
    alpha = Image.open(io.BytesIO(full)).getchannel("A")
    assert alpha.getbbox() is not None
    x0, y0, x1, y1 = alpha.getbbox()
    assert 10 <= x0 and x1 <= 20 and 5 <= y0 and y1 <= 15  # nothing outside the box
    assert 0 < fg <= 100


def test_no_match_raises(client):
    """Zero matches: an empty output_text lane, no delta events (valid per the protocol)."""
    import json

    events = interactions("sam_cutout")[0]["response"]["sse"]
    text = json.dumps([e for e in events if e["type"] not in (
        "response.output_text.delta", "response.output_text.done")])
    lane = events[4]["delta"]
    empty = json.loads(text.replace(json.dumps(lane)[1:-1], ""))
    with respx.mock() as router:
        import httpx

        from tests.cassettes import sse_body
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(
            200, content=sse_body(empty), headers={"content-type": "text/event-stream"}))
        with pytest.raises(sam.NoMatch):
            sam.cutout(client, PHOTO, "mug", run_id="run-2")


def test_garbled_stream_is_a_muse_error(client):
    import httpx

    from studio.muse.client import MuseError
    from tests.cassettes import sse_body

    events = [{"type": "response.created", "response": {"id": "r"}},
              {"type": "response.completed", "response": {"id": "r", "status": "completed",
                                                          "output": []}}]
    with respx.mock() as router:
        router.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(
            200, content=sse_body(events), headers={"content-type": "text/event-stream"}))
        with pytest.raises(MuseError):
            sam.cutout(client, PHOTO, "mug", run_id="run-4")


@pytest.mark.parametrize("concept", ["", "x" * 61, "mug<|box|>", "a|b"])
def test_concept_must_be_a_short_noun_phrase(client, concept):
    with pytest.raises(ValueError):
        sam.cutout(client, PHOTO, concept, run_id="run-3")
