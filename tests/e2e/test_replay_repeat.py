"""Replay serves repeated runs in one process (the judge sandbox runs the same brief many
times): a second run must replay like the first, not get another call's recording."""

import secrets

from fastapi.testclient import TestClient

from studio import demo
from studio.app import create_app
from studio.settings import Settings
from tests.test_ui_studio import wait_for


def test_two_runs_back_to_back_in_one_replay_process(tmp_path):
    password = "Rp1-" + secrets.token_urlsafe(24)
    app = create_app(Settings.load({
        "STUDIO_DATA_DIR": str(tmp_path / "v"), "JUDGE_PASSWORD": password,
        "SESSION_SECRET": secrets.token_urlsafe(32), "STUDIO_REPLAY": "1",
        "STUDIO_REPLAY_SET": "demo", "META_ADAPTER": "mock"}))
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/login", data={"username": "judge", "password": password}
                      ).status_code == 200
        statuses = []
        for _ in range(2):
            r = c.post("/api/runs", json={"brief": demo.BRIEFS["demo"]["brief"]})
            assert r.status_code == 202, r.text
            data = wait_for(c, r.json()["run_id"], timeout=60)
            statuses.append((data["status"], len(data["state"]["creatives"]["images"])))
    assert statuses == [("awaiting_approval", 3), ("awaiting_approval", 3)]
