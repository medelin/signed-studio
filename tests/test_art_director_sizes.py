"""Day 4 real run: a plan without a 1:1 placement sent `1024x1280` to the Responses
image_generation tool, which accepts only square sizes (`unknown value 1024x1280`, 400).
The first image then goes through /images/edits, which takes every placement size."""

import io
import types

from PIL import Image

from studio.agents import art_director
from studio.muse import image, sam
from studio.receipts import Ledger


def _png(w, h):
    out = io.BytesIO()
    Image.new("RGBA", (w, h), (200, 100, 80, 255)).save(out, "PNG")
    return out.getvalue()


def test_first_non_square_placement_uses_images_edits(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sam, "cutout", lambda *a, **k: types.SimpleNamespace(
        crop_png=_png(40, 40), foreground_pixels=1600))
    monkeypatch.setattr(art_director, "write_scene", lambda *a, **k: "a mug on a desk")

    def fake(kind):
        def call(client, *args, size, **kwargs):
            calls.append((kind, size))
            w, h = (int(x) for x in size.split("x"))
            return image.ImageOut(_png(w // 8, h // 8), w // 8, h // 8,
                                  types.SimpleNamespace(response_id=""))
        return call

    monkeypatch.setattr(image, "respond_image", fake("responses"))
    monkeypatch.setattr(image, "edit", fake("edits"))
    client = types.SimpleNamespace(ledger=Ledger(), unrecorded=frozenset({"sam.check"}))
    out = art_director.run(client, _png(80, 80), "Team mugs", run_id="run_x", concept="mug",
                           data_dir=tmp_path, placements=("feed_4x5", "story_9x16"))
    assert calls == [("edits", "1024x1280"), ("edits", "1080x1920")]
    assert [i.placement for i in out.images] == ["feed_4x5", "story_9x16"]
    calls.clear()
    art_director.run(client, _png(80, 80), "Team mugs", run_id="run_y", concept="mug",
                     data_dir=tmp_path, placements=("feed_1x1", "feed_4x5"))
    assert calls == [("responses", "1024x1024"), ("edits", "1024x1280")]
