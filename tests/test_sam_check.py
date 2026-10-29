"""SAM 3.1 on the generated images (``sam.check``): the silhouette metric, rules S1/S2 in
the reviewer, the read-only receipt, the degraded paths (not recorded, budget, failure),
the mask overlay asset and the 9:16 headline prompt."""

import functools
import io
import json
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from studio import replay
from studio.agents import art_director, reviewer
from studio.agents.schemas import CopyVariant, Finding, Review, SamCheck
from studio.muse import sam
from studio.muse.client import MuseClient
from studio.receipts import Ledger, sha256_hex
from studio.ui import i18n

ROOT = Path(__file__).resolve().parents[1]
PHOTO = ROOT / "fixtures" / "assets" / "product-demo.png"
# the recorded SAM stream of the demo set: "mug" on product-demo.png
MUG_STREAM = ROOT / "fixtures" / "replay" / "blobs" / "53b42d947536e8554220b499f081b944.txt"


def _mask(size, draw):
    im = Image.new("L", size, 0)
    draw(ImageDraw.Draw(im))
    return im


def _ellipse(size, box):
    return _mask(size, lambda d: d.ellipse(box, fill=255))


def _mug(size, x0, y0, s, handle_right=True):
    """A body rectangle with a handle ring on one side, scaled by ``s``."""
    def draw(d):
        d.rectangle((x0, y0, x0 + 60 * s, y0 + 80 * s), fill=255)
        hx = x0 + 60 * s if handle_right else x0 - 25 * s
        d.ellipse((hx, y0 + 20 * s, hx + 25 * s, y0 + 55 * s), fill=255)
    return _mask(size, draw)


# -- the metric --------------------------------------------------------------------------


def test_identical_silhouettes_score_one():
    m = _mug((200, 200), 40, 40, 1)
    assert sam.silhouette_iou(m, m) == (1.0, False)


def test_the_metric_ignores_position_and_scale():
    small = _mug((200, 200), 20, 30, 1)
    big = _mug((1000, 600), 300, 100, 5)
    score, _ = sam.silhouette_iou(small, big)
    assert score >= 0.9


def test_a_mirrored_product_is_the_same_product():
    right = _mug((300, 300), 60, 60, 2, handle_right=True)
    left = _mug((300, 300), 120, 60, 2, handle_right=False)
    score, mirrored = sam.silhouette_iou(right, left)
    assert score >= 0.9 and mirrored is True


def test_a_different_object_scores_low():
    mug = _mug((300, 300), 60, 60, 2)
    stick = _mask((300, 300), lambda d: d.rectangle((140, 10, 150, 290), fill=255))
    score, _ = sam.silhouette_iou(mug, stick)
    assert score < reviewer.IOU_BLOCK


def test_large_images_are_checked_on_a_smaller_copy():
    out = io.BytesIO()
    Image.new("RGB", (1152, 2048), (9, 9, 9)).save(out, "PNG")
    small = Image.open(io.BytesIO(sam.fit(out.getvalue(), 1024)))
    assert small.size == (576, 1024)


def test_empty_masks_score_zero_and_edges_are_named():
    empty = Image.new("L", (50, 50), 0)
    assert sam.silhouette_iou(empty, _ellipse((50, 50), (5, 5, 40, 40))) == (0.0, False)
    assert sam.edges((0, 10, 50, 40), (100, 100)) == ["left"]
    assert sam.edges((10, 10, 100, 100), (100, 100)) == ["right", "bottom"]
    assert sam.edges((10, 10, 90, 90), (100, 100)) == []


# -- rules S1 / S2 -----------------------------------------------------------------------


def _ok(**kw):
    return SamCheck(**{"status": "ok", "matches": 1, "area": 0.2, "box": [0.2, 0.3, 0.8, 0.9],
                       "edges": [], "iou": 0.82, "people": 0, **kw})


def _sev(found):
    return [(f.rule, f.severity) for f in found]


def test_a_good_check_is_one_info_finding_with_the_numbers():
    found = reviewer.sam_findings("feed_1x1", _ok())
    assert _sev(found) == [("S1", "info")]
    assert "IoU 0.82" in found[0].evidence and "20.0 %" in found[0].evidence


