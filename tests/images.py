"""Test helper: a solid PNG that meets the placement's format rule (F1) sizes."""

import io

from PIL import Image

SIZES = {"feed_1x1": (600, 600), "feed_4x5": (600, 750), "story_9x16": (504, 896)}


def placement_png(placement: str = "feed_1x1", color=(200, 60, 40)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", SIZES[placement], color).save(out, "PNG")
    return out.getvalue()
