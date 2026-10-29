"""SAM 3.1: the product cut-out from the owner's photo, and the fidelity check of every
generated image.

The request is a streamed Responses call (``model="sam-3.1"``, one user message with an
``input_text`` concept and an ``input_image``). The SSE ``output_text`` lane is parsed with
Meta's ``meta_sam_parser`` (image format): each match is a box (source pixels, half-open) and a
mask raster that covers the box, possibly at a lower resolution. The largest match is
scaled onto its box and used as the alpha channel of the source photo.

``check`` runs SAM again on each image Muse Image drew: the product concept (is it there,
how big, does it touch an edge, does its silhouette match the cut-out) and ``person`` (the
placement prompt forbids people; SAM 3 is prompted by noun phrases, and ``person`` is one
of its most reliable concepts). The fidelity metric is the **silhouette IoU**: each binary
mask is cropped to its own bounding box, scaled (aspect kept) to fit a 64×64 square and
centred; IoU = |A∩B| / |A∪B|, the better of the generated mask as drawn and mirrored (the
product seen from the other side is the same product). 1.0 is the same outline; a
different object scores about 0.3 or less. Check requests carry
``metadata.purpose = "sam.check"``, so a replay set tells them apart from the cut-out.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from meta_sam_parser import (
    CompletedOutcome,
    InvalidSegmentationMaskError,
    ResponsesStreamError,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    image_segmentation_format,
)
from PIL import Image, ImageChops

from studio.muse.client import MuseClient, MuseError
from studio.muse.vision import data_url
from studio.receipts import sha256_hex

MODEL = "sam-3.1"
MAX_CONCEPT = 60
_TO_ALPHA = bytes([0, 255] + [255] * 254)  # raster 0/1 -> alpha 0/255

CHECK_TOOL = "sam.check"
PEOPLE_CONCEPT = "person"
CHECK_SIDE = 768  # generated images are checked on a copy at most this large
NORM = 64  # side of the square the silhouettes are compared on
EDGE = 0.005  # a box within 0.5 % of an image side touches it
MIN_PERSON_AREA = 0.002  # smaller "person" matches are ignored as noise
OVERLAY_SIDE = 512
PRODUCT_TINT = (0, 132, 255, 120)
PEOPLE_TINT = (230, 40, 40, 150)
CALLS_PER_CHECK = 2  # product + person


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


def _concept(concept: str) -> str:
    concept = concept.strip()
    if not concept or len(concept) > MAX_CONCEPT or "<" in concept or "|" in concept:
        raise ValueError("concept must be a short noun phrase")
    return concept


def _segment(
    client: MuseClient, image_png: bytes, concept: str, *, run_id: str, agent: str,
    tier_reason: str, metadata: dict[str, str] | None = None,
) -> tuple[list[tuple[SegmentationBoxRecord, SegmentationMaskRecord]], str, str]:
    """``(pairs, response id, model receipt id)`` of one streamed SAM call."""
    result = client.respond_stream(
        MODEL,
        [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": concept},
            {"type": "input_image", "image_url": data_url(image_png)},
        ]}],
        run_id=run_id, agent=agent, tier_reason=tier_reason, metadata=metadata,
    )
    if result.finish != "completed":
        raise MuseError(f"SAM stream ended {result.finish or 'without a response'}",
                        code="bad_stream")
    return parse_lane(result.text), result.response_id, result.receipt_id


def cutout(
    client: MuseClient, image_png: bytes, concept: str, *, run_id: str,
    agent: str = "art_director",
) -> Cutout:
    concept = _concept(concept)
    pairs, response_id, receipt_id = _segment(
        client, image_png, concept, run_id=run_id, agent=agent,
        tier_reason="segmentation of the operator's product photo")
    if not pairs:
        raise NoMatch(f"no {concept!r} found in the image", code="no_match")
    box, mask = max(pairs, key=lambda p: (p[0].right - p[0].left) * (p[0].bottom - p[0].top))
    full, crop, xyxy, fg = compose(image_png, box, mask)
    client.ledger.append(
        run_id, agent=agent, kind="tool_call", input_sha256=[sha256_hex(image_png)],
        output_sha256=sha256_hex(crop),
        details={"tool": "sam.cutout", "foreground_pixels": fg, "box": list(xyxy),
                 "mask_px": [mask.mask.width, mask.mask.height], "matches": len(pairs),
                 "model_receipt": receipt_id},
    )
    return Cutout(full, crop, xyxy, (mask.mask.width, mask.mask.height), fg, len(pairs),
                  response_id, receipt_id)


# -- fidelity check of a generated image ------------------------------------------------


def mask_image(size: tuple[int, int], box: SegmentationBoxRecord,
               mask: SegmentationMaskRecord) -> Image.Image:
    """One match as a full-size ``L`` mask (0 / 255): its raster scaled onto its box."""
    raster = decode_mask_to_raster(mask.mask)
    small = Image.frombytes("L", (mask.mask.width, mask.mask.height),
                            bytes(raster).translate(_TO_ALPHA))
    w, h = size
    left, top = max(0, int(box.left)), max(0, int(box.top))
    right, bottom = min(w, int(box.right)), min(h, int(box.bottom))
    out = Image.new("L", size, 0)
    if right > left and bottom > top:
        out.paste(small.resize((right - left, bottom - top), Image.Resampling.NEAREST),
                  (left, top))
    return out


def count(mask: Image.Image) -> int:
    """Pixels of an ``L`` mask at or above half intensity."""
    return sum(mask.histogram()[128:])


def _binary(mask: Image.Image) -> Image.Image:
    return mask.convert("L").point(lambda v: 255 if v >= 128 else 0)


def silhouette(mask: Image.Image, n: int = NORM) -> Image.Image | None:
    """The mask cropped to its bounding box, scaled (aspect kept) to fit an n×n square,
    centred and thresholded; ``None`` if it is empty."""
    binary = _binary(mask)
    bbox = binary.getbbox()
    if bbox is None:
        return None
    crop = binary.crop(bbox)
    scale = n / max(crop.size)
    w, h = max(1, round(crop.width * scale)), max(1, round(crop.height * scale))
    fitted = _binary(crop.resize((w, h), Image.Resampling.BILINEAR))
    square = Image.new("L", (n, n), 0)
    square.paste(fitted, ((n - w) // 2, (n - h) // 2))
    return square


def iou(a: Image.Image, b: Image.Image) -> float:
    """|A∩B| / |A∪B| of two same-size binary masks (0.0 when both are empty)."""
    union = count(ImageChops.lighter(a, b))
    return count(ImageChops.darker(a, b)) / union if union else 0.0


def silhouette_iou(reference: Image.Image, generated: Image.Image) -> tuple[float, bool]:
    """``(IoU, mirrored)``: the better of the generated silhouette as drawn and mirrored."""
    a, b = silhouette(reference), silhouette(generated)
    if a is None or b is None:
        return 0.0, False
    straight = iou(a, b)
    flipped = iou(a, b.transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    if flipped > straight:
        return round(flipped, 3), True
    return round(straight, 3), False


def fit(png: bytes, side: int | None = None) -> bytes:
    """The image as sent to the check: at most ``side`` (``CHECK_SIDE``) px on its longer
    side."""
    side = side or CHECK_SIDE
    im = Image.open(io.BytesIO(png))
    if max(im.size) <= side:
        return png
    rgb = im.convert("RGB")
    rgb.thumbnail((side, side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    rgb.save(out, "PNG")
    return out.getvalue()


def edges(bbox: tuple[int, int, int, int], size: tuple[int, int]) -> list[str]:
    """The image sides a box touches (within ``EDGE`` of the side)."""
    left, top, right, bottom = bbox
    w, h = size
    tx, ty = max(1, round(w * EDGE)), max(1, round(h * EDGE))
    hits = (("left", left <= tx), ("top", top <= ty), ("right", right >= w - tx),
            ("bottom", bottom >= h - ty))
    return [side for side, hit in hits if hit]


def overlay_png(size: tuple[int, int], product: Image.Image | None,
                people: list[Image.Image]) -> bytes:
    """RGBA PNG, transparent except the product (blue) and people (red) masks; at most
    ``OVERLAY_SIDE`` px (the page stretches it over the image)."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    tinted = [(product, PRODUCT_TINT)] if product is not None else []
    for mask, tint in tinted + [(p, PEOPLE_TINT) for p in people]:
        layer.paste(Image.new("RGBA", size, tint), (0, 0), _binary(mask))
    layer.thumbnail((OVERLAY_SIDE, OVERLAY_SIDE), Image.Resampling.NEAREST)
    out = io.BytesIO()
    layer.save(out, "PNG", optimize=True)
    return out.getvalue()