@pytest.mark.parametrize("check,expected", [
    (_ok(iou=0.4), [("S1", "warn")]),
    (_ok(iou=0.1), [("S1", "block")]),
    (_ok(matches=0, iou=None), [("S1", "block")]),
    (_ok(area=0.01), [("S1", "warn")]),
    (_ok(area=0.9), [("S1", "warn")]),
    (_ok(edges=["bottom"]), [("S1", "warn")]),
    (_ok(people=2, people_area=0.1), [("S1", "info"), ("S2", "warn")]),
    (SamCheck(status="not_recorded"), [("S1", "info")]),
    (SamCheck(status="skipped"), [("S1", "info")]),
    (SamCheck(status="failed", error="network"), [("S1", "warn")]),
])
def test_findings_by_case(check, expected):
    assert _sev(reviewer.sam_findings("feed_4x5", check)) == expected


def test_area_bounds_follow_the_placement():
    check = _ok(area=0.04)  # fine for a story, too small for the feed
    assert _sev(reviewer.sam_findings("story_9x16", check)) == [("S1", "info")]
    assert _sev(reviewer.sam_findings("feed_1x1", check)) == [("S1", "warn")]
    assert reviewer.sam_findings("feed_1x1", None) == []


def test_s_rules_survive_normalisation_and_raise_the_verdict():
    review = Review(verdict="OK", findings=[], summary="fine")
    out = reviewer.with_findings(review, reviewer.sam_findings("feed_1x1", _ok(iou=0.1)))
    assert out.verdict == "BLOCK" and out.findings[0].rule == "S1"
    model = Review(verdict="WARN", summary="",
                   findings=[Finding(rule="S2 people", evidence="x", severity="warn")])
    assert reviewer.normalize(model).findings[0].rule == "S2"


def test_reviewer_run_joins_sam_findings_and_records_them(monkeypatch):
    ledger = Ledger()
    client = type("C", (), {"ledger": ledger})()
    monkeypatch.setattr(reviewer, "review", lambda *a, **k: Review(
        verdict="OK", findings=[], summary="ok"))
    png = io.BytesIO()
    Image.new("RGB", (1080, 1080), (200, 200, 200)).save(png, "PNG")
    copy = CopyVariant(primary_text="Warm mugs for the desk.", headline="Desk mugs",
                       description="Stoneware", cta="SHOP_NOW")
    result = reviewer.run(client, None, png.getvalue(), copy, run_id="run_s1",
                          placement="feed_1x1", sam_check=_ok(edges=["left"]))
    assert result.verdict == "WARN"
    assert ("S1", "warn") in _sev(result.primary.findings)
    rec = [r for r in ledger.receipts("run_s1") if r.kind == "review"][-1]
    assert rec.details["sam"] == {"status": "ok", "findings": ["S1:warn"]}


# -- sam.check against the recorded SAM stream --------------------------------------------


_parse = functools.lru_cache(maxsize=8)(sam.parse_lane)  # records are immutable


@pytest.fixture(autouse=True)
def _full_size(monkeypatch):
    # the recorded boxes are in the photo's own 1600-px space: check it at that size
    monkeypatch.setattr(sam, "CHECK_SIDE", 2048)
    # the strict mask decode takes seconds; each recorded stream is parsed once
    monkeypatch.setattr(sam, "parse_lane", _parse)


def _client(handler):
    return MuseClient("test-key-not-a-secret", ledger=Ledger(),
                      transport=httpx.MockTransport(handler))


def _stream_handler(bodies, product_text=None, people_text=""):
    stream = MUG_STREAM.read_text()

    def handle(request):
        body = json.loads(request.content)
        bodies.append(body)
        target = (body.get("metadata") or {}).get("target")
        text = (product_text if product_text is not None else stream) if target != "people" \
            else (people_text or _empty_stream(stream))
        return httpx.Response(200, content=text.encode(),
                              headers={"content-type": "text/event-stream"})
    return handle


def _empty_stream(stream):
    """The recorded stream with its output text emptied (SAM found nothing)."""
    out = []
    for block in stream.split("\n\n"):
        if '"response.output_text.delta"' in block:
            continue
        block = re.sub(r'"text":"(?:[^"\\]|\\.)*"', '"text":""', block)
        out.append(block)
    return "\n\n".join(out)


@pytest.fixture(scope="module")
def cut():
    bodies = []
    c = _client(_stream_handler(bodies))
    return sam.cutout(c, PHOTO.read_bytes(), "mug", run_id="run_cut")


