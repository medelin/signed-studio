"""The owner's phone clip as the ad's video: checked, cropped to 9:16, owned, bound to one
brief, used by the art director instead of a cut from renders; WhatsApp videos too."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import clip, db
from tests.test_judge_live import LIVE
from tests.test_ui_studio import PASSWORD, csrf, login, settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def phone_video(tmp_path: Path, seconds: int = 3, size: str = "640x360",
                audio: bool = True) -> bytes:
    """A landscape test video, as a phone in the wrong orientation would send."""
    out = tmp_path / "phone.mp4"
    args = [shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"testsrc=size={size}:rate=30:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(out)]
    subprocess.run(args, check=True, capture_output=True)  # noqa: S603
    return out.read_bytes()


def probe(path: Path) -> dict:
    out = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries",  # noqa: S603
                          "stream=codec_type,width,height:format=duration", "-of", "json",
                          str(path)], check=True, capture_output=True)
    return json.loads(out.stdout)


@needs_ffmpeg
def test_a_landscape_clip_becomes_a_9x16_video_with_a_poster(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.init(conn)
    made = clip.store(conn, tmp_path, phone_video(tmp_path), account_id="acct_1",
                      source="upload")
    info = probe(made.path)
    video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (video["width"], video["height"]) == (1080, 1920)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert 2.5 <= made.seconds <= 3.5 and made.poster_path.read_bytes()[:4] == b"\x89PNG"
    assert clip.get(conn, made.id).sha256 == made.sha256


@needs_ffmpeg
def test_a_long_clip_is_cut_to_fifteen_seconds(tmp_path):
    made_path, _, _, seconds = clip.normalize(phone_video(tmp_path, seconds=20, audio=False),
                                              tmp_path / "out")
    assert seconds == clip.MAX_S
    assert float(probe(made_path)["format"]["duration"]) <= clip.MAX_S + 0.2


@needs_ffmpeg
@pytest.mark.parametrize("data,code", [(b"", "empty_video"), (b"not a video" * 50,
                                                              "video_unreadable")])
def test_what_is_not_a_video_is_refused(tmp_path, data, code):
    with pytest.raises(clip.ClipError) as exc:
        clip.normalize(data, tmp_path / "out")
    assert exc.value.code in (code, "no_video_stream")


@needs_ffmpeg
def test_a_clip_is_bound_once_to_its_owners_brief(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.init(conn)
    made = clip.store(conn, tmp_path, phone_video(tmp_path), account_id="acct_1",
                      source="whatsapp")
    assert clip.waiting(conn, "acct_1").id == made.id
    assert not clip.attach(conn, made.id, "b_other", "acct_2")  # not theirs
    assert clip.attach(conn, made.id, "b_1", "acct_1")
    assert not clip.attach(conn, made.id, "b_2", "acct_1")  # used once
    assert clip.for_brief(conn, "b_1").id == made.id and clip.waiting(conn, "acct_1") is None


@needs_ffmpeg
def test_upload_and_draft_bind_the_clip(tmp_path):
    from tests.test_brand_scan import _app
    from tests.test_brief_flow import drafted
    app, _ = _app(tmp_path)
    app.state.accounts.create("ola", PASSWORD)
    with TestClient(app, base_url="https://testserver") as c:
        c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})
        r = c.post("/api/clips", content=phone_video(tmp_path),
                   headers={"Content-Type": "video/mp4"})
        assert r.status_code == 200, r.text
        clip_id = r.json()["clip_id"]
        assert c.get(f"/api/clips/{clip_id}/poster.png").content[:4] == b"\x89PNG"
        d = drafted(c, clip_id=clip_id)
    assert d["clip_id"] == clip_id and clip.for_brief(app.state.conn, d["brief_id"]).id == clip_id
    with TestClient(app, base_url="https://testserver") as other:  # someone else's poster
        login(other)
        assert other.get(f"/api/clips/{clip_id}/poster.png").status_code == 404


def test_on_a_live_adapter_the_judge_cannot_add_a_clip(tmp_path):
    from studio.app import create_app
    app = create_app(settings(tmp_path, **LIVE))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        r = c.post("/api/clips", content=b"x", headers={"Content-Type": "video/mp4"})
    assert r.status_code == 403 and r.json() == {"error": "clip_not_allowed"}


@needs_ffmpeg
def test_the_art_director_uses_the_owners_clip(tmp_path):
    from studio.agents import art_director
    from studio.receipts import Ledger

    class Client:
        ledger = Ledger()

    mp4 = tmp_path / "c.mp4"
    poster = tmp_path / "p.png"
    mp4.write_bytes(b"mp4-bytes")
    poster.write_bytes(b"\x89PNG-poster")
    out = tmp_path / "data" / "creatives" / "run_x"
    out.mkdir(parents=True)
    video, image = art_director._own_video(Client(), (mp4, poster, 7.5), out, tmp_path / "data",
                                           "run_x")
    assert video.storyboard == "owner" and video.seconds == 7.5
    assert (tmp_path / "data" / video.path).read_bytes() == b"mp4-bytes"
    assert image.placement == "video_9x16"
    assert Client.ledger.receipts("run_x")[-1].details["tool"] == "video.own_clip"


@needs_ffmpeg
def test_a_clip_may_be_larger_than_the_small_body_limit(tmp_path):
    from studio.app import create_app
    app = create_app(settings(tmp_path))
    app.state.accounts.create("ola", PASSWORD)
    with TestClient(app, base_url="https://testserver") as c:
        c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})
        r = c.post("/api/clips", content=b"\0" * (300 * 1024),
                   headers={"Content-Type": "video/mp4"})
    assert r.status_code == 422, r.text  # read and refused as video, not cut at 64 KB


@needs_ffmpeg
@pytest.mark.parametrize("payload", [
    b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n",
    b"ffconcat version 1.0\nfile '/etc/passwd'\n",
])
def test_a_playlist_posing_as_video_never_reads_local_files(tmp_path, payload):
    with pytest.raises(clip.ClipError) as exc:
        clip.normalize(payload * 20, tmp_path / "out")
    assert exc.value.code in ("video_unreadable", "no_video_stream")
    assert not (tmp_path / "out" / "clip.mp4").exists()


@pytest.mark.parametrize("is_demo,tier,adapter,ok", [
    (False, "full", "graph", True), (True, "full", "graph", False),
    (False, "trial", "graph", False), (True, "trial", "mock", True)])
def test_one_rule_decides_who_may_add_a_clip(is_demo, tier, adapter, ok):
    """The brief page and WhatsApp share it: the judge and trial accounts never put a clip
    into the operator's live ad account."""
    class Account:
        pass
    a = Account()
    a.is_demo, a.tier = is_demo, tier
    assert clip.may_add(a, adapter) is ok
    assert clip.may_add(None, adapter) is False
