"""Formats beyond the single image: carousel cards (2-10, 1:1, reviewed like every image),
a 9:16 video cut by code with shots ordered by Muse Wonder (code fallback), copy A/B as
dynamic creative where Meta supports it, otherwise separate ads; the F1 limits for each."""

import io
import json
import shutil
import types

import pytest
from PIL import Image

from studio import db
from studio.agents import art_director, formats, media_buyer, video
from studio.meta import objectives
from studio.meta.adapter import MOCK_PAGE, NotApproved, ads_plan, creative_spec
from studio.meta.mock import MockAdapter
from studio.muse import image, sam
from studio.muse.client import MuseError
from studio.receipts import Ledger, sha256_hex
from tests.approve import approve
from tests.attack.test_paused_only import RUN, payload


def _png(w, h, colour=(200, 100, 80)):
    out = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(out, "PNG")
    return out.getvalue()


def test_card_and_video_limits_come_from_the_guides():
    assert formats.surface("card_3") == "carousel_card"
    f = formats.check("card_1", {"headline": "x" * 21, "primary_text": "y" * 81})
    assert {x.severity for x in f} == {"warn"} and len(f) == 2
    assert formats.check("card_2", {}, (1080, 1080)) == []
    (small,) = formats.check("card_2", {}, (1024, 1024))
    assert small.severity == "warn" and "1080×1080" in small.evidence
    assert formats.check("card_2", {}, (1080, 1350))[0].severity == "block"
    assert formats.check("video_9x16", {}, (1080, 1920)) == []
    assert formats.check_video(8.0, 10_000) == []
    assert formats.check_video(16 * 60, 10)[0].severity == "block"
    assert formats.check_carousel(1) and formats.check_carousel(11)
    assert formats.check_carousel(2) == formats.check_carousel(10) == []


def test_storyboard_falls_back_to_code_when_wonder_fails():
    class Failing:
        ledger = Ledger()

        def chat(self, *a, **k):
            raise MuseError("down", status=503, code="unavailable")

    shots, model = video.storyboard(Failing(), "Hi", ["feed_1x1", "story_9x16"], run_id="r")
    assert model == "code" and shots == [("feed_1x1", 2), ("story_9x16", 2)]


