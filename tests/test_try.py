"""The public tour (``/try``): public without an account, read-only, nothing from the live
database or settings, images only from the replay set, data equal to the recorded set, and
a chain the browser can verify from the canonical bytes it is given."""

import hashlib
import json
import re
import secrets
import sqlite3

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from studio import app as app_module
from studio import auth, replay, tour
from studio.app import create_app
from studio.receipts import GENESIS, canonical, sha256_hex
from studio.settings import Settings
from studio.ui import i18n

UI = app_module.UI
SECRETS = {
    "JUDGE_PASSWORD": "Jd7-" + secrets.token_urlsafe(24),
    "SESSION_SECRET": secrets.token_urlsafe(32),
    "MODEL_API_KEY": "mk-" + secrets.token_hex(16),
    "BASETEN_API_KEY": "bt-" + secrets.token_hex(16),
    "META_ACCESS_TOKEN": "EAAG" + secrets.token_hex(20),
    "META_AD_ACCOUNT_ID": "act_" + "".join(secrets.choice("123456789") for _ in range(15)),
    "META_PAGE_ID": "".join(secrets.choice("123456789") for _ in range(15)),
    "META_PAGE_TOKEN": "EAAP" + secrets.token_hex(20),
    "META_APP_SECRET": secrets.token_hex(16),
    "GOOGLE_ADS_REFRESH_TOKEN": "1//" + secrets.token_hex(16),
    "GOOGLE_ADS_CLIENT_SECRET": "GOCSPX-" + secrets.token_hex(10),
}
PUBLIC = ("/try", "/pl/try", "/try?lang=pl", "/try/data.json")
BLOB = re.compile(r"^[0-9a-f]{32}\.png$")


@pytest.fixture
def app(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "v"),
                                  "STUDIO_REQUIRE_LOGIN": "1", **SECRETS}))
    return a


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def data():
    return tour.public(tour.DEFAULT_SET)


def test_the_tour_is_public_while_everything_else_needs_a_login(client, data):
    for path in PUBLIC + tuple(f"/try/img/{im['blob']}" for im in data["images"]):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, path
    assert client.get("/studio", follow_redirects=False).status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 303
    # narrowly: nothing next to it becomes public
    for path in ("/try/", "/tryx", "/try/data.jsonx", "/pl/try/x", "/try/other.json"):
        assert client.get(path, follow_redirects=False).status_code in (303, 401), path
    assert {"/try", "/pl/try", "/try/data.json"} <= auth.PUBLIC_PATHS
    assert "/try/" not in auth.PUBLIC_PREFIXES and "/try" not in auth.PUBLIC_PREFIXES


def test_no_secret_account_or_live_ledger_data_leaks(app, client, data):
    bodies = [client.get(p).text for p in PUBLIC]
    live = app.state.ledger
    for body in bodies:
        for name, value in SECRETS.items():
            assert value not in body, name
        assert live.epoch not in body  # the tour shows its own throwaway ledger
        assert str(app.state.settings.data_dir) not in body
    assert data["epoch"] != live.epoch


def test_cache_headers_never_let_a_shared_cache_keep_a_cookie(client, data):
    r = client.get("/try/data.json")
    assert r.headers["cache-control"].startswith("private, max-age=")
    r = client.get(f"/try/img/{data['images'][0]['blob']}")
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"].startswith("private,")


