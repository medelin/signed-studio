"""Voice brief: browser audio → ffmpeg (mono 24 kHz PCM WAV) → Muse Voice Transcribe.

``POST /asr/transcribe`` (multipart: ``request`` JSON + ``audio`` file) with
``mode=ENDPOINTING``, ``audioEncoding=WAV`` and ``languageBias=["pl"]``; the response is the
transcript plus ``turns[{startMs, endMs, transcript, speaker}]``. Limits enforced here, before
any request: ≤ 32 MB upload, ≤ 10 minutes of audio. ffmpeg reads only a local temp file
(``-protocol_whitelist file``), so a crafted container cannot make it fetch anything. The
audio is deleted with the temp directory; nothing is kept unless the caller stores it.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.muse.client import MuseClient, MuseError, MuseResult

MODEL = "muse-voice-transcribe-1.0"
AGENT = "voice"
MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 600
SAMPLE_RATE = 24_000
FFMPEG_TIMEOUT_S = 60


class VoiceError(ValueError):
    def __init__(self, message: str, code: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Turn:
    start_ms: int
    end_ms: int
    text: str
    speaker: str = ""


@dataclass
class Transcript:
    text: str
    turns: list[Turn] = field(default_factory=list)
    seconds: float = 0.0
    result: MuseResult | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"transcript": self.text, "seconds": round(self.seconds, 2),
                "turns": [{"start_ms": t.start_ms, "end_ms": t.end_ms, "text": t.text,
                           "speaker": t.speaker} for t in self.turns]}


def ffmpeg_args(src: Path, dst: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Any input → mono 24 kHz signed 16-bit PCM WAV, at most ``MAX_SECONDS`` + 1 s read,
    no metadata (byte-stable output), local files only."""
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "file", "-i", str(src),
            "-t", str(MAX_SECONDS + 1), "-vn", "-map_metadata", "-1",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", "-f", "wav", "-y", str(dst)]


def wav_seconds(wav: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError) as exc:
        raise VoiceError("not a WAV file", "bad_audio") from exc


def check_upload(data: bytes) -> None:
    if not data:
        raise VoiceError("empty upload", "empty_audio")
    if len(data) > MAX_BYTES:
        raise VoiceError(f"audio larger than {MAX_BYTES} bytes", "audio_too_large", 413)


def to_wav(data: bytes) -> bytes:
    """Convert an upload with ffmpeg and enforce the duration limit."""
    check_upload(data)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VoiceError("ffmpeg is not installed", "no_ffmpeg", 503)
    with tempfile.TemporaryDirectory(prefix="studio-voice-") as tmp:
        src, dst = Path(tmp) / "in.bin", Path(tmp) / "out.wav"
        src.write_bytes(data)
        try:
            proc = subprocess.run(ffmpeg_args(src, dst, ffmpeg), capture_output=True,  # noqa: S603
                                  timeout=FFMPEG_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as exc:
            raise VoiceError("audio conversion timed out", "convert_timeout") from exc
        if proc.returncode != 0 or not dst.exists():
            raise VoiceError("audio could not be decoded", "bad_audio")
        wav = dst.read_bytes()
    seconds = wav_seconds(wav)
    if seconds > MAX_SECONDS:
        raise VoiceError(f"audio longer than {MAX_SECONDS // 60} minutes", "audio_too_long",
                         413)
    if seconds < 0.3:
        raise VoiceError("audio is too short", "audio_too_short")
    return wav


def _turns(items: list[dict[str, Any]]) -> list[Turn]:
    out = []
    for t in items:
        try:
            out.append(Turn(int(t.get("startMs", 0)), int(t.get("endMs", 0)),
                            str(t.get("transcript", "")), str(t.get("speaker", "") or "")))
        except (TypeError, ValueError):
            continue
    return out


def transcribe(
    client: MuseClient,
    wav: bytes,
    *,
    run_id: str,
    language_bias: tuple[str, ...] = ("pl",),
    mode: str = "ENDPOINTING",
) -> Transcript:
    """One Muse Voice Transcribe call for a converted WAV (``to_wav``)."""
    if len(wav) > MAX_BYTES:
        raise VoiceError(f"audio larger than {MAX_BYTES} bytes", "audio_too_large", 413)
    seconds = wav_seconds(wav)
    if seconds > MAX_SECONDS:
        raise VoiceError(f"audio longer than {MAX_SECONDS // 60} minutes", "audio_too_long",
                         413)
    request = {"mode": mode, "model": MODEL, "audioEncoding": "WAV",
               "languageBias": list(language_bias)}
    result = client.transcribe(wav, request, seconds=seconds, run_id=run_id, agent=AGENT,
                               tier_reason="operator voice brief (speech to text)")
    if not result.text.strip():
        raise MuseError("the transcription is empty", code="empty_transcript")
    return Transcript(result.text.strip(), _turns(result.output_items), seconds, result)
