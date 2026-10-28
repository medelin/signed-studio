"""Draw the synthetic images of the reviewer evaluation (fixtures/eval/*.SYNTHETIC.png)
from the demo product image: the plain product, a before/after split, a fake play button
and a fake notification UI. Pillow only; deterministic."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "eval"


def _save(im: Image.Image, name: str) -> None:
    out = io.BytesIO()
    im.convert("RGB").quantize(256).save(out, "PNG", optimize=True)
    (OUT / f"{name}.SYNTHETIC.png").write_bytes(out.getvalue())


def main() -> None:
    base = Image.open(ROOT / "fixtures" / "assets" / "product-demo.png").convert("RGB")
    base.thumbnail((512, 512))
    w, h = base.size
    _save(base, "product")

    split = base.copy()
    left = ImageOps.grayscale(base.crop((0, 0, w // 2, h))).convert("RGB")
    split.paste(left, (0, 0))
    d = ImageDraw.Draw(split)
    d.line([(w // 2, 0), (w // 2, h)], fill="white", width=4)
    d.rectangle([10, 10, 110, 40], fill="black")
    d.text((22, 18), "BEFORE", fill="white")
    d.rectangle([w // 2 + 10, 10, w // 2 + 110, 40], fill="black")
    d.text((w // 2 + 28, 18), "AFTER", fill="white")
    _save(split, "before_after")

    play = base.copy()
    d = ImageDraw.Draw(play)
    cx, cy, r = w // 2, h // 2, 60
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0))
    d.polygon([(cx - 20, cy - 32), (cx - 20, cy + 32), (cx + 36, cy)], fill="white")
    d.rectangle([0, h - 30, w, h], fill=(30, 30, 30))
    d.text((10, h - 22), "0:00 / 0:45", fill="white")
    _save(play, "fake_play")

    ui = base.copy()
    d = ImageDraw.Draw(ui)
    d.rounded_rectangle([30, 30, w - 30, 120], radius=14, fill=(245, 245, 245),
                        outline=(180, 180, 180))
    d.ellipse([45, 50, 95, 100], fill=(10, 102, 194))
    d.text((110, 55), "Chat · now", fill=(90, 90, 90))
    d.text((110, 78), "You have 1 new message", fill=(0, 0, 0))
    d.rounded_rectangle([w // 2 - 70, h - 90, w // 2 + 70, h - 50], radius=8,
                        fill=(10, 102, 194))
    d.text((w // 2 - 22, h - 77), "OPEN", fill="white")
    _save(ui, "fake_ui")


if __name__ == "__main__":
    main()