def test_the_tour_makes_no_call_and_writes_nothing(app, client, monkeypatch):
    def no_network(self, request):
        raise AssertionError(f"network call from the tour: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    db = app.state.settings.data_dir / "studio.db"
    conn = sqlite3.connect(db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    def counts():
        query = 'SELECT count(*) FROM "{}"'  # table names from sqlite_master, not input
        return {t: conn.execute(query.format(t)).fetchone()[0] for t in tables}

    before, seq = counts(), app.state.ledger.seq()
    for path in PUBLIC:
        client.get(path)
    assert counts() == before and app.state.ledger.seq() == seq
    conn.close()


@pytest.mark.parametrize("name", [
    "../replay.json", "..%2Fmuse%2Freplay.json", "%2e%2e%2fstudio.db",
    "5380d46ee37ab1750e7840c785d83e70.txt",  # a blob of the set, but not an image
    "324162357B3554699511A8EB908F0C1B.png",  # case matters
    "324162357b3554699511a8eb908f0c1b.png/..",
    "00000000000000000000000000000000.png",
])
def test_image_route_refuses_traversal_and_unknown_names(client, name):
    r = client.get(f"/try/img/{name}", follow_redirects=False)
    assert r.status_code in (303, 401, 404)
    assert b"PNG" not in r.content[:8]


def test_only_images_of_the_tour_set_are_served(client):
    other = {b for s in ("demo", "office", "pushy", "brief") for b in tour.image_names(s)}
    foreign = sorted(other - tour.image_names(tour.DEFAULT_SET))
    assert foreign, "the other sets have images of their own"
    assert client.get(f"/try/img/{foreign[0]}").status_code == 404
    assert all(BLOB.match(n) for n in tour.image_names(tour.DEFAULT_SET))


def test_the_tour_is_the_recorded_set(data):
    meta = replay.load_set(data["set"])
    assert data["brief"]["text"] == meta["draft_text"]
    assert data["recorded"] == meta["recorded"]
    answers = []
    for item in meta["interactions"]:
        resp = item["response"].get("json") or {}
        for choice in resp.get("choices") or []:
            try:
                answers.append(json.loads(choice["message"]["content"]))
            except (KeyError, TypeError, ValueError):
                pass
    # the copy variants, the plan angles and the reviewers' verdicts are recorded answers
    assert any(a.get("variants") == data["copy"]["variants"] for a in answers)
    assert any(a.get("angles") == data["plan"]["angles"] for a in answers)
    recorded = [a for a in answers if "verdict" in a]
    for f in data["review"]["formats"]:
        for who in ("primary", "second"):
            # the recorded answer, plus findings of the studio's own format rules (F1);
            # levels and sources are re-derived by code from the rules table
            shown = f[who]
            seen = [(x["rule"], x["evidence"]) for x in shown["findings"]]
            assert any(a.get("summary") == shown["summary"]
                       and all((x["rule"], x["evidence"]) in seen
                               for x in a.get("findings") or [])
                       for a in recorded), (f["placement"], who)
            assert all(x["source"] for x in shown["findings"])
    # the recorded P5 disagreement is a tip on Meta now: tips are not disagreements
    assert data["review"]["verdict"] == "OK" and data["review"]["disagreements"] == []
    # the images are the set's own outputs: the full-size hash is the recorded blob's
    for im in data["images"]:
        assert im["blob"] in tour.image_names(data["set"])
        png = replay._upscale((replay.BLOBS / im["blob"]).read_bytes(),
                              f"{im['width']}x{im['height']}")
        assert sha256_hex(png) == im["sha256"]
    assert {i["sha256"] for i in data["approval"]["content"]["images"]} == {
        i["sha256"] for i in data["images"]}
    assert len(data["copy"]["variants"]) == 3


def test_the_chain_verifies_from_the_bytes_the_browser_gets(data):
    key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(data["ledger_key"]))
    prev = GENESIS
    assert data["genesis"] == GENESIS
    for r, body in zip(data["receipts"], data["canonical"]["receipts"], strict=True):
        assert hashlib.sha256(body.encode()).hexdigest() == r["hash"]
        assert json.loads(body) == {k: v for k, v in r.items() if k not in ("hash", "sig")}
        assert json.loads(body)["prev"] == prev
        key.verify(bytes.fromhex(r["sig"]), bytes.fromhex(r["hash"]))
        prev = r["hash"]
    m = data["manifest"]
    key.verify(bytes.fromhex(m["sig"]), data["canonical"]["manifest"].encode())
    assert m["runs"][data["run_id"]] == {"count": len(data["receipts"]), "head": prev}
    fields = data["approval"]["fields"]
    assert sha256_hex(data["canonical"]["content"]) == fields["content_sha256"]
    assert sha256_hex(data["canonical"]["brief"]) == fields["brief_sha256"]
    assert data["canonical"]["content"] == canonical(data["approval"]["content"]).decode()
    message = json.loads(data["canonical"]["message"])
    assert message["type"] == "approval" and all(message[k] == v for k, v in fields.items())
    assert fields["chain_head"] in {r["hash"] for r in data["receipts"]}
    confirmed = [r for r in data["receipts"] if r["kind"] == "brief.confirmed"]
    assert confirmed[0]["details"]["brief_sha256"] == fields["brief_sha256"]
    # nothing was signed: no approval receipt, no external write
    assert not [r for r in data["receipts"] if r["kind"] in ("approval", "external_write")]
    assert all(r["provider"].endswith("-replay") for r in data["receipts"] if r["provider"])


def test_pages_in_both_languages(client):
    en = client.get("/try?lang=en").text
    pl = client.get("/pl/try").text
    assert '<html lang="en" ' in en and i18n.EN["try.notice"] in en
    assert '<html lang="pl" ' in pl and i18n.PL["try.notice"] in pl
    assert i18n.EN["try.notice"] not in pl
    assert 'href="/pl/try"' in en and 'href="/try?lang=en"' in pl
    assert tour.REPO_URL in en and 'href="/login?next=/studio"' in en
    for key in ("try.s.brief", "try.s.approval", "try.verify.run"):
        assert i18n.EN[key] in en and i18n.PL[key] in pl
    assert set(tour_keys("en")) == set(tour_keys("pl"))


def tour_keys(lang):
    return [k for k in i18n.CATALOGS[lang] if k.startswith(("try.", "js.tour_"))]


def test_csp_holds_no_inline_script_or_style(client):
    r = client.get("/try")
    assert r.headers["content-security-policy"] == app_module.CSP
    assert "script-src 'self';" in app_module.CSP
    html = r.text
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert 'type="application/json"' in tag or re.search(r'src="/static/[a-z0-9]+\.js"', tag)
    assert re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", html) is None
    assert " style=" not in html and "onclick" not in html.lower()
    assert '<script src="/static/tour.js" defer></script>' in html
    assert (UI / "static" / "tour.js").is_file() and (UI / "static" / "tour.css").is_file()
    js = (UI / "static" / "tour.js").read_text()
    assert "/try/data.json" in js and not re.search(r"\bfetch\((?!\"/try/data\.json\")", js)
    assert "eval(" not in js and "innerHTML" not in js


def test_landing_and_login_link_to_the_tour(tmp_path):
    a = create_app(Settings.load({"STUDIO_DATA_DIR": str(tmp_path / "o"),
                                  "JUDGE_PASSWORD": SECRETS["JUDGE_PASSWORD"],
                                  "SESSION_SECRET": SECRETS["SESSION_SECRET"]}))
    with TestClient(a, base_url="https://testserver") as c:
        assert 'href="/try"' in c.get("/?lang=en").text
        assert 'href="/pl/try"' in c.get("/?lang=pl").text
        assert 'href="/try"' in c.get("/login?lang=en").text


def test_tour_file_names_its_set_and_schema():
    raw = json.loads(tour.tour_path().read_text())
    assert raw["schema"] == tour.SCHEMA and raw["set"] == tour.DEFAULT_SET
    with pytest.raises(ValueError):
        tour.tour_path("../muse")
