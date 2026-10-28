"""Voice brief: browser audio → ffmpeg (mono 24 kHz PCM WAV) → Muse Voice Transcribe.

``POST /asr/transcribe`` (multipart: ``request`` JSON + ``audio`` file) with
``mode=ENDPOINTING``, ``audioEncoding=WAV`` and ``languageBias=["pl"]``; the response is the
transcript plus ``turns[{startMs, endMs, transcript, speaker}]``. Limits enforced here, before
any request: ≤ 32 MB upload, ≤ 10 minutes of audio.

ffmpeg never auto-detects the input: the upload's magic bytes must identify WAV, WebM/
Matroska, Ogg, MP4/M4A, MP3 or AAC (``sniff``), anything else — e.g. an HLS ``#EXTM3U`` or
``ffconcat`` playlist that would make ffmpeg read other local files — is a 415 before
ffmpeg runs; ffmpeg is then given ``-f <that demuxer>``, ``-format_whitelist`` of the audio
demuxers only, ``-protocol_whitelist file`` and the path of the upload in a private
(0700) temp directory. The audio is deleted with the temp directory; nothing is kept
unless the caller stores it.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import threading
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
MAX_CONVERSIONS = 2  # concurrent ffmpeg processes; more uploads get 429, not a queue
_SLOTS = threading.BoundedSemaphore(MAX_CONVERSIONS)


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


# demuxers ffmpeg may use; sniff() picks one, nothing else is ever opened
DEMUXERS = ("wav", "matroska", "ogg", "mov", "mp3", "aac")
FORMAT_WHITELIST = "wav,matroska,webm,ogg,mov,mp4,m4a,3gp,3g2,mj2,mp3,aac"


def sniff(data: bytes) -> str:
    """The ffmpeg demuxer for an audio upload, from its magic bytes (``VoiceError`` 415)."""
    head = data[:16]
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"  # also WebM
    if head[:4] == b"OggS":
        return "ogg"
    if head[4:8] == b"ftyp":
        return "mov"  # MP4 / M4A (external data references stay disabled by default)
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] in (
            0xFB, 0xF3, 0xF2, 0xFA, 0xE3)):
        return "mp3"
    if len(head) > 1 and head[0] == 0xFF and head[1] in (0xF1, 0xF9):
        return "aac"
    raise VoiceError("unsupported audio container (wav, webm, ogg, mp4, mp3, aac)",
                     "unsupported_audio_type", 415)


def ffmpeg_args(src: Path, dst: Path, demuxer: str, ffmpeg: str = "ffmpeg") -> list[str]:
    """Upload → mono 24 kHz signed 16-bit PCM WAV, at most ``MAX_SECONDS`` + 1 s read,
    no metadata (byte-stable output); explicit demuxer, whitelisted formats and protocols."""
    if demuxer not in DEMUXERS:
        raise ValueError(f"demuxer {demuxer!r} is not allowed")
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", demuxer, "-format_whitelist", FORMAT_WHITELIST,
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


def _convert(data: bytes, demuxer: str) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise VoiceError("ffmpeg is not installed", "no_ffmpeg", 503)
    with tempfile.TemporaryDirectory(prefix="studio-voice-") as tmp:
        src, dst = Path(tmp) / "in.bin", Path(tmp) / "out.wav"
        src.write_bytes(data)
        try:
            proc = subprocess.run(ffmpeg_args(src, dst, demuxer, ffmpeg),  # noqa: S603
                                  capture_output=True,
                                  timeout=FFMPEG_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as exc:
            raise VoiceError("audio conversion timed out", "convert_timeout") from exc
        if proc.returncode != 0 or not dst.exists():
            raise VoiceError("audio could not be decoded", "bad_audio")
        return dst.read_bytes()


def to_wav(data: bytes) -> bytes:
    """Convert an upload with ffmpeg (at most ``MAX_CONVERSIONS`` at once; 429 ``busy``
    otherwise) and enforce the duration limit."""
    check_upload(data)
    demuxer = sniff(data)  # before ffmpeg ever sees the bytes
    if not _SLOTS.acquire(blocking=False):
        raise VoiceError("too many audio conversions at once; try again", "busy", 429)
    try:
        wav = _convert(data, demuxer)
    finally:
        _SLOTS.release()
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
