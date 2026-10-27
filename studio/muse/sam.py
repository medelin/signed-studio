"""SAM 3.1 product cut-out: one noun phrase → box + mask → alpha PNG.

The request is a streamed Responses call (``model="sam-3.1"``, one user message with an
``input_text`` concept and an ``input_image``). The SSE ``output_text`` lane is parsed with
Meta's ``meta_sam_parser`` (image format): each match is a box (source pixels, half-open) and a
mask raster that covers the box, possibly at a lower resolution. The largest match is
scaled onto its box and used as the alpha channel of the source photo.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from meta_sam_parser import (
    CompletedOutcome,
    InvalidSegmentationMaskError,
    ResponsesStreamError,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
)
from PIL import Image

from studio.muse.client import MuseClient, MuseError
from studio.muse.vision import data_url
from studio.receipts import sha256_hex

MODEL = "sam-3.1"
MAX_CONCEPT = 60
_TO_ALPHA = bytes([0, 255] + [255] * 254)  # raster 0/1 -> alpha 0/255


class NoMatch(MuseError):
    """SAM found no instance of the concept in the image."""


@dataclass(frozen=True)
class Cutout:
    alpha_png: bytes  # full-size RGBA, transparent outside the mask
    crop_png: bytes  # RGBA cropped to the box (what the art director sends to Muse Image)
    box: tuple[int, int, int, int]  # left, top, right, bottom (half-open, source pixels)
    mask_px: tuple[int, int]  # mask raster (width, height) as returned
    foreground_pixels: int  # opaque pixels in the full-size alpha
    matches: int
    response_id: str
    receipt_id: str


def parse_lane(text: str) -> list[tuple[SegmentationBoxRecord, SegmentationMaskRecord]]:
    """(box, mask) pairs from the streamed ``output_text`` lane, via ``meta_sam_parser``.

    The client has already assembled the lane from the SSE events; the parser is fed it
    once (its strict pure-Python mask decode costs seconds per pass on a large mask).
    """
    parser = image_segmentation_format().create_parser()
    try:
        parser.push(text, emit=False)
        result = parser.finish(CompletedOutcome()).result
    except (ResponsesStreamError, InvalidSegmentationMaskError, ValueError) as exc:
        raise MuseError(f"unreadable SAM output ({type(exc).__name__})",
                        code="bad_stream") from exc
    boxes = {r.object_id: r for r in result.records if isinstance(r, SegmentationBoxRecord)}
    return [(boxes[r.object_id], r) for r in result.records
            if isinstance(r, SegmentationMaskRecord) and r.object_id in boxes]


def compose(photo_png: bytes, box: SegmentationBoxRecord,
            mask: SegmentationMaskRecord) -> tuple[bytes, bytes, tuple[int, int, int, int], int]:
    """Alpha-composite: the mask raster scaled onto its box becomes the photo's alpha."""
    photo = Image.open(io.BytesIO(photo_png)).convert("RGBA")
    raster = decode_mask_to_raster(mask.mask)
    mw, mh = mask.mask.width, mask.mask.height
    small = Image.frombytes("L", (mw, mh), bytes(raster).translate(_TO_ALPHA))
    left, top = max(0, int(box.left)), max(0, int(box.top))
    right, bottom = min(photo.width, int(box.right)), min(photo.height, int(box.bottom))
    if right <= left or bottom <= top:
        raise NoMatch("SAM box is empty", code="empty_box")
    scaled = small.resize((right - left, bottom - top), Image.Resampling.NEAREST)
    alpha = Image.new("L", photo.size, 0)
    alpha.paste(scaled, (left, top))
    photo.putalpha(alpha)
    foreground = sum(alpha.histogram()[1:])
    full = io.BytesIO()
    photo.save(full, "PNG")
    crop = io.BytesIO()
    photo.crop((left, top, right, bottom)).save(crop, "PNG")
    return full.getvalue(), crop.getvalue(), (left, top, right, bottom), foreground


def cutout(
    client: MuseClient, image_png: bytes, concept: str, *, run_id: str,
    agent: str = "art_director",
) -> Cutout:
    concept = concept.strip()
    if not concept or len(concept) > MAX_CONCEPT or "<" in concept or "|" in concept:
        raise ValueError("concept must be a short noun phrase")
    result = client.respond_stream(
        MODEL,
        [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": data_url(image_png)},
        ]}],
        run_id=run_id, agent=agent, tier_reason="segmentation of the operator's product photo",
    )
    if result.finish != "completed":
        raise MuseError(f"SAM stream ended {result.finish or 'without a response'}",
                        code="bad_stream")
    pairs = parse_lane(result.text)
    if not pairs:
        raise NoMatch(f"no {concept!r} found in the image", code="no_match")
    box, mask = max(pairs, key=lambda p: (p[0].right - p[0].left) * (p[0].bottom - p[0].top))
    full, crop, xyxy, fg = compose(image_png, box, mask)
    client.ledger.append(
        run_id, agent=agent, kind="tool_call", input_sha256=[sha256_hex(image_png)],
        output_sha256=sha256_hex(crop),
        details={"tool": "sam.cutout", "foreground_pixels": fg, "box": list(xyxy),
                 "mask_px": [mask.mask.width, mask.mask.height], "matches": len(pairs),
                 "model_receipt": result.receipt_id},
    )
    return Cutout(full, crop, xyxy, (mask.mask.width, mask.mask.height), fg, len(pairs),
                  result.response_id, result.receipt_id)
