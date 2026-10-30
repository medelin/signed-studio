"""The owner's own video clip (shot on a phone) as the ad's video.

A small seller has footage: the workshop, the product in hand, themselves talking. The
studio takes one clip, from the brief page (``POST /api/clips``) or as a WhatsApp video
message, and makes it the campaign's 9:16 video instead of a video cut from rendered
images:

- **Checked first**: ffprobe reads the upload (only the ``file`` protocol and container
  demuxers, ``FORMATS``: never a playlist, concat list or image sequence that could pull in
  other local files; a timeout, no network); it must have a video stream, last at most
  ``MAX_INPUT_S`` and weigh at most ``MAX_BYTES``.
- **Normalised by code**: ffmpeg (fixed argument list, no shell) crops it to 1080×1920,
  keeps the first ``MAX_S`` seconds at 30 fps, H.264 + AAC with ``faststart``; the frame at
  ``POSTER_AT_S`` is the poster (``video_9x16``), which both reviewers check like every
  other creative and Meta needs as the thumbnail. Phone rotation is applied by ffmpeg.
- **Owned**: a clip belongs to the account that sent it (``clips.account_id``) and is
  used by at most one brief (``clips.brief_id``); a WhatsApp clip without a caption waits
  for that account's next brief for ``ATTACH_WINDOW_S``.

The signed ``campaign.create`` payload carries the clip's sha256 like every image, so
what is uploaded to Meta after the signature is exactly the clip the owner approved.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import sqlite3
import subprocess  # noqa: S404 - fixed argument lists, files we wrote
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from studio.approvals import transaction

MAX_BYTES = 64 * 1024 * 1024
MAX_INPUT_S = 180.0
MAX_S = 15.0
POSTER_AT_S = 0.6
SIZE = (1080, 1920)
TIMEOUT_S = 180
ATTACH_WINDOW_S = 24 * 3600
# container demuxers only: a playlist (hls), concat list or image sequence uploaded as
# "video" could make ffmpeg open other local files through the file protocol
FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm"
TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
         "video/3gpp": ".3gp", "video/x-matroska": ".mkv"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL CHECK (source IN ('upload', 'whatsapp')),
    path TEXT NOT NULL,          -- the normalised MP4
    poster_path TEXT NOT NULL,   -- its 1080x1920 PNG poster
    sha256 TEXT NOT NULL,
    seconds REAL NOT NULL,
    brief_id TEXT NOT NULL DEFAULT '',  -- the brief it was used for ('' = waiting)
    created_ts REAL NOT NULL
);
"""


class ClipError(Exception):
    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Clip:
    id: str
    account_id: str
    path: Path
    poster_path: Path
    sha256: str
    seconds: float
    brief_id: str = ""


def may_add(account: object, meta_adapter: str) -> bool:
    """Whether ``account`` may add a clip (from the brief page or WhatsApp). On a live
    adapter the clip goes to the operator's ad account: full accounts only, never the
    shared judge account or a trial account. One rule for every way in."""
    if account is None:
        return False
    if meta_adapter == "mock":
        return True
    return not getattr(account, "is_demo", True) and getattr(account, "tier", "") == "full"


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _run(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, capture_output=True, timeout=TIMEOUT_S, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired):
        raise ClipError("video_unreadable") from None


def probe(path: Path) -> tuple[float, bool]:
    """``(duration, has_audio)``; ``ClipError`` without a video stream."""
    if shutil.which("ffprobe") is None:
        raise ClipError("ffmpeg_missing", 503)
    out = _run(["ffprobe", "-v", "error", "-protocol_whitelist", "file",
                "-format_whitelist", FORMATS,
                "-show_entries", "format=duration,format_name:stream=codec_type",
                "-of", "json", str(path)])
    try:
        data = json.loads(out.stdout or b"{}")
    except ValueError:
        raise ClipError("video_unreadable") from None
    fmt = str((data.get("format") or {}).get("format_name") or "")
    if not fmt or not set(fmt.split(",")) <= set(FORMATS.split(",")):
        raise ClipError("video_unreadable")
    kinds = {s.get("codec_type") for s in data.get("streams") or [] if isinstance(s, dict)}
    if "video" not in kinds:
        raise ClipError("no_video_stream")
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not 0.5 <= duration <= MAX_INPUT_S:
        raise ClipError("video_length")
    return duration, "audio" in kinds


