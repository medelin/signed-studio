"""Art director: product photo → SAM 3.1 cut-out → one Muse Image creative per placement.

1. ``sam.cutout`` isolates the product (noun phrase from settings, e.g. "mug").
2. Spark Contributor writes one scene prompt (strict JSON; no PII in the input, so the
   cheap tier, decided by ``pii.choose_tier``).
3. The first placement is a Responses API turn on Muse Image with the cut-out attached
   (``store=true``: its response id anchors the series); every further placement is an
   ``/images/edits`` call given the cut-out *and* the first image, so the product, palette
   and light stay the same across 1:1, 4:5 and 9:16.

Optional formats from a confirmed Brief v1: ``carousel_cards`` (2-10) more renders as the
cards of a carousel ad (``card_1`` …, each an ``/images/edits`` call with the cut-out and
the first image, resized by code to 1080×1080), and ``video``: a 9:16 video cut by code
from the renders, its shots ordered by ``muse-wonder-1.0`` (``video.py``), with the
poster frame (``video_9x16``) as one more image the reviewers check.

Files go to ``<data_dir>/assets/<run_id>/``; every image is a receipt priced at $0.01.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from studio.agents import prompts, video
from studio.agents.schemas import PLACEMENT_SIZES, CreativeImage, CreativeSet, VideoClip
from studio.muse import image, sam
from studio.muse.client import MuseClient
from studio.muse.structured import structured
from studio.pii import choose_tier
from studio.receipts import is_opaque, sha256_hex

AGENT = "art_director"
DEFAULT_PLACEMENTS = ("feed_1x1", "feed_4x5", "story_9x16")
FLATTEN_BG = (244, 239, 230)  # brand off-white
IMAGE_COST_USD = 0.01  # Muse Image price per generated image (see muse.image)
# The Responses image_generation tool takes only these sizes (a real run got
# "unknown value `1024x1280`"); other first placements go through /images/edits.
RESPONSES_SIZES = frozenset({"1024x1024"})
CARD_SIZE = "1024x1024"
CARD_PX = 1080  # the carousel guide's recommended card size


def card_prompt(scene: str, headline: str, i: int, n: int) -> str:
    return (placement_prompt(scene, headline, "feed_1x1", first=False)
            + f"\nThis is card {i} of {n} of a carousel: show the product from a different "
              "angle or in a different detail than the other cards; same palette and light.")


def _square(png: bytes, px: int = CARD_PX) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    if im.size == (px, px):
        return png
    out = io.BytesIO()
    im.resize((px, px), Image.Resampling.LANCZOS).save(out, "PNG")
    return out.getvalue()


class CostCapReached(Exception):
    """The run's budget would be exceeded by the next image; nothing more is rendered."""


def clean_placements(placements: Sequence[str]) -> tuple[str, ...]:
    """Known placements, first occurrence only, at most one per format."""
    known = [p for p in dict.fromkeys(placements) if p in PLACEMENT_SIZES]
    return tuple(known[:len(PLACEMENT_SIZES)])


class ScenePrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene: str


def _flatten(rgba_png: bytes) -> bytes:
    """Cut-out on the brand off-white (an opaque input is the safest edit input)."""
    im = Image.open(io.BytesIO(rgba_png)).convert("RGBA")
    bg = Image.new("RGBA", im.size, FLATTEN_BG + (255,))
    bg.alpha_composite(im)
    out = io.BytesIO()
    bg.convert("RGB").save(out, "PNG")
    return out.getvalue()


def write_scene(client: MuseClient, headline: str, *, run_id: str) -> str:
    user = f"Headline: {headline}\nWrite the scene for the product ad image."
    tier, reason, model = choose_tier(user, "contributor")
    obj, _ = structured(
        client, model,
        [{"role": "system", "content": prompts.system(AGENT)},
         {"role": "user", "content": user}],
        ScenePrompt, tier=tier, tier_reason=reason, run_id=run_id, agent=AGENT,
        max_tokens=2000, reasoning_effort="minimal",
    )
    return obj.scene.strip()[:1200]


def placement_prompt(scene: str, headline: str, placement: str, first: bool) -> str:
    keep = ("The first image is the product cut-out: keep the product exactly as it is "
            "(shape, glaze, handle, proportions).")
    if not first:
        keep += (" The second image is the first ad of this series: match its scene, "
                 "palette, light and typography, recomposed for this format.")
    return (f"{keep}\nScene: {scene}\nFormat: {placement} ({PLACEMENT_SIZES[placement]}). "
            f'Text in the image: only the headline "{headline}", small, with clear margins. '
            "No people, no faces, no other logos.")


