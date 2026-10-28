"""D4-5 polish: empty states tell the visitor what to do; the nav labels every page."""

import secrets

import pytest
from fastapi.testclient import TestClient

from studio.app import create_app
from studio.settings import Settings


@pytest.fixture
def client(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v"),
                                  "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
                                  "SESSION_SECRET": secrets.token_urlsafe(32)}))
    with a.state.conn.lock:
        a.state.conn.execute("DELETE FROM comments")
        a.state.conn.execute("DELETE FROM leads")
        a.state.conn.commit()
    with TestClient(a, base_url="https://testserver") as c:
        yield c


@pytest.mark.parametrize("path,needle", [
    ("/", "Start a run in the"),
    ("/inbox", "No comments yet."),
    ("/leads", "No leads yet. A comment"),
    ("/report", "No report yet."),
    ("/receipts", "No receipts yet."),
])
def test_empty_states_explain_the_next_step(client, path, needle):
    r = client.get(path)
    assert r.status_code == 200 and needle in r.text


def test_every_page_links_every_page(client):
    html = client.get("/").text
    for href in ("/studio", "/inbox", "/leads", "/report", "/receipts"):
        assert f'href="{href}"' in html


def test_replay_mode_is_labelled(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "r"),
                                  "STUDIO_REPLAY": "1"}))
    with TestClient(a, base_url="https://testserver") as c:
        assert ">replay</span>" in c.get("/").text