def normalize(data: bytes, out_dir: Path) -> tuple[Path, Path, str, float]:
    """The upload → ``(clip.mp4, poster.png, sha256, seconds)`` in ``out_dir``."""
    if not data:
        raise ClipError("empty_video")
    if len(data) > MAX_BYTES:
        raise ClipError("video_too_large", 413)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in"
        src.write_bytes(data)
        duration, audio = probe(src)
        mp4, poster = out_dir / "clip.mp4", out_dir / "poster.png"
        w, h = SIZE
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file",
                "-format_whitelist", FORMATS, "-i", str(src), "-t", f"{MAX_S}",
                "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                       f"crop={w}:{h},fps=30,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
        args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"] if audio else ["-an"]
        args += ["-movflags", "+faststart", str(mp4)]
        if _run(args).returncode != 0 or not mp4.exists():
            raise ClipError("video_unreadable")
    at = min(POSTER_AT_S, max(0.0, min(duration, MAX_S) / 2))
    if _run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file",
             "-format_whitelist", FORMATS, "-ss", f"{at:.2f}", "-i", str(mp4),
             "-frames:v", "1", str(poster)]
            ).returncode != 0 or not poster.exists():
        raise ClipError("video_unreadable")
    body = mp4.read_bytes()
    return mp4, poster, hashlib.sha256(body).hexdigest(), min(duration, MAX_S)


def store(conn: sqlite3.Connection, data_dir: Path, data: bytes, *, account_id: str,
          source: str, now: float | None = None) -> Clip:
    clip_id = "clip_" + secrets.token_hex(8)
    mp4, poster, digest, seconds = normalize(data, data_dir / "clips" / clip_id)
    now = time.time() if now is None else now
    with transaction(conn) as c:
        c.execute("INSERT INTO clips (id, account_id, source, path, poster_path, sha256, "
                  "seconds, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (clip_id, account_id, source, str(mp4), str(poster), digest, seconds, now))
    return Clip(clip_id, account_id, mp4, poster, digest, seconds)


def _row(row: tuple | None) -> Clip | None:  # type: ignore[type-arg]
    if row is None:
        return None
    return Clip(row[0], row[1], Path(row[2]), Path(row[3]), row[4], float(row[5]), row[6])


_COLS = "id, account_id, path, poster_path, sha256, seconds, brief_id"


def get(conn: sqlite3.Connection, clip_id: str) -> Clip | None:
    with conn.lock if hasattr(conn, "lock") else _Null():
        return _row(conn.execute(f"SELECT {_COLS} FROM clips WHERE id = ?",  # noqa: S608
                                 (clip_id,)).fetchone())


def attach(conn: sqlite3.Connection, clip_id: str, brief_id: str, account_id: str) -> bool:
    """Bind a waiting clip of this account to a brief (once)."""
    with transaction(conn) as c:
        cur = c.execute("UPDATE clips SET brief_id = ? WHERE id = ? AND brief_id = '' AND "
                        "account_id = ?", (brief_id, clip_id, account_id))
        return cur.rowcount == 1


def waiting(conn: sqlite3.Connection, account_id: str, now: float | None = None
            ) -> Clip | None:
    """The account's newest clip not yet used, sent within ``ATTACH_WINDOW_S``."""
    now = time.time() if now is None else now
    with conn.lock if hasattr(conn, "lock") else _Null():
        return _row(conn.execute(
            f"SELECT {_COLS} FROM clips WHERE account_id = ? AND brief_id = '' AND "  # noqa: S608
            "created_ts > ? ORDER BY created_ts DESC LIMIT 1",
            (account_id, now - ATTACH_WINDOW_S)).fetchone())


def for_brief(conn: sqlite3.Connection, brief_id: str) -> Clip | None:
    with conn.lock if hasattr(conn, "lock") else _Null():
        return _row(conn.execute(f"SELECT {_COLS} FROM clips WHERE brief_id = ?",  # noqa: S608
                                 (brief_id,)).fetchone())


class _Null:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