def run(
    client: MuseClient,
    photo_png: bytes,
    headline: str,
    *,
    run_id: str,
    concept: str,
    data_dir: Path,
    placements: tuple[str, ...] = DEFAULT_PLACEMENTS,
    can_spend: Callable[[float], bool] | None = None,
    carousel_cards: int = 0,
    with_video: bool = False,
) -> CreativeSet:
    """``can_spend(usd)`` is asked before every image; ``False`` raises ``CostCapReached``."""
    if not is_opaque(run_id):
        raise ValueError("run_id must be an opaque id")
    unknown = [p for p in placements if p not in PLACEMENT_SIZES]
    if unknown or not placements:
        raise ValueError(f"unknown placements: {unknown}")
    placements = clean_placements(placements)
    out_dir = data_dir / "assets" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cut = sam.cutout(client, photo_png, concept, run_id=run_id, agent=AGENT)
    cutout_path = out_dir / "cutout.png"
    cutout_path.write_bytes(cut.crop_png)
    product = _flatten(cut.crop_png)

    scene = write_scene(client, headline, run_id=run_id)
    images: list[CreativeImage] = []
    used_prompts: list[str] = []
    first_png: bytes | None = None
    for i, placement in enumerate(placements):
        if can_spend is not None and not can_spend(IMAGE_COST_USD):
            raise CostCapReached(f"stopped before {placement}: run budget reached")
        size = PLACEMENT_SIZES[placement]
        text = placement_prompt(scene, headline, placement, first=i == 0)
        if first_png is None and size not in RESPONSES_SIZES:
            out = image.edit(client, [product], text, size=size, run_id=run_id, agent=AGENT)
            first_png = out.png
        elif first_png is None:
            out = image.respond_image(client, text, [product], size=size, run_id=run_id,
                                      agent=AGENT)
            first_png = out.png
        else:
            out = image.edit(client, [product, first_png], text, size=size, run_id=run_id,
                             agent=AGENT)
        path = out_dir / f"{placement}.png"
        path.write_bytes(out.png)
        used_prompts.append(text)
        images.append(CreativeImage(
            placement=placement, size=size, path=str(path.relative_to(data_dir)),
            sha256=sha256_hex(out.png), width=out.width, height=out.height,
            response_id=out.result.response_id,
        ))
    assert first_png is not None
    cards: list[str] = []
    for i in range(1, carousel_cards + 1 if carousel_cards >= 2 else 1):
        if can_spend is not None and not can_spend(IMAGE_COST_USD):
            raise CostCapReached(f"stopped before card {i}: run budget reached")
        text = card_prompt(scene, headline, i, carousel_cards)
        out = image.edit(client, [product, first_png], text, size=CARD_SIZE, run_id=run_id,
                         agent=AGENT)
        png = _square(out.png)
        name = f"card_{i}"
        path = out_dir / f"{name}.png"
        path.write_bytes(png)
        used_prompts.append(text)
        cards.append(name)
        images.append(CreativeImage(
            placement=name, size=f"{CARD_PX}x{CARD_PX}", path=str(path.relative_to(data_dir)),
            sha256=sha256_hex(png), width=CARD_PX, height=CARD_PX,
            response_id=out.result.response_id))
    clip = None
    if with_video:
        clip = _video(client, headline, images, out_dir, data_dir, run_id)
        images.append(clip[1])
    return CreativeSet(
        cutout_path=str(cutout_path.relative_to(data_dir)), cutout_sha256=sha256_hex(
            cut.crop_png), foreground_pixels=cut.foreground_pixels, images=images,
        prompts=used_prompts, response_ids=[im.response_id for im in images],
        cards=cards, video=clip[0] if clip else None,
    )


def _video(client: MuseClient, headline: str, images: list[CreativeImage], out_dir: Path,
           data_dir: Path, run_id: str) -> tuple[VideoClip, CreativeImage]:
    """Storyboard (Wonder, or code) → MP4 + poster frame from the rendered images."""
    by_name = {im.placement: im for im in images}
    shots, model = video.storyboard(client, headline, list(by_name), run_id=run_id)
    frames = [((data_dir / by_name[n].path).read_bytes(), sec) for n, sec in shots]
    mp4 = out_dir / f"{video.POSTER}.mp4"
    poster, seconds = video.cut(frames, mp4)
    poster_path = out_dir / f"{video.POSTER}.png"
    poster_path.write_bytes(poster)
    data = mp4.read_bytes()
    client.ledger.append(run_id, agent=AGENT, kind="tool_call", output_sha256=sha256_hex(data),
                         input_sha256=[sha256_hex(f) for f, _ in frames],
                         details={"tool": "video.cut", "status": "ok", "side_effects": "local",
                                  "seconds": seconds, "shots": len(shots),
                                  "storyboard": model})
    clip = VideoClip(path=str(mp4.relative_to(data_dir)), sha256=sha256_hex(data),
                     seconds=seconds, bytes=len(data), poster=video.POSTER, storyboard=model)
    w, h = video.SIZE
    return clip, CreativeImage(placement=video.POSTER, size=f"{w}x{h}",
                               path=str(poster_path.relative_to(data_dir)),
                               sha256=sha256_hex(poster), width=w, height=h, response_id="")