def test_check_of_the_photo_itself_matches_its_cutout(cut):
    bodies = []
    client = _client(_stream_handler(bodies))
    png = PHOTO.read_bytes()
    found = sam.check(client, png, cut.crop_png, "mug", run_id="run_chk",
                      placement="feed_1x1")
    assert found.matches == 1 and found.iou is not None and found.iou >= 0.95
    assert 0 < found.area < 1 and found.people == 0
    # the requests: product, then person, both tagged as the check (and downscaled)
    assert [b["input"][0]["content"][0]["text"] for b in bodies] == ["mug", "person"]
    assert {b["metadata"]["purpose"] for b in bodies} == {sam.CHECK_TOOL}
    overlay = Image.open(io.BytesIO(found.overlay_png))
    assert overlay.mode == "RGBA" and max(overlay.size) <= sam.OVERLAY_SIDE
    assert overlay.getchannel("A").getextrema() == (0, sam.PRODUCT_TINT[3])


def test_the_check_receipt_is_read_only_hashes_and_numbers(cut):
    client = _client(_stream_handler([]))
    png = PHOTO.read_bytes()
    found = sam.check(client, png, cut.crop_png, "mug", run_id="run_rec",
                      placement="story_9x16")
    recs = client.ledger.receipts("run_rec")
    tool = [r for r in recs if r.kind == "tool_call"]
    assert len(tool) == 1 and tool[0].id == found.receipt_id
    d = tool[0].details
    assert d["tool"] == "sam.check" and d["side_effects"] == "read" and d["status"] == "ok"
    assert d["placement"] == "story_9x16" and d["silhouette_iou"] == found.iou
    assert tool[0].input_sha256 == [sha256_hex(png), sha256_hex(cut.crop_png)]
    assert tool[0].output_sha256 == sha256_hex(found.overlay_png)
    assert len(d["model_receipts"]) == 2
    text = json.dumps(d)
    assert len(text) < 1000 and "base64" not in text  # numbers, never pixels
    assert sum(r.kind == "model_call" for r in recs) == 2


def test_people_in_the_image_are_counted(cut):
    client = _client(_stream_handler([], people_text=MUG_STREAM.read_text()))
    found = sam.check(client, PHOTO.read_bytes(), cut.crop_png, "mug", run_id="run_ppl",
                      placement="feed_1x1")
    assert found.people == 1 and found.people_area > 0
    assert ("S2", "warn") in _sev(reviewer.sam_findings("feed_1x1", SamCheck(
        status="ok", matches=found.matches, area=found.area, iou=found.iou,
        people=found.people, people_area=found.people_area)))


def test_no_product_found_is_a_block(cut):
    empty = _empty_stream(MUG_STREAM.read_text())
    client = _client(_stream_handler([], product_text=empty))
    found = sam.check(client, PHOTO.read_bytes(), cut.crop_png, "mug", run_id="run_none",
                      placement="feed_1x1")
    assert found.matches == 0 and found.iou is None
    check = SamCheck(status="ok", matches=0, iou=None)
    assert _sev(reviewer.sam_findings("feed_1x1", check)) == [("S1", "block")]


# -- the art director's wrapper: overlay file and degraded paths --------------------------


def test_art_director_writes_the_overlay_next_to_the_image(cut, tmp_path):
    client = _client(_stream_handler([]))
    out_dir = tmp_path / "assets" / "run_ad"
    out_dir.mkdir(parents=True)
    check = art_director.sam_check(client, PHOTO.read_bytes(), cut.crop_png, "mug",
                                   placement="feed_1x1", out_dir=out_dir, data_dir=tmp_path,
                                   run_id="run_ad")
    assert check.status == "ok" and check.overlay == "assets/run_ad/feed_1x1_mask.png"
    assert sha256_hex((tmp_path / check.overlay).read_bytes()) == check.overlay_sha256


