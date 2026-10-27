"""Art director + Muse Image series (D2-5) against the recorded flow on the demo photo."""

import pytest
import respx

from studio.agents import art_director
from studio.agents.schemas import CreativeSet
from studio.muse.client import BASE_URL, MuseClient
from studio.muse.image import png_size
from studio.receipts import Ledger, sha256_hex
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT, mount

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()


@pytest.fixture(scope="module")
def flow(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    with respx.mock() as router:
        sent = mount(router, "image_series", BASE_URL)
        cs = art_director.run(client, PHOTO, "A Mug for Slow Mornings", run_id="run-art",
                              concept="mug", data_dir=data_dir)
    return cs, sent, ledger, data_dir


def test_three_placements_saved_under_the_run(flow):
    cs, _, _, data_dir = flow
    assert isinstance(cs, CreativeSet)
    assert [i.placement for i in cs.images] == ["feed_1x1", "feed_4x5", "story_9x16"]
    assert [i.size for i in cs.images] == ["1024x1024", "1024x1280", "1080x1920"]
    for im in cs.images:
        path = data_dir / im.path
        assert path.parent == data_dir / "assets" / "run-art"
        data = path.read_bytes()
        assert sha256_hex(data) == im.sha256
        assert png_size(data) == (im.width, im.height)
    w, h = cs.images[2].width, cs.images[2].height
    assert h > w  # 9:16 is portrait (the API sets aspect, not exact pixels)
    assert (data_dir / cs.cutout_path).exists() and cs.foreground_pixels > 0


def test_series_is_one_responses_turn_then_edits_with_the_first_image(flow):
    _, sent, _, _ = flow
    sam_call, scene_call, first, *rest = sent
    assert sam_call["model"] == "sam-3.1"
    assert scene_call["model"] == "muse-spark-1.3-contributor"
    assert first["model"] == "muse-image-1.0" and first["store"] is True
    assert first["tools"] == [{"type": "image_generation", "size": "1024x1024",
                               "output_format": "png"}]
    assert "tool_choice" not in first  # image models accept only the image tool
    assert len(rest) == 2
    for body in rest:
        assert len(body["images"]) == 2  # cut-out + first image of the series
        assert "mask" not in body and body["response_format"] == "b64_json"


def test_three_images_cost_three_cents(flow):
    _, _, ledger, _ = flow
    image_receipts = [r for r in ledger.receipts("run-art") if r.model == "muse-image-1.0"]
    assert len(image_receipts) == 3
    assert sum(r.cost_usd for r in image_receipts) == pytest.approx(0.03)
    assert all(r.details.get("units") == 1 for r in image_receipts)


def test_unknown_placement_refused(tmp_path):
    client = MuseClient(FAKE_KEY, ledger=Ledger())
    with pytest.raises(ValueError):
        art_director.run(client, PHOTO, "x", run_id="r", concept="mug", data_dir=tmp_path,
                         placements=("billboard",))