def _ffmpeg():
    return shutil.which("ffmpeg") is not None


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not installed")
def test_video_is_cut_by_code_and_the_poster_is_its_first_frame(tmp_path):
    out = tmp_path / "v.mp4"
    poster, seconds = video.cut([(_png(64, 64), 1), (_png(36, 64, (10, 200, 10)), 2)], out)
    assert seconds == 3.0 and out.stat().st_size > 0
    im = Image.open(io.BytesIO(poster))
    assert im.size == (1080, 1920)


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not installed")
def test_art_director_renders_cards_and_the_video(tmp_path, monkeypatch):
    monkeypatch.setattr(sam, "cutout", lambda *a, **k: types.SimpleNamespace(
        crop_png=_png(40, 40), foreground_pixels=1600))
    monkeypatch.setattr(art_director, "write_scene", lambda *a, **k: "a mug")
    calls = []

    def fake(client, *args, size, **kwargs):
        calls.append(size)
        w, h = (int(x) for x in size.split("x"))
        return image.ImageOut(_png(w // 8, h // 8), w // 8, h // 8,
                              types.SimpleNamespace(response_id=""))

    monkeypatch.setattr(image, "respond_image", fake)
    monkeypatch.setattr(image, "edit", fake)
    monkeypatch.setattr(video, "storyboard", lambda *a, **k: ([("story_9x16", 1),
                                                               ("card_1", 1)], "code"))
    ledger = Ledger()
    client = types.SimpleNamespace(ledger=ledger, unrecorded=frozenset({"sam.check"}))
    out = art_director.run(client, _png(80, 80), "Mugs",
                           run_id="run_v", concept="mug", data_dir=tmp_path,
                           placements=("feed_1x1", "story_9x16"), carousel_cards=3,
                           with_video=True)
    assert out.cards == ["card_1", "card_2", "card_3"]
    assert [i.placement for i in out.images] == ["feed_1x1", "story_9x16", "card_1",
                                                 "card_2", "card_3", "video_9x16"]
    assert all((i.width, i.height) == (1080, 1080) for i in out.images[2:5])
    assert out.video and out.video.seconds == 2.0 and out.video.poster == "video_9x16"
    mp4 = (tmp_path / out.video.path).read_bytes()
    assert sha256_hex(mp4) == out.video.sha256
    (cut,) = [r for r in ledger.receipts("run_v") if r.details.get("tool") == "video.cut"]
    assert cut.output_sha256 == out.video.sha256 and cut.details["side_effects"] == "local"


def _variants(n):
    return [{"primary_text": f"Text {i}", "headline": f"Head {i}", "description": "d",
             "cta": "LEARN_MORE"} for i in range(n)]


def test_copy_ab_is_dynamic_creative_only_where_meta_supports_it():
    sales = objectives.delivery("sales", page_id=MOCK_PAGE, promotion={"pixel_id": "1"})
    traffic = objectives.delivery("traffic", page_id=MOCK_PAGE)
    p = payload(ad_variants=_variants(3))
    assert [(j.key, j.fmt) for j in ads_plan(p, sales)] == [("ad", "dynamic")]
    assert [(j.key, j.fmt, j.variant) for j in ads_plan(p, traffic)] == [
        ("ad", "single", 0), ("ad_2", "single", 1), ("ad_3", "single", 2)]
    body = creative_spec(p, sales, MOCK_PAGE, "h1", job=ads_plan(p, sales)[0])
    feed = body["asset_feed_spec"]
    assert [b["text"] for b in feed["bodies"]] == ["Text 0", "Text 1", "Text 2"]
    assert feed["images"] == [{"hash": "h1"}] and feed["ad_formats"] == ["SINGLE_IMAGE"]
    with_cards = payload(ad_variants=_variants(2), format={"cards": ["card_1", "card_2"],
                                                           "video": None})
    assert [j.fmt for j in ads_plan(with_cards, sales)] == ["single", "single", "carousel"]


def test_carousel_and_video_creatives_need_approved_uploads():
    spec = objectives.delivery("traffic", page_id=MOCK_PAGE)
    p = payload(format={"cards": ["card_1", "card_2"], "video": {
        "sha256": "0" * 64, "seconds": 4.0, "poster": "video_9x16", "storyboard": "code"}})
    jobs = {j.fmt: j for j in ads_plan(p, spec)}
    hashes = {"card_1": "c1", "card_2": "c2", "video_9x16": "vp"}
    carousel = creative_spec(p, spec, MOCK_PAGE, "h", job=jobs["carousel"], hashes=hashes)
    cards = carousel["object_story_spec"]["link_data"]["child_attachments"]
    assert [c["image_hash"] for c in cards] == ["c1", "c2"]
    vid = creative_spec(p, spec, MOCK_PAGE, "h", job=jobs["video"], hashes=hashes,
                        video_id="v1")
    assert vid["object_story_spec"]["video_data"]["video_id"] == "v1"
    assert vid["object_story_spec"]["video_data"]["image_hash"] == "vp"
    with pytest.raises(NotApproved):
        creative_spec(p, spec, MOCK_PAGE, "h", job=jobs["carousel"], hashes={"card_1": "x"})
    with pytest.raises(NotApproved):
        creative_spec(p, spec, MOCK_PAGE, "h", job=jobs["video"], hashes=hashes)


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, tmp_path


def test_media_buyer_creates_every_ad_paused_and_activation_names_them_all(env):
    conn, ledger, data = env
    assets = data / "assets" / RUN
    assets.mkdir(parents=True)
    files = {"feed_1x1": _png(10, 10), "card_1": _png(9, 9), "card_2": _png(8, 8),
             "video_9x16": _png(9, 16)}
    for name, png in files.items():
        (assets / f"{name}.png").write_bytes(png)
    mp4 = b"\x00\x00\x00\x18ftypmp42demo"
    (assets / "video_9x16.mp4").write_bytes(mp4)
    p = payload(images=[{"placement": k, "sha256": sha256_hex(v)} for k, v in files.items()],
                ad_variants=_variants(2),
                format={"cards": ["card_1", "card_2"], "video": {
                    "sha256": sha256_hex(mp4), "seconds": 4.0, "poster": "video_9x16",
                    "storyboard": "code"}})
    v = approve(conn, ledger, "campaign.create", "cmp_fmt1", p, RUN)
    mock = MockAdapter()
    out = media_buyer.create(conn, ledger, mock, v, p, data)
    ads = [o for o in mock.objects.values() if o.get("type") == "ad"]
    assert len(ads) == 4 and all(a["status"] == "PAUSED" for a in ads)
    formats_made = [o["format"] for o in mock.objects.values() if o.get("type") == "creative"]
    assert formats_made == ["single", "single", "carousel", "video"]
    assert [c[0] for c in mock.calls].count("upload_image") == 4
    assert "upload_video" in [c[0] for c in mock.calls]
    assert {"ad_id", "ad_2_id", "ad_3_id", "ad_4_id", "video_id"} <= set(out)
    c = media_buyer.campaign(conn, "cmp_fmt1")
    assert len(c["more_ad_external_ids"]) == 3
    pending, act = media_buyer.request_activation(conn, ledger, "cmp_fmt1", 50)
    assert act["more_ad_external_ids"] == c["more_ad_external_ids"]
    va = approve(conn, ledger, "campaign.activate", "cmp_fmt1", act, RUN)
    media_buyer.activate(conn, ledger, mock, va, act, 50)
    assert all(o["status"] == "ACTIVE" for o in mock.objects.values()
               if o.get("type") in ("ad", "adset", "campaign"))
    steps = [r.details["tool"] for r in ledger.receipts(RUN)
             if r.details.get("tool", "").startswith("activate")]
    assert steps == ["activate_ad"] * 4 + ["activate_adset", "activate_campaign"]
    export = data / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    from studio.approvals import Keys
    from studio.receipts import verify
    assert verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok


def test_a_video_on_disk_that_differs_from_the_signed_one_is_not_uploaded(env):
    conn, ledger, data = env
    assets = data / "assets" / RUN
    assets.mkdir(parents=True)
    (assets / "feed_1x1.png").write_bytes(_png(10, 10))
    (assets / "video_9x16.png").write_bytes(_png(9, 16))
    (assets / "video_9x16.mp4").write_bytes(b"changed")
    p = payload(images=[{"placement": "feed_1x1", "sha256": sha256_hex(_png(10, 10))},
                        {"placement": "video_9x16", "sha256": sha256_hex(_png(9, 16))}],
                format={"cards": [], "video": {"sha256": "0" * 64, "seconds": 2.0,
                                               "poster": "video_9x16", "storyboard": "code"}})
    v = approve(conn, ledger, "campaign.create", "cmp_fmt2", p, RUN)
    mock = MockAdapter()
    with pytest.raises(media_buyer.BuyError):
        media_buyer.create(conn, ledger, mock, v, p, data)
    assert "upload_video" not in [c[0] for c in mock.calls]
    assert json.dumps(mock.calls)  # nothing beyond the images
