"""The public tour (``/try``): one recorded run, replayed in the browser, no account.

``build(set_name)`` runs a replay set (``fixtures/replay/<set>/replay.json``) through the
app offline, exactly like ``studio demo`` (no network, no key, a throwaway data directory
and ledger key), stops where the owner would sign, and returns what the tour shows: the
brief, the confirmed Brief v1, the plan, the copy variants, the images, both reviewers'
verdicts, the approval the owner would sign (nothing is signed) and the run's receipts with
the throwaway ledger key that signed them. ``python -m studio.tour`` writes it to
``fixtures/tour/<set>.json``; the route only ever reads that file.

``public(data)`` is what ``/try/data.json`` serves: the file plus, for every hashed object,
the exact canonical bytes as a string, so the browser can re-hash them with SubtleCrypto
(JavaScript cannot reproduce Python's float formatting, e.g. ``6.0``) and compare the
parsed bytes with the object shown.

Images are the replay set's own recorded model outputs (``fixtures/replay/blobs``, the
512-px copies); ``image_names(set)`` is the allowlist the image route serves from.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import sqlite3
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from studio import replay
from studio.receipts import GENESIS, canonical

ROOT = Path(__file__).resolve().parent.parent
TOUR_DIR = ROOT / "fixtures" / "tour"
DEFAULT_SET = "muse"
SCHEMA = "tour/v1"
REPO_URL = "https://github.com/medelin/muse-studio-lite"
_BLOB = re.compile(r"@blob:([0-9a-f]{32}\.png)(?:#(\d{1,5})x(\d{1,5}))?")


def tour_path(set_name: str = DEFAULT_SET, root: Path = TOUR_DIR) -> Path:
    replay.set_path(set_name)  # the same name rule (raises on anything else)
    return root / f"{set_name}.json"


def set_images(set_name: str, root: Path = replay.REPLAY_DIR) -> list[dict[str, Any]]:
    """The images the set's image calls returned, in recorded order: ``{blob, width,
    height}`` (the size the API sent; the stored copy is at most 512 px)."""
    out = []
    for item in replay.load_set(set_name, root).get("interactions") or []:
        route = str(item.get("route", ""))
        if "images/edits" not in route and "image_generation" not in route:
            continue
        for m in _BLOB.finditer(json.dumps(item.get("response"))):
            out.append({"blob": m.group(1), "width": int(m.group(2) or 0),
                        "height": int(m.group(3) or 0)})
    return out


@lru_cache(maxsize=8)
def image_names(set_name: str = DEFAULT_SET) -> frozenset[str]:
    return frozenset(i["blob"] for i in set_images(set_name))


# -- build (offline, at authoring time) ----------------------------------------------


def build(set_name: str = DEFAULT_SET) -> dict[str, Any]:
    """Replay the set up to the owner's signature and collect what the tour shows."""
    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi.testclient import TestClient

    from studio import demo
    from studio.app import create_app
    from studio.receipts import public_key_raw
    from studio.routes_studio import replay_clients
    from studio.settings import Settings

    meta = replay.load_set(set_name)
    if "draft_text" not in meta:
        raise ValueError(f"set {set_name!r} has no Brief v1 draft")

    def no_network(self: Any, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network call while building the tour: {request.url}")

    real = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = no_network  # type: ignore[method-assign]
    try:
        with tempfile.TemporaryDirectory() as tmp:
            password = "Tr0-" + secrets.token_urlsafe(24)
            env = {"STUDIO_DATA_DIR": tmp, "JUDGE_PASSWORD": password,
                   "SESSION_SECRET": secrets.token_urlsafe(32), "COOKIE_SECURE": "1",
                   "META_ADAPTER": "mock", "STUDIO_REPLAY": "1",
                   "STUDIO_REPLAY_SET": set_name, "MODEL_API_KEY": ""}
            for field, name in (("account_currency", "STUDIO_ACCOUNT_CURRENCY"),
                                ("product_photo", "STUDIO_PRODUCT_PHOTO"),
                                ("concept", "STUDIO_PRODUCT_CONCEPT")):
                if meta.get(field):
                    env[name] = (str(ROOT / meta[field]) if field == "product_photo"
                                 else str(meta[field]))
            settings = Settings.load(env)
            app = create_app(settings)
            transport = replay.ReplayTransport.from_set(set_name)
            app.state.muse, app.state.open_weights = replay_clients(
                settings, app.state.ledger, transport)
            with TestClient(app, base_url="https://testserver") as c:
                assert c.post("/login", json={"username": "judge", "password": password}
                              ).status_code == 200
                key = Ed25519PrivateKey.generate()
                raw = base64.urlsafe_b64encode(public_key_raw(key.public_key())
                                               ).rstrip(b"=").decode()
                approver = c.post("/api/keys", json={"public_key": raw}).json()["approver"]
                draft = c.post("/api/briefs/draft", json={
                    "text": meta["draft_text"], "lang": meta.get("lang", "en")}).json()
                res = demo.resolutions(draft["brief"]["open_questions"],
                                       meta.get("answers") or {})
                started = c.post("/api/runs", json={"brief_id": draft["brief_id"],
                                                    "brief_v1": draft["brief"],
                                                    "resolutions": res})
                assert started.status_code == 202, started.text[:300]
                run_id = started.json()["run_id"]
                data = demo._wait(c, run_id, 60)
                state = data["state"]
                if data["status"] != "awaiting_approval":
                    raise RuntimeError(f"replay ended {data['status']!r}, not at approval")
                payload = c.get(f"/api/approvals/{state['pending_id']}/payload",
                                params={"approver": approver}).json()
                export = c.get("/api/receipts.jsonl").text
                ledger_key = c.get("/api/ledger-key").json()
            conn = sqlite3.connect(Path(tmp) / "studio.db")
            confirmed = json.loads(conn.execute(
                "SELECT brief_v1 FROM briefs WHERE run_id = ?", (run_id,)).fetchone()[0])
            conn.close()
    finally:
        httpx.HTTPTransport.handle_request = real  # type: ignore[method-assign]
    if transport.stats["miss"]:
        raise RuntimeError(f"replay missed {transport.stats['miss']} call(s)")

    lines = [json.loads(x) for x in export.splitlines() if x.strip()]
    receipts = [r for r in lines if "manifest" not in r and r["run_id"] == run_id]
    (manifest,) = [r["manifest"] for r in lines if "manifest" in r]
    recorded = set_images(set_name)
    by_sha = {_sha_of(i): i for i in recorded}
    images = []
    for im in (state.get("creatives") or {}).get("images") or []:
        src = by_sha.get(im["sha256"])
        if src is None:
            raise RuntimeError(f"image {im['placement']} is not a recorded output of the set")
        images.append({"placement": im["placement"], "width": im["width"],
                       "height": im["height"], "sha256": im["sha256"], "blob": src["blob"]})
    review = state.get("review") or {}
    return {
        # derived from a recorded set (replayed, never re-asked): its provenance carries over
        "_provenance": "recorded", "schema": SCHEMA, "set": set_name,
        "recorded": meta.get("recorded", ""),
        "lang": meta.get("lang", "en"), "recorded_cost_usd": meta.get("cost_usd", 0),
        "run_id": run_id, "run_cost_usd": state.get("cost_usd", 0),
        "brief": {"text": meta["draft_text"],
                  "draft_questions": draft["brief"]["open_questions"],
                  "resolutions": res, "confirmed": confirmed},
        "plan": state.get("plan") or {},
        "angle_index": state.get("angle_index", 0),
        "copy": state.get("copy") or {},
        "images": images,
        "review": {k: review.get(k) for k in ("verdict", "primary_verdict", "second_opinion",
                                              "disagreements", "formats", "variant_index")},
        "producer_summary": state.get("summary", ""),
        "approval": {"fields": payload["fields"], "content": payload["content"],
                     "content_sha256": payload["content_sha256"],
                     "expires_ts": payload["expires_ts"]},
        "receipts": receipts,
        "manifest": manifest,
        "ledger_key": ledger_key["public_key"],
        "epoch": ledger_key["epoch"],
    }


def _sha_of(image: dict[str, Any]) -> str:
    from studio.receipts import sha256_hex

    png = (replay.BLOBS / image["blob"]).read_bytes()
    if image["width"]:
        png = replay._upscale(png, f"{image['width']}x{image['height']}")
    return sha256_hex(png)


# -- serve ----------------------------------------------------------------------------


@lru_cache(maxsize=4)
def load(set_name: str = DEFAULT_SET, root: Path = TOUR_DIR) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(tour_path(set_name, root).read_text())
    if data.get("schema") != SCHEMA or data.get("set") != set_name:
        raise ValueError(f"not a {SCHEMA} file for set {set_name!r}")
    return data


def receipt_body(receipt: dict[str, Any]) -> str:
    """The canonical bytes a receipt's ``hash`` is the SHA-256 of (as text)."""
    return canonical({k: v for k, v in receipt.items() if k not in ("hash", "sig")}).decode()


def manifest_body(manifest: dict[str, Any]) -> str:
    return canonical({"type": "manifest",
                      **{k: manifest[k] for k in ("seq", "ts", "epoch", "runs")}}).decode()


def approval_message(fields: dict[str, Any]) -> str:
    from studio.receipts import approval_message as message

    return message(fields).decode()


@lru_cache(maxsize=4)
def public(set_name: str = DEFAULT_SET) -> dict[str, Any]:
    """``/try/data.json``: the tour plus the canonical text of every hashed object."""
    data = load(set_name)
    return {**data, "genesis": GENESIS,
            "canonical": {
                "receipts": [receipt_body(r) for r in data["receipts"]],
                "manifest": manifest_body(data["manifest"]),
                "brief": canonical(data["brief"]["confirmed"]).decode(),
                "content": canonical(data["approval"]["content"]).decode(),
                "message": approval_message(data["approval"]["fields"]),
            }}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    set_name = args[0] if args else DEFAULT_SET
    data = build(set_name)
    path = tour_path(set_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"{path.relative_to(ROOT)}: {len(data['receipts'])} receipts, "
          f"{len(data['images'])} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