@pytest.mark.parametrize("status", ["not_recorded", "skipped", "failed"])
def test_degraded_checks_never_fail_the_run(status, tmp_path):
    def handle(request):
        return httpx.Response(503 if status == "failed" else 200, json={"error": {}})
    client = _client(handle)
    client.retry_delay_s = 0
    if status == "not_recorded":
        client.unrecorded = frozenset({sam.CHECK_TOOL})
    check = art_director.sam_check(
        client, PHOTO.read_bytes(), b"", "mug", placement="feed_4x5", out_dir=tmp_path,
        data_dir=tmp_path, run_id="run_deg",
        can_spend=(lambda usd: False) if status == "skipped" else None)
    assert check.status == status and check.overlay == ""
    tool = [r for r in client.ledger.receipts("run_deg") if r.kind == "tool_call"]
    assert tool[-1].details["status"] == status and tool[-1].details["tool"] == "sam.check"
    if status != "failed":  # not recorded / budget: no SAM call at all
        assert not [r for r in client.ledger.receipts("run_deg") if r.kind == "model_call"]


def test_an_old_replay_set_marks_the_check_not_recorded(tmp_path):
    from studio.routes_studio import replay_clients
    from studio.settings import Settings

    data = replay.load_set("office")
    assert not replay.has_purpose(data, sam.CHECK_TOOL)
    settings = Settings.load({"STUDIO_DATA_DIR": str(tmp_path), "STUDIO_REPLAY": "1",
                              "STUDIO_REPLAY_SET": "office"})
    muse, _ = replay_clients(settings, Ledger())
    assert sam.CHECK_TOOL in muse.unrecorded


def test_the_check_has_its_own_replay_route():
    def req(meta):
        body = {"model": "sam-3.1", "stream": True, "input": [], **meta}
        return httpx.Request("POST", "https://api.meta.ai/v1/responses", json=body)
    cut_key, cut_route, _ = replay.request_key(req({}))
    chk_key, chk_route, _ = replay.request_key(req({"metadata": {"purpose": "sam.check"}}))
    assert cut_route == "POST api.meta.ai /v1/responses sam-3.1 stream "
    assert chk_route == cut_route + " purpose=sam.check" and chk_key != cut_key
    assert replay.has_purpose({"interactions": [{"route": chk_route}]}, "sam.check")


def test_a_check_request_misses_on_a_set_without_it():
    transport = replay.ReplayTransport.from_set("office")
    body = {"model": "sam-3.1", "stream": True, "metadata": {"purpose": "sam.check"},
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "mug"}]}]}
    r = transport.handle_request(httpx.Request("POST", "https://api.meta.ai/v1/responses",
                                               json=body))
    assert r.status_code == 599  # never another call's recording


# -- 9:16 headline prompt, UI ---------------------------------------------------------------


def test_story_prompt_sizes_the_headline_for_a_phone():
    story = art_director.placement_prompt("a desk", "Desk mugs", "story_9x16", first=False)
    feed = art_director.placement_prompt("a desk", "Desk mugs", "feed_1x1", first=True)
    assert "phone" in story and "small," not in story and "14 %" in story
    assert "small, with clear margins" in feed and "phone" not in feed
    for p in ("feed_1x1", "feed_4x5", "story_9x16"):
        text = art_director.placement_prompt("s", "H", p, first=False)
        assert '"H"' in text and "No people, no faces." in text


def test_sam_strings_exist_in_both_languages_with_the_same_placeholders():
    keys = [k for k in i18n.EN if k.startswith("js.sam_")]
    assert len(keys) >= 8
    for k in keys:
        assert k in i18n.PL and i18n.placeholders(i18n.EN[k]) == i18n.placeholders(i18n.PL[k])


def test_overlay_css_cannot_widen_the_page():
    css = (ROOT / "studio" / "ui" / "static" / "style.css").read_text()
    frame = re.search(r"\.mask-frame \{([^}]*)\}", css).group(1)
    overlay = re.search(r"\.carousel \.mask-overlay \{([^}]*)\}", css).group(1)
    assert "max-width: 100%" in frame and "max-width: 100%" in overlay
    assert "position: absolute" in overlay
    js = (ROOT / "studio" / "ui" / "static" / "app.js").read_text()
    assert '_mask.png"' in js and "aria-pressed" in js and "innerHTML" not in js


def test_mask_assets_are_served(tmp_path):
    from studio.routes_studio import ASSET_NAMES

    assert {"feed_1x1_mask", "story_9x16_mask", "card_3_mask"} <= ASSET_NAMES
    assert "cutout_mask" not in ASSET_NAMES and "video_9x16_mask" not in ASSET_NAMES
