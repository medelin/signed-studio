"""Muse Image 1.0: text-to-image (``/images/generations``) and image edits (``/images/edits``).

Every call is a ``model_call`` receipt priced per generated image ($0.01). ``size`` sets the
aspect ratio only; the generator picks the pixel dimensions, which are read back from the
PNG. ``mask`` is not supported by the API and never sent.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from studio.muse.client import MuseClient, MuseError, MuseResult
from studio.muse.vision import data_url

MODEL = "muse-image-1.0"


@dataclass(frozen=True)
class ImageOut:
    png: bytes
    width: int
    height: int
    result: MuseResult


def png_size(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _one(result: MuseResult) -> ImageOut:
    if not result.images:
        raise MuseError("Muse Image returned no image", code="no_image")
    png = result.images[0].data
    width, height = png_size(png)
    return ImageOut(png, width, height, result)


def generate(
    client: MuseClient, prompt: str, *, size: str, run_id: str, agent: str
) -> ImageOut:
    body = {"model": MODEL, "prompt": prompt, "n": 1, "size": size, "output_format": "png"}
    return _one(client.images("generations", body, run_id=run_id, agent=agent))


def edit(
    client: MuseClient,
    images: list[bytes],
    prompt: str,
    *,
    size: str,
    run_id: str,
    agent: str,
) -> ImageOut:
    """Image-to-image with 1-10 input images (JSON ``images[].image_url`` data URLs)."""
    if not 1 <= len(images) <= 10:
        raise ValueError("edits take 1-10 input images")
    body = {"model": MODEL, "prompt": prompt, "n": 1, "size": size, "output_format": "png",
            "images": [{"image_url": data_url(i)} for i in images]}
    return _one(client.images("edits", body, run_id=run_id, agent=agent))


def respond_image(
    client: MuseClient,
    prompt: str,
    images: list[bytes],
    *,
    size: str,
    run_id: str,
    agent: str,
    previous_response_id: str | None = None,
) -> ImageOut:
    """Responses API turn on Muse Image (``image_generation`` tool, ``store=true``), so a
    later turn can refine it with ``previous_response_id``."""
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    content += [{"type": "input_image", "image_url": data_url(i)} for i in images]
    result = client.respond(
        MODEL, [{"role": "user", "content": content}], run_id=run_id, agent=agent,
        tools=[{"type": "image_generation", "size": size, "output_format": "png"}],
        previous_response_id=previous_response_id, store=True,
    )
    return _one(result)
