"""Judge sandbox: reset semantics (docs/50 §1.5 item 4), judge-only writes, limits, deploy files."""

import base64
import re
import secrets
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from studio import db, sandbox
from studio.app import CSP, create_app
from studio.receipts import public_key_raw
from studio.sandbox import SandboxConfig, Window, maybe_reset, read_marker
from studio.settings import ConfigError, Settings

ROOT = Path(__file__).resolve().parent.parent
SECRET = secrets.token_urlsafe(32)
PASSWORD = "Jd7-" + secrets.token_urlsafe(24)
HOUR = 3600.0


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    db.seed(c)
    yield c
    c.close()


def counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("comments", "leads", "receipts", "nonces")}


def settings(tmp_path, **extra):
    env = {"STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": PASSWORD,
           "SESSION_SECRET": SECRET, "COOKIE_SECURE": "1"}
    env.update(extra)
    return Settings.load(env)


# -- configuration ------------------------------------------------------------------------


def test_sandbox_is_off_by_default():
    assert SandboxConfig.load({}).enabled is False
    with pytest.raises(ConfigError):
        SandboxConfig.load({"STUDIO_SANDBOX": "maybe"})
    with pytest.raises(ConfigError):
        SandboxConfig.load({"STUDIO_SANDBOX": "1", "STUDIO_RESET_INTERVAL_S": "5"})


@pytest.mark.parametrize("env, needle", [
    ({"JUDGE_PASSWORD": "", "SESSION_SECRET": ""}, "JUDGE_PASSWORD"),
    ({"COOKIE_SECURE": "0"}, "COOKIE_SECURE"),
])
def test_unsafe_sandbox_refuses_to_start(tmp_path, env, needle):
    with pytest.raises(ConfigError, match=needle):
        SandboxConfig(enabled=True).validate(settings(tmp_path, **env))


def test_sandbox_refuses_the_graph_adapter(tmp_path):
    s = settings(tmp_path, META_ADAPTER="graph", META_ACCESS_TOKEN="t", META_AD_ACCOUNT_ID="1",
                 META_PAGE_ID="2", META_LANDING_URL="https://example.com/")
    with pytest.raises(ConfigError, match="META_ADAPTER=mock"):
        SandboxConfig(enabled=True).validate(s)


# -- reset semantics ----------------------------------------------------------------------


def test_disabled_sandbox_never_resets_nor_marks(conn):
    conn.execute("DELETE FROM leads")
    assert maybe_reset(conn, enabled=False, now=10 * HOUR) == "disabled"
    assert read_marker(conn) is None
    assert counts(conn)["leads"] == 0


def test_first_tick_writes_the_marker_only(conn):
    conn.execute("DELETE FROM leads")  # a judge's work, made before the first tick
    assert maybe_reset(conn, enabled=True, now=1000.0) == "marked"
    assert read_marker(conn) == 1000.0
    assert counts(conn)["leads"] == 0  # nothing wiped


def test_reset_after_interval_keeps_receipts_nonces_and_keys(conn, tmp_path):
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    ledger.append("run-1", agent="producer", kind="model_call")
    conn.execute("INSERT INTO nonces VALUES ('n_x', 1)")
    maybe_reset(conn, enabled=True, now=0.0)
    conn.execute("DELETE FROM leads")
    before = counts(conn)
    seq = ledger.manifest()["seq"]
    assert maybe_reset(conn, enabled=True, now=HOUR - 1) == "waiting"
    assert counts(conn)["leads"] == 0
    assert maybe_reset(conn, enabled=True, now=HOUR) == "reset"
    after = counts(conn)
    assert after["leads"] > 0  # re-seeded
    assert after["receipts"] == before["receipts"] == 1
    assert after["nonces"] == before["nonces"] == 1
    assert ledger.manifest()["seq"] == seq
    assert read_marker(conn) == HOUR


def test_reset_waits_for_quiet_but_not_forever(conn):
    maybe_reset(conn, enabled=True, now=0.0)
    assert maybe_reset(conn, enabled=True, now=HOUR, last_write=HOUR - 30) == "deferred"
    assert read_marker(conn) == 0.0
    overdue = 1.5 * HOUR
    assert maybe_reset(conn, enabled=True, now=overdue, last_write=overdue - 1) == "reset"


def test_failed_reset_keeps_marker_and_data(conn, tmp_path):
    maybe_reset(conn, enabled=True, now=0.0)
    conn.execute("DELETE FROM leads")
    bad = tmp_path / "seed"
    bad.mkdir()
    (bad / "leads.json").write_text('[{"email": "someone@real-domain.com"}]')
    with pytest.raises(ValueError):
        maybe_reset(conn, enabled=True, now=HOUR, seed_dir=bad)
    assert read_marker(conn) == 0.0  # retried on the next tick
    assert counts(conn)["leads"] == 0


