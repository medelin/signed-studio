"""Voice brief (D3-4): ffmpeg conversion, limits, recorded Muse Voice Transcribe call."""

import email.parser
import email.policy
import hashlib
import io
import json
import shutil
import wave

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.muse import voice
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT, load
from tests.test_ui_studio import login, settings

CASSETTE = load("voice_transcribe")
WAV = (ROOT / CASSETTE["audio"]).read_bytes()
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _wav(seconds, rate=24_000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


def test_ffmpeg_args_make_mono_24k_pcm_from_local_files_only(tmp_path):
    args = voice.ffmpeg_args(tmp_path / "in.bin", tmp_path / "out.wav", "matroska")
    joined = " ".join(args)
    assert args.index("-f") < args.index("-i") and args[args.index("-f") + 1] == "matroska"
    assert args.index("-format_whitelist") < args.index("-i")
    assert "hls" not in joined and "concat" not in joined
    with pytest.raises(ValueError):
        voice.ffmpeg_args(tmp_path / "in.bin", tmp_path / "out.wav", "hls")
    assert "-ac 1" in joined and "-ar 24000" in joined and "-c:a pcm_s16le" in joined
    assert "-protocol_whitelist file" in joined and "-nostdin" in args
    assert args.index("-protocol_whitelist") < args.index("-i")  # applies to the input
    assert "-t 601" in joined and "-map_metadata -1" in joined
    assert args[-1].endswith("out.wav") and "-f wav" in joined


def test_upload_and_duration_limits():
    with pytest.raises(voice.VoiceError) as exc:
        voice.check_upload(b"x" * (32 * 1024 * 1024 + 1))
    assert exc.value.code == "audio_too_large" and exc.value.status == 413
    with pytest.raises(voice.VoiceError):
        voice.check_upload(b"")
    long = _wav(601, rate=8000)
    client = MuseClient(FAKE_KEY, ledger=Ledger(), budget=Budget(1.0))
    with pytest.raises(voice.VoiceError) as exc:
        voice.transcribe(client, long, run_id="b_1")
    assert exc.value.code == "audio_too_long"
    assert voice.wav_seconds(_wav(2.5)) == 2.5


@needs_ffmpeg
def test_conversion_is_mono_24k_and_enforces_ten_minutes():
    out = voice.to_wav(_wav(1.0, rate=48_000))
    with wave.open(io.BytesIO(out)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 24_000, 2)
    with pytest.raises(voice.VoiceError) as exc:
        voice.to_wav(_wav(605, rate=8000))
    assert exc.value.code == "audio_too_long"
    with pytest.raises(voice.VoiceError) as exc:
        voice.to_wav(b"RIFF\0\0\0\0WAVEgarbage" * 10)
    assert exc.value.code == "bad_audio"


def _parts(request):
    raw = (b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
           + request.content)
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)
    return {p.get_param("name", header="content-disposition"): p.get_payload(decode=True)
            for p in msg.iter_parts()}


def _mock(router):
    sent = []
    rec = CASSETTE["interactions"][0]

    def handler(request):
        assert request.headers["authorization"] == f"Bearer {FAKE_KEY}"
        assert request.url.path.endswith("/asr/transcribe")
        parts = _parts(request)
        sent.append(parts)
        assert json.loads(parts["request"]) == rec["request"]["multipart"]["request"]
        assert hashlib.sha256(parts["audio"]).hexdigest() == rec["request"]["multipart"][
            "audio_sha256"]
        return httpx.Response(200, json=rec["response"]["json"])

    router.post(url__startswith=BASE_URL).mock(side_effect=handler)
    return sent


def test_recorded_polish_brief_is_transcribed_with_turn_timestamps():
    ledger = Ledger()
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    with respx.mock() as router:
        sent = _mock(router)
        t = voice.transcribe(client, WAV, run_id="b_rec")
    req = json.loads(sent[0]["request"])
    assert req == {"mode": "ENDPOINTING", "model": "muse-voice-transcribe-1.0",
                   "audioEncoding": "WAV", "languageBias": ["pl"]}
    assert "kubki" in t.text and "Instagram" in t.text
    assert t.turns and t.turns[0].start_ms == 0 and t.turns[-1].end_ms > 15_000
    (r,) = ledger.receipts("b_rec")
    assert r.model == "muse-voice-transcribe-1.0" and r.details["endpoint"] == "asr.transcribe"
    assert r.input_sha256 == [hashlib.sha256(WAV).hexdigest()]
    assert r.details["units"] == 19  # audioDurationMs 18880 → 19 s billed
    assert abs(r.cost_usd - 19 * 0.18 / 3600) < 1e-9


