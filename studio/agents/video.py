"""Video placement: a short 9:16 video cut by code from the run's rendered images.

``muse-wonder-1.0`` takes text and images and returns text (it does not render video), so
it writes the storyboard: which rendered image comes when and for how long (strict JSON,
from the placements and the headline only, no personal data). Code then composes each
image on a 1080×1920 frame (the product's own brand background) and cuts an H.264 MP4
with ffmpeg; the first frame is the poster (``video_9x16.png``), which both reviewers
check like every other creative, and which Meta needs as the video's thumbnail. If the
storyboard call fails, code orders the images itself (two seconds each) and says so.

ffmpeg reads only the PNG frames written here (``-f image2``, a fixed argument list, no
shell), so no input can make it open other files.
"""

from __future__ import annotations

import io
import subprocess  # noqa: S404 - fixed argument list, our own PNG frames only
import tempfile
from collections.abc import Sequence
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict

from studio.muse.client import MuseClient, MuseError
from studio.muse.structured import structured

AGENT = "art_director"
MODEL = "muse-wonder-1.0"
SIZE = (1080, 1920)
POSTER = "video_9x16"
MAX_SHOTS = 6
SHOT_S = (1, 4)  # seconds per shot
FFMPEG_TIMEOUT_S = 120
BACKGROUND = (244, 239, 230)


class Shot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str
    seconds: int


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shots: list[Shot]


class VideoError(Exception):
    pass


def storyboard(client: MuseClient, headline: str, images: Sequence[str], *,
               run_id: str) -> tuple[list[tuple[str, int]], str]:
    """``([(placement, seconds)], model)``; ``"code"`` when the model call failed or its
    answer named no usable image."""
    names = list(dict.fromkeys(images))
    user = (f"Headline: {headline}\nRendered images (use only these names): "
            f"{', '.join(names)}\nOrder them into a 6-12 second vertical video ad: at most "
            f"{MAX_SHOTS} shots, {SHOT_S[0]}-{SHOT_S[1]} seconds each, the most striking "
            "image first.")
    try:
        board, _ = structured(
            client, MODEL,
            [{"role": "system", "content": "You storyboard short vertical video ads from "
                                           "still images. Answer with the shot list only."},
             {"role": "user", "content": user}],
            Storyboard, tier="standard", tier_reason="video storyboard: placement names "
                                                     "and the headline, no personal data",
            run_id=run_id, agent=AGENT, max_tokens=2000, reasoning_effort="minimal")
        shots = [(s.image, max(SHOT_S[0], min(SHOT_S[1], s.seconds)))
                 for s in board.shots if s.image in names][:MAX_SHOTS]
        if shots:
            return shots, MODEL
    except (MuseError, ValueError):
        pass
    return [(n, 2) for n in names][:MAX_SHOTS], "code"


def frame(png: bytes) -> bytes:
    """``png`` fitted into a 1080×1920 frame on the brand background."""
    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail(SIZE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", SIZE, BACKGROUND)
    canvas.paste(im, ((SIZE[0] - im.width) // 2, (SIZE[1] - im.height) // 2))
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def cut(shots: Sequence[tuple[bytes, int]], out_path: Path) -> tuple[bytes, float]:
    """Frames (PNG bytes, seconds) → H.264 MP4 at ``out_path``; returns (poster, seconds)."""
    if not shots:
        raise VideoError("no shots")
    frames = [frame(png) for png, _ in shots]
    with tempfile.TemporaryDirectory() as tmp:
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for i, (data, (_, seconds)) in enumerate(zip(frames, shots, strict=True)):
            path = Path(tmp) / f"f{i}.png"
            path.write_bytes(data)
            args += ["-f", "image2", "-loop", "1", "-t", str(int(seconds)), "-i", str(path)]
        joined = "".join(f"[{i}:v]" for i in range(len(frames)))
        args += ["-filter_complex", f"{joined}concat=n={len(frames)}:v=1:a=0,format=yuv420p",
                 "-r", "30", "-c:v", "libx264", "-movflags", "+faststart", str(out_path)]
        try:
            subprocess.run(args, check=True, timeout=FFMPEG_TIMEOUT_S,  # noqa: S603
                           capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VideoError(f"ffmpeg failed ({type(exc).__name__})") from None
    return frames[0], float(sum(s for _, s in shots))