def test_marker_from_the_future_is_rewritten_not_trusted(conn):
    maybe_reset(conn, enabled=True, now=10 * HOUR)
    conn.execute("DELETE FROM leads")
    assert maybe_reset(conn, enabled=True, now=HOUR) == "marked"
    assert read_marker(conn) == HOUR
    assert counts(conn)["leads"] == 0


# -- limits ---------------------------------------------------------------------------------


def test_window_limits_and_is_bounded():
    w = Window(limit=2, window_s=60, max_keys=3)
    assert w.hit("a", now=0) == 0 and w.hit("a", now=1) == 0
    assert w.hit("a", now=2) == pytest.approx(58)
    assert w.hit("a", now=60.5) == 0  # the first hit left the window
    for k in "bcdef":
        w.hit(k, now=70)
    assert len(w) == 3


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def make(**limits):
        cfg = SandboxConfig(enabled=True, reset_thread=False, **limits)
        monkeypatch.setattr(sandbox.SandboxConfig, "load", classmethod(lambda cls, env=None: cfg))
        app = create_app(settings(tmp_path))
        return TestClient(app, base_url="https://testserver")
    return make


def login(client):
    r = client.post("/login", data={"username": "judge", "password": PASSWORD})
    assert r.status_code == 200
    return r


def key_body():
    raw = public_key_raw(Ed25519PrivateKey.generate().public_key())
    return {"public_key": base64.urlsafe_b64encode(raw).rstrip(b"=").decode()}


def test_public_pages_stay_readable(make_client):
    with make_client() as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").json()["ok"] is True
        assert client.get("/api/receipts.jsonl").status_code == 200
        head = client.head("/healthz")
        assert head.status_code == 200 and head.content == b""


@pytest.mark.parametrize("path", ["/api/runs", "/api/keys", "/api/comments/simulate",
                                  "/api/reports", "/api/anything-new"])
def test_every_write_needs_the_judge(make_client, path):
    with make_client() as client:
        r = client.post(path, json={})
        assert r.status_code == 401
        assert r.json() == {"error": "login_required"}


def test_judge_writes_pass_then_hit_the_session_limit(make_client):
    with make_client(session_writes_per_min=2) as client:
        login(client)
        assert client.post("/api/keys", json=key_body()).status_code == 200
        assert client.post("/api/keys", json=key_body()).status_code == 200
        r = client.post("/api/keys", json=key_body())
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1


def test_per_ip_request_limit(make_client):
    with make_client(ip_requests_per_min=10) as client:
        codes = [client.get("/healthz").status_code for _ in range(12)]
        assert codes[:10] == [200] * 10
        assert codes[10:] == [429, 429]


def test_resetter_tick_uses_the_app_database(make_client):
    with make_client() as client:
        resetter = client.app.state.sandbox_resetter
        assert resetter.tick() == "marked"
        assert resetter.tick() == "waiting"


# -- deployment files -------------------------------------------------------------------


def _caddy_header(text: str, name: str) -> str:
    m = re.search(rf'^\s*{name} "([^"]*)"', text, re.M)
    assert m, name
    return m.group(1)


@pytest.mark.parametrize("path", ["Caddyfile", "deploy/Caddyfile"])
def test_caddyfiles_match_the_app(path):
    text = (ROOT / path).read_text()
    assert _caddy_header(text, "Content-Security-Policy") == CSP
    assert _caddy_header(text, "Strict-Transport-Security").startswith("max-age=")
    assert "max_size 128KB" in text and "max_size 33MB" in text


def test_systemd_unit_is_hardened():
    unit = (ROOT / "deploy/muse-studio-lite.service").read_text()
    for line in ("NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes",
                 "ReadWritePaths=/var/lib/muse-studio-lite", "MemoryMax=", "Restart=on-failure",
                 "User=muse", "STUDIO_SANDBOX=1", "--host 127.0.0.1", "--no-proxy-headers"):
        assert line in unit, line


def test_compose_carries_no_secrets_and_sandbox_on():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert 'STUDIO_SANDBOX: "1"' in compose and "META_ADAPTER: mock" in compose
    for name in ("MODEL_API_KEY", "JUDGE_PASSWORD", "SESSION_SECRET"):
        assert name not in compose
    assert ".env" in (ROOT / ".dockerignore").read_text()