@needs_ffmpeg
def test_voice_route_converts_transcribes_and_keeps_no_audio(tmp_path):
    a = create_app(settings(tmp_path))
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0))
    with TestClient(a, base_url="https://testserver") as c, respx.mock(
            assert_all_called=False) as router:
        router.route(host="testserver").pass_through()
        assert c.post("/api/briefs/voice", content=WAV,
                      headers={"content-type": "audio/wav"}).status_code == 401
        login(c)
        assert c.post("/api/briefs/voice", content=b"x",
                      headers={"content-type": "text/plain"}).status_code == 415
        _mock(router)
        r = c.post("/api/briefs/voice", content=WAV, headers={"content-type": "audio/wav"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "kubki" in body["transcript"] and body["turns"] and body["audio_kept"] is False
    row = a.state.conn.execute("SELECT source, text FROM briefs WHERE id = ?",
                               (body["brief_id"],)).fetchone()
    assert row[0] == "voice" and "kubki" in row[1]
    assert not (tmp_path / "var" / "audio").exists()


def test_voice_upload_above_the_limit_is_413(tmp_path):
    a = create_app(settings(tmp_path))
    with TestClient(a, base_url="https://testserver") as c:
        login(c)
        r = c.post("/api/briefs/voice", content=b"\0" * (32 * 1024 * 1024 + 1),
                   headers={"content-type": "audio/webm"})
    assert r.status_code == 413


@pytest.mark.parametrize("data", [
    b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:1,\nfile:///etc/passwd\n",
    b"ffconcat version 1.0\nfile '/etc/passwd'\n",
    b"<?xml version='1.0'?><MPD></MPD>",
    b"just some text that is not audio",
])
def test_playlists_and_unknown_containers_are_rejected_before_ffmpeg(data, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ffmpeg must not run")

    monkeypatch.setattr(voice.subprocess, "run", boom)
    with pytest.raises(voice.VoiceError) as exc:
        voice.to_wav(data)
    assert exc.value.code == "unsupported_audio_type" and exc.value.status == 415


@needs_ffmpeg
@pytest.mark.parametrize(("codec", "fmt", "demuxer"), [
    ("libopus", "webm", "matroska"), ("libvorbis", "ogg", "ogg"), ("pcm_s16le", "wav", "wav"),
])
def test_valid_webm_ogg_and_wav_pass(tmp_path, codec, fmt, demuxer):
    import subprocess

    src = tmp_path / "in.wav"
    src.write_bytes(_wav(1.0, rate=48_000))
    out = tmp_path / f"out.{fmt}"
    proc = subprocess.run([shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error",  # noqa: S603
                           "-i", str(src),
                           "-c:a", codec, str(out)], capture_output=True, check=False)
    if proc.returncode != 0:
        pytest.skip(f"ffmpeg cannot encode {codec}")
    data = out.read_bytes()
    assert voice.sniff(data) == demuxer
    wav = voice.to_wav(data)
    assert 0.9 < voice.wav_seconds(wav) < 1.2


def test_route_rejects_a_playlist_with_415(tmp_path):
    a = create_app(settings(tmp_path))
    a.state.muse = MuseClient(FAKE_KEY, ledger=a.state.ledger, budget=Budget(1.0))
    with TestClient(a, base_url="https://testserver") as c:
        login(c)
        r = c.post("/api/briefs/voice", content=b"#EXTM3U\nfile:///etc/passwd\n",
                   headers={"content-type": "audio/webm"})
    assert r.status_code == 415 and r.json()["error"] == "unsupported_audio_type"


def test_ffmpeg_conversions_are_capped(monkeypatch):
    import threading

    from studio.muse import voice
    ran = []
    monkeypatch.setattr(voice.subprocess, "run",
                        lambda *a, **k: ran.append(a) or type("P", (), {"returncode": 1})())
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(voice, "_SLOTS", slots)
    assert voice.MAX_CONVERSIONS >= 1
    slots.acquire()  # a conversion is running
    with pytest.raises(voice.VoiceError) as exc:
        voice.to_wav(WAV)
    assert exc.value.code == "busy" and exc.value.status == 429 and ran == []
    slots.release()
    with pytest.raises(voice.VoiceError):  # the fake ffmpeg wrote nothing: bad_audio …
        voice.to_wav(WAV)
    assert slots.acquire(blocking=False)  # … and the slot was given back
