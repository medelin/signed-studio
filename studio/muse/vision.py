"""Image parts for Muse Spark vision (inline data URLs, user messages only)."""

from __future__ import annotations

import base64
from typing import Any

MAX_IMAGE_BYTES = 50_000_000
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_mime(data: bytes) -> str:
    """MIME type from magic bytes; only formats the Model API accepts."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("unsupported image format (png, jpeg, gif, webp)")


def data_url(data: bytes) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image larger than {MAX_IMAGE_BYTES} bytes")
    return f"data:{sniff_mime(data)};base64,{base64.b64encode(data).decode()}"


def vision_parts(data: bytes) -> dict[str, Any]:
    """Responses API ``input_image`` part."""
    return {"type": "input_image", "image_url": data_url(data)}


def chat_image_part(data: bytes) -> dict[str, Any]:
    """Chat Completions ``image_url`` part."""
    return {"type": "image_url", "image_url": {"url": data_url(data)}}