@dataclass(frozen=True)
class Check:
    """What SAM saw in one generated image (fractions are of the checked image)."""

    matches: int
    area: float  # product mask pixels / image pixels
    box: tuple[float, float, float, float]  # product mask bbox, fractions of width/height
    edges: list[str]  # image sides the product touches
    iou: float | None  # silhouette IoU vs the cut-out (None: no product found)
    mirrored: bool
    people: int | None  # person matches (None: not checked)
    people_area: float
    overlay_png: bytes = field(repr=False)
    receipt_id: str


def check(
    client: MuseClient, image_png: bytes, cutout_png: bytes, concept: str, *, run_id: str,
    placement: str, agent: str = "art_director", people: bool = True,
) -> Check:
    """SAM on a generated image: the product (largest match) against the cut-out's
    silhouette (alpha of ``cutout_png``), then ``person``. One read-only ``tool_call``
    receipt with hashes and numbers, never pixels. ``MuseError`` from SAM propagates."""
    concept = _concept(concept)
    png = fit(image_png)
    size = Image.open(io.BytesIO(png)).size
    meta = {"purpose": CHECK_TOOL, "placement": placement[:32]}
    pairs, _, rid_product = _segment(
        client, png, concept, run_id=run_id, agent=agent, metadata={**meta, "target": "product"},
        tier_reason="product fidelity check of a generated image")
    product = max((mask_image(size, b, m) for b, m in pairs), key=count, default=None)
    rids = [rid_product]
    people_masks: list[Image.Image] = []
    if people:
        found, _, rid_people = _segment(
            client, png, PEOPLE_CONCEPT, run_id=run_id, agent=agent,
            metadata={**meta, "target": "people"},
            tier_reason="no-people check of a generated image")
        rids.append(rid_people)
        min_px = MIN_PERSON_AREA * size[0] * size[1]
        people_masks = [m for m in (mask_image(size, b, mm) for b, mm in found)
                        if count(m) >= min_px]
    total = size[0] * size[1]
    area, score, mirrored = 0.0, None, False
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    sides: list[str] = []
    if product is not None and (bbox := product.getbbox()) is not None:
        area = round(count(product) / total, 4)
        ref = Image.open(io.BytesIO(cutout_png)).convert("RGBA").getchannel("A")
        score, mirrored = silhouette_iou(ref, product)
        box = (round(bbox[0] / size[0], 3), round(bbox[1] / size[1], 3),
               round(bbox[2] / size[0], 3), round(bbox[3] / size[1], 3))
        sides = edges(bbox, size)
    else:
        product = None
    people_area = round(sum(count(m) for m in people_masks) / total, 4)
    overlay = overlay_png(size, product, people_masks)
    rec = client.ledger.append(
        run_id, agent=agent, kind="tool_call",
        input_sha256=[sha256_hex(image_png), sha256_hex(cutout_png)],
        output_sha256=sha256_hex(overlay),
        details={"tool": CHECK_TOOL, "status": "ok", "side_effects": "read",
                 "placement": placement, "matches": len(pairs), "area": area,
                 "box": list(box), "edges": sides, "silhouette_iou": score,
                 "mirrored": mirrored, "people": len(people_masks) if people else None,
                 "people_area": people_area, "checked_px": list(size),
                 "model_receipts": rids},
    )
    return Check(len(pairs), area, box, sides, score, mirrored,
                 len(people_masks) if people else None, people_area, overlay, rec.id)


def check_not_run(client: MuseClient, image_png: bytes, *, run_id: str, placement: str,
                  status: str, agent: str = "art_director", error: str = "") -> None:
    """The receipt of a check that did not run (``not_recorded`` | ``skipped`` |
    ``failed``): the image hash and why, so the export shows the gap."""
    client.ledger.append(
        run_id, agent=agent, kind="tool_call", input_sha256=[sha256_hex(image_png)],
        details={"tool": CHECK_TOOL, "status": status, "side_effects": "read",
                 "placement": placement, **({"error": error[:64]} if error else {})})
