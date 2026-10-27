import asyncio
import base64
import secrets
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from fastapi.testclient import TestClient

from studio.app import LoginLimiter, client_ip, create_app
from studio.receipts import public_key_raw, verify
from studio.settings import ConfigError, Settings

SECRET = secrets.token_urlsafe(32)  # fresh per test run
PASSWORD = "correct horse battery"


def settings(tmp_path, **extra):
    env = {
        "STUDIO_DATA_DIR": str(tmp_path / "var"),
        "JUDGE_PASSWORD": PASSWORD,
        "SESSION_SECRET": SECRET,
        "COOKIE_SECURE": "1",
    }
    env.update(extra)
    return Settings.load(env)


@pytest.fixture
def app(tmp_path):
    return create_app(settings(tmp_path))


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def login(client, password=PASSWORD):
    return client.post("/login", data={"username": "judge", "password": password})


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def test_index_is_public_and_read_only(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Receipts" in r.text
    assert "text/html" in r.headers["content-type"]


def test_index_escapes_receipt_content(app, client):
    app.state.ledger.append("run-x", agent="community", kind="model_call",
                            tier_reason="<script>alert(1)</script>")
    r = client.get("/")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_security_headers(client):
    h = client.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"


def test_login_sets_hardened_cookie(client):
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    assert "max-age=28800" in cookie
    assert SECRET.lower() not in cookie


def test_wrong_password_401(client):
    assert login(client, "nope").status_code == 401


def test_non_ascii_password_401_not_500(client):
    r = client.post("/login", data={"username": "judge", "password": "zażółć gęślą"})
    assert r.status_code == 401


def test_login_json_body(client):
    r = client.post("/login", json={"username": "judge", "password": PASSWORD})
    assert r.status_code == 200


def test_login_disabled_without_judge_password(tmp_path):
    app = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "var")}))
    with TestClient(app, base_url="https://testserver") as c:
        assert login(c).status_code == 403


def test_weak_secret_refuses_to_start(tmp_path):
    with pytest.raises(ConfigError):
        create_app(settings(tmp_path, SESSION_SECRET="short"))


async def _burst(app, n, password="nope", headers=None, peer=("203.0.113.9", 4000)):
    transport = httpx.ASGITransport(app=app, client=peer)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        reqs = [c.post("/login", data={"username": "judge", "password": password},
                       headers=(headers(i) if callable(headers) else headers))
                for i in range(n)]
        return [r.status_code for r in await asyncio.gather(*reqs)]


def test_limiter_atomic_under_concurrency(app):
    codes = asyncio.run(_burst(app, 12))
    assert codes.count(401) == 5 and codes.count(429) == 7


def test_limiter_blocks_correct_password_after_limit(app):
    asyncio.run(_burst(app, 5))
    assert asyncio.run(_burst(app, 1, password=PASSWORD)) == [429]


def test_success_clears_limiter(app):
    asyncio.run(_burst(app, 4))
    assert asyncio.run(_burst(app, 1, password=PASSWORD)) == [200]
    assert asyncio.run(_burst(app, 5)) == [401] * 5


def test_xff_spoof_ignored_without_trusted_proxy(app):
    codes = asyncio.run(_burst(app, 6, headers=lambda i: {"x-forwarded-for": f"198.51.100.{i}"}))
    assert codes.count(429) == 1


def test_xff_rightmost_used_behind_trusted_proxy(tmp_path):
    app = create_app(settings(tmp_path, TRUSTED_PROXY="10.0.0.2"))
    peer = ("10.0.0.2", 5000)
    spoof = lambda i: {"x-forwarded-for": f"198.51.100.{i}, 192.0.2.7"}  # noqa: E731
    codes = asyncio.run(_burst(app, 6, headers=spoof, peer=peer))
    assert codes.count(429) == 1  # leftmost spoofing does not reset the bucket
    other = asyncio.run(_burst(app, 1, headers={"x-forwarded-for": "192.0.2.8"}, peer=peer))
    assert other == [401]


class _Req:
    def __init__(self, peer, xff=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_client_ip_rules():
    trusted = frozenset({"10.0.0.2"})
    assert client_ip(_Req("10.0.0.2", "1.1.1.1, 2.2.2.2"), trusted) == "2.2.2.2"
    assert client_ip(_Req("10.0.0.3", "1.1.1.1, 2.2.2.2"), trusted) == "10.0.0.3"
    assert client_ip(_Req("10.0.0.2", "garbage"), trusted) == "10.0.0.2"
    assert client_ip(_Req("10.0.0.2", "1.1.1.1, "), trusted) == "1.1.1.1"


def test_limiter_memory_is_bounded():
    lim = LoginLimiter(max_attempts=5, window_s=900, max_clients=100)
    for i in range(1000):
        lim.reserve(f"ip-{i}")
    assert len(lim) <= 100


def test_logout_is_post_only_and_revokes(client):
    login(client)
    pub = public_key_raw(Ed25519PrivateKey.generate().public_key())
    assert client.post("/api/keys", json={"public_key": b64url(pub)}).status_code == 200
    assert client.get("/logout").status_code == 405
    stale = client.cookies.get("studio_session")
    assert client.post("/logout").status_code == 200
    client.cookies.clear()
    client.cookies.set("studio_session", stale)  # replay the old cookie after logout
    r = client.post("/api/keys", json={"public_key": b64url(pub)})
    assert r.status_code == 401


def test_keys_requires_session(client):
    pub = public_key_raw(Ed25519PrivateKey.generate().public_key())
    assert client.post("/api/keys", json={"public_key": b64url(pub)}).status_code == 401


def test_keys_registers_and_publishes(client):
    login(client)
    pub = public_key_raw(Ed25519PrivateKey.generate().public_key())
    r = client.post("/api/keys", json={"public_key": b64url(pub)})
    assert r.status_code == 200
    approver = r.json()["approver"]
    listed = client.get("/api/approver-keys").json()
    assert listed[approver] == pub.hex()


def test_keys_rejects_bad_key(client):
    login(client)
    assert client.post("/api/keys", json={"public_key": "AAAA"}).status_code == 400
    assert client.post("/api/keys", json={"nope": 1}).status_code == 400


def test_cross_origin_post_rejected(client):
    login(client)
    pub = public_key_raw(Ed25519PrivateKey.generate().public_key())
    r = client.post("/api/keys", json={"public_key": b64url(pub)},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_forged_and_expired_cookies_rejected(app, client):
    login(client)
    value = client.cookies.get("studio_session")
    sid, exp, mac = value.split(".")
    pub = b64url(public_key_raw(Ed25519PrivateKey.generate().public_key()))
    client.cookies.clear()
    client.cookies.set("studio_session", f"{sid}.{exp}.{'0' * len(mac)}")
    assert client.post("/api/keys", json={"public_key": pub}).status_code == 401
    expired = app.state.sessions.issue(now=time.time() - 9 * 3600)
    client.cookies.clear()
    client.cookies.set("studio_session", expired)
    assert client.post("/api/keys", json={"public_key": pub}).status_code == 401


def test_read_only_blocks_writes(tmp_path):
    app = create_app(settings(tmp_path, STUDIO_READ_ONLY="1"))
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        pub = b64url(public_key_raw(Ed25519PrivateKey.generate().public_key()))
        assert c.post("/api/keys", json={"public_key": pub}).status_code == 503
        assert c.get("/").status_code == 200


def test_body_too_large_413(client):
    r = client.post("/login", content=b"x" * 70_000,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_chunked_body_too_large_413(client):
    def gen():
        for _ in range(20):
            yield b"x" * 8192

    r = client.post("/login", content=gen(),
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413


def test_export_verifies_with_published_key_and_seq(app, client, tmp_path):
    app.state.ledger.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3")
    export = client.get("/api/receipts.jsonl")
    assert export.status_code == 200
    key = client.get("/api/ledger-key").json()
    manifest = client.get("/api/ledger-manifest").json()
    assert manifest["epoch"] == key["epoch"]
    path = tmp_path / "r.jsonl"
    path.write_bytes(export.content)
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key["public_key"]))
    report = verify(path, pub, min_seq=manifest["seq"], approver_keys={})
    assert report.ok, report.errors


def _sid(client):
    return client.cookies.get("studio_session").split(".")[0]


def _register(client, key):
    return client.post("/api/keys", json={"public_key": b64url(public_key_raw(key.public_key()))})


def _approve_with(app, key, approver, session_id):
    from studio import approvals as ap
    from studio import db

    conn, led = app.state.conn, app.state.ledger
    led.append("run-k", agent="community", kind="model_call")
    with db.transaction(conn):
        p = ap.Pending.create("reply.post", "c-1", {"m": "hi"}, ledger=led, run_id="run-k")
        p.save(conn)
    approval = ap.Approval(pending_id=p.id, **p.fields(approver),
                           signature=key.sign(p.message(approver)).hex())
    with db.transaction(conn):
        ap.verify_and_consume(conn, approval, {"m": "hi"}, ledger=led, session_id=session_id)


def test_key_dies_with_session_on_logout(app, client):
    from studio import approvals as ap

    login(client)
    key = Ed25519PrivateKey.generate()
    approver = _register(client, key).json()["approver"]
    sid = _sid(client)
    client.post("/logout")
    with pytest.raises(ap.KeyInactive):
        _approve_with(app, key, approver, sid)


def test_key_works_while_session_active(app, client):
    login(client)
    key = Ed25519PrivateKey.generate()
    approver = _register(client, key).json()["approver"]
    _approve_with(app, key, approver, _sid(client))


def test_keys_from_previous_process_are_inactive(tmp_path):
    from studio import approvals as ap

    first = create_app(settings(tmp_path))
    with TestClient(first, base_url="https://testserver") as c:
        login(c)
        key = Ed25519PrivateKey.generate()
        approver = _register(c, key).json()["approver"]
        sid = _sid(c)
    second = create_app(settings(tmp_path))  # sessions live in process memory
    with pytest.raises(ap.KeyInactive):
        _approve_with(second, key, approver, sid)


def test_ipv6_addresses_in_one_64_share_the_limit(app):
    codes = [asyncio.run(_burst(app, 1, peer=(f"2001:db8:0:1::{i:x}", 4000)))[0]
             for i in range(1, 7)]
    assert codes == [401] * 5 + [429]
    other = asyncio.run(_burst(app, 1, peer=("2001:db8:0:2::1", 4000)))
    assert other == [401]


def test_global_counter_locks_across_addresses(app):
    for i in range(LoginLimiter.GLOBAL_MAX_FAILURES):
        assert asyncio.run(_burst(app, 1, peer=(f"198.51.100.{i + 1}", 1))) == [401]
    locked = asyncio.run(_burst(app, 1, password=PASSWORD, peer=("192.0.2.200", 1)))
    assert locked == [429]


def _fail_globally(lim, t, n=None):
    """``n`` failed logins at time ``t`` from distinct addresses (default: enough to pause)."""
    for i in range(n or lim.GLOBAL_MAX_FAILURES):
        ip = f"198.51.{int(t) % 200}.{i % 250}"
        if lim.reserve(ip, now=t):
            lim.settle(ip, False, now=t)


def test_global_pause_grows_but_is_capped():
    lim = LoginLimiter(max_attempts=100)
    t = 1000.0
    pauses = []
    for _ in range(12):
        _fail_globally(lim, t)
        pauses.append(lim.locked_until - t)
        t = lim.locked_until + 1
    assert pauses[1] > pauses[0]
    assert max(pauses) <= LoginLimiter.MAX_LOCK_S <= 300


def test_failure_storm_never_locks_a_correct_login_beyond_the_cap():
    """A series of failures from many prefixes cannot block the judge longer than the cap."""
    lim = LoginLimiter()
    t = 1000.0
    for _ in range(30):  # sustained storm from fresh prefixes
        _fail_globally(lim, t, n=lim.GLOBAL_MAX_FAILURES * 3)
        t += 1
    last_failure = t - 1
    assert lim.locked_until <= last_failure + LoginLimiter.MAX_LOCK_S
    t = last_failure + LoginLimiter.MAX_LOCK_S
    assert lim.reserve("192.0.2.77", now=t)
    assert lim.settle("192.0.2.77", True, now=t)


def test_strikes_decay_after_quiet_period():
    lim = LoginLimiter(max_attempts=100)
    t = 1000.0
    for _ in range(6):
        _fail_globally(lim, t)
        t = lim.locked_until + 1
    t += LoginLimiter.STRIKE_DECAY_S + 1
    _fail_globally(lim, t)
    assert lim.locked_until - t == LoginLimiter.BASE_LOCK_S


def test_paused_settle_is_refused_and_not_counted():
    lim = LoginLimiter()
    assert lim.reserve("192.0.2.1", now=990.0)  # reserved before the pause began
    _fail_globally(lim, 1000.0)
    assert not lim.settle("192.0.2.1", True, now=1001.0)  # pause applies to the result too
    assert lim.locked_until == 1000.0 + LoginLimiter.BASE_LOCK_S  # and nothing was added


async def _slow_then_correct(app, slow, delay_s):
    async def slow_body():
        await asyncio.sleep(delay_s)
        yield b"username=judge&password=nope"

    async def one(peer, content, headers):
        transport = httpx.ASGITransport(app=app, client=peer)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
            return (await c.post("/login", content=content, headers=headers)).status_code

    form = {"content-type": "application/x-www-form-urlencoded"}
    slow_tasks = [asyncio.create_task(one((f"198.51.100.{i + 1}", 1), slow_body(), form))
                  for i in range(slow)]
    await asyncio.sleep(0.05)  # every slow request is in flight now
    correct = await one(("192.0.2.200", 1), f"username=judge&password={PASSWORD}", form)
    return correct, await asyncio.gather(*slow_tasks)


def test_slow_bodies_do_not_use_up_the_global_budget(app, monkeypatch):
    monkeypatch.setattr("studio.app.LOGIN_BODY_TIMEOUT_S", 0.3)
    correct, slow = asyncio.run(
        _slow_then_correct(app, LoginLimiter.GLOBAL_MAX_FAILURES + 5, delay_s=2.0))
    assert correct == 200
    assert set(slow) == {408}  # timed out reading the body, not counted as global failures
    assert app.state.limiter.locked_until == 0.0


def test_table_flood_does_not_reset_blocked_prefix():
    lim = LoginLimiter(max_attempts=5, window_s=900, max_clients=10)
    for _ in range(5):
        assert lim.reserve("203.0.113.1", now=1.0)
    assert not lim.reserve("203.0.113.1", now=2.0)
    for i in range(100):
        lim.reserve(f"198.51.100.{i}", now=3.0)
    assert not lim.reserve("203.0.113.1", now=4.0)


def test_full_table_of_blocked_clients_fails_closed():
    lim = LoginLimiter(max_attempts=1, window_s=900, max_clients=3)
    for i in range(3):
        assert lim.reserve(f"203.0.113.{i}", now=1.0)  # each now at its limit
    assert not lim.reserve("192.0.2.1", now=2.0)
    assert lim.reserve("192.0.2.1", now=1000.0)  # entries expired: room again


def test_revoked_key_cannot_be_revived_by_a_new_session(app, client):
    login(client)
    key = Ed25519PrivateKey.generate()
    assert _register(client, key).status_code == 200
    client.post("/logout")
    login(client)
    r = _register(client, key)
    assert r.status_code == 409 and r.json()["error"] == "key_conflict"
    assert _register(client, Ed25519PrivateKey.generate()).status_code == 200  # fresh key ok


def test_key_of_one_session_cannot_be_claimed_by_another(app, tmp_path):
    key = Ed25519PrivateKey.generate()
    with TestClient(app, base_url="https://testserver") as a, \
            TestClient(app, base_url="https://testserver") as b:
        login(a)
        login(b)
        approver = _register(a, key).json()["approver"]
        assert _register(b, key).status_code == 409
        from studio import approvals as ap
        with pytest.raises(ap.WrongSession):
            _approve_with(app, key, approver, _sid(b))
        _approve_with(app, key, approver, _sid(a))
