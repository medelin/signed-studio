"""Record the day-2 cassettes with the real Model API (operator machine only).

    .venv/bin/python scripts/record_day2.py copy [sam image review_spark producer ...]

Spend is tracked by the studio budget module against a persistent receipt ledger in
``~/.local/state/muse-studio-lite/record/`` (outside the repo; cap
``RECORD_BUDGET_USD``, default $1.50 per UTC day), so repeated runs cannot exceed the cap.
Cassettes keep only the request path + JSON body and the response
status + JSON body (or SSE events); no headers. Inline images in requests become
``data:<mime>;sha256,<hex>``; base64 image results larger than 4 KB are written as PNG
files under ``fixtures/cassettes/files/`` and referenced as ``@file:<path>``. A cassette
containing the API key is refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio import db  # noqa: E402
from studio.muse.client import MuseClient  # noqa: E402
from studio.receipts import Ledger  # noqa: E402
from studio.settings import Budget, Settings  # noqa: E402

OUT = ROOT / "fixtures" / "cassettes"
FILES = OUT / "files"
_DATA_URL = re.compile(r"^data:([a-z]+/[a-z0-9.+-]+);base64,(.*)$", re.S)


def _scrub_request(value: Any) -> Any:
    if isinstance(value, str):
        m = _DATA_URL.match(value)
        if m:
            digest = hashlib.sha256(base64.b64decode(m.group(2))).hexdigest()
            return f"data:{m.group(1)};sha256,{digest}"
        return value
    if isinstance(value, dict):
        return {k: _scrub_request(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_request(v) for v in value]
    return value


class Recorder(httpx.BaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.interactions: list[dict[str, Any]] = []
        self.name = "unnamed"
        self._files = 0

    def _store_b64(self, value: Any) -> Any:
        if isinstance(value, str) and len(value) > 4096:
            try:
                raw = base64.b64decode(value, validate=True)
            except ValueError:
                return value
            FILES.mkdir(parents=True, exist_ok=True)
            self._files += 1
            ext = "png" if raw.startswith(b"\x89PNG") else "bin"
            path = FILES / f"{self.name}-{self._files}.{ext}"
            path.write_bytes(raw)
            return "@file:" + str(path.relative_to(ROOT))
        if isinstance(value, dict):
            return {k: self._store_b64(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._store_b64(v) for v in value]
        return value

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        body = response.read()
        path = request.url.path
        for base in ("/v1",):
            if path.startswith(base):
                path = path[len(base):]
        recorded: dict[str, Any] = {"status": response.status_code}
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            events = []
            for line in body.decode().splitlines():
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                    events.append(json.loads(line[5:]))
            recorded["sse"] = self._store_b64(events)
        else:
            recorded["json"] = self._store_b64(json.loads(body))
        self.interactions.append({
            "request": {"method": request.method, "path": path,
                        "json": _scrub_request(json.loads(request.content))},
            "response": recorded,
        })
        return httpx.Response(response.status_code, headers=response.headers, content=body)


def save(name: str, rec: Recorder, key: str, extra: dict[str, Any] | None = None) -> None:
    text = json.dumps({**(extra or {}), "interactions": rec.interactions}, indent=2,
                      ensure_ascii=False) + "\n"
    if key and key in text:
        raise SystemExit(f"refusing to write {name}: API key found in cassette")
    (OUT / f"{name}.json").write_text(text)
    for it in rec.interactions:
        r = it["response"]
        usage = r.get("json", {}).get("usage") if "json" in r else "sse"
        print(name, r["status"], json.dumps(usage))
    rec.interactions = []


def open_client(rec: Recorder) -> tuple[MuseClient, str]:
    settings = Settings.load(env_file=ROOT / ".env")
    # Outside the repo: the ledger key must never sit in the working tree.
    rec_dir = Path(os.environ.get("RECORD_STATE_DIR",
                                  Path.home() / ".local/state/muse-studio-lite/record"))
    rec_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = db.connect(rec_dir / "record.db")
    db.init(conn)
    ledger: Ledger = db.open_ledger(conn, rec_dir / "ledger.key")
    limit = float(os.environ.get("RECORD_BUDGET_USD", "1.50"))
    budget = Budget.from_ledger(limit, ledger)
    client = MuseClient(settings.model_api_key, settings.model_api_base_url, ledger=ledger,
                        budget=budget, transport=rec, timeout=300.0)
    return client, settings.model_api_key


STEPS: dict[str, Callable[[MuseClient, Recorder, str], None]] = {}


def step(name: str) -> Callable[[Callable[[MuseClient, Recorder, str], None]],
                                Callable[[MuseClient, Recorder, str], None]]:
    def deco(fn: Callable[[MuseClient, Recorder, str], None]) -> Callable[..., None]:
        STEPS[name] = fn
        return fn
    return deco


@step("copy")
def _copy(client: MuseClient, rec: Recorder, key: str) -> None:
    from studio.agents import copywriter
    from studio.agents.schemas import Angle

    rec.name = "copy_contributor"
    angle = Angle(name="Morning ritual", hook="Your first coffee deserves a real mug",
                  persona="home-office worker, 30-45")
    copywriter.write(client, angle, "home-office worker", run_id="rec", brief="")
    save("copy_contributor", rec, key)


PRODUCT_PROMPT = (
    "Studio product photo of one hand-thrown stoneware coffee mug with a clay-red glaze and "
    "a round handle, standing alone in the centre on a plain off-white seamless background, "
    "soft daylight from the left, gentle shadow, sharp focus, no text, no logo, no people."
)


@step("product")
def _product(client: MuseClient, rec: Recorder, key: str) -> None:
    """The synthetic, brand-neutral demo product photo (model-generated, see CHANGELOG)."""
    from studio.muse import image

    rec.name = "image_generate"
    out = image.generate(client, PRODUCT_PROMPT, size="1024x1024", run_id="rec",
                         agent="art_director")
    asset = ROOT / "fixtures" / "assets" / "product-demo.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(out.png)
    for it in rec.interactions:  # reference the asset instead of a second copy
        for d in it["response"]["json"]["data"]:
            ref = d.get("b64_json", "")
            if ref.startswith("@file:"):
                (ROOT / ref[6:]).unlink()
                d["b64_json"] = "@file:" + str(asset.relative_to(ROOT))
    print("product photo", out.width, "x", out.height, len(out.png), "bytes")
    save("image_generate", rec, key)


@step("sam")
def _sam(client: MuseClient, rec: Recorder, key: str) -> None:
    from studio.muse import sam

    rec.name = "sam_cutout"
    photo = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
    cut = sam.cutout(client, photo, "mug", run_id="rec")
    (ROOT / "fixtures" / "assets" / "product-demo-cutout.png").write_bytes(cut.crop_png)
    print("box", cut.box, "mask", cut.mask_px, "fg", cut.foreground_pixels, "n", cut.matches)
    save("sam_cutout", rec, key)


@step("art")
def _art(client: MuseClient, rec: Recorder, key: str) -> None:
    import tempfile

    from studio.agents import art_director

    rec.name = "image_series"
    photo = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        cs = art_director.run(client, photo, "A Mug for Slow Mornings", run_id="rec",
                              concept="mug", data_dir=Path(tmp))
    for im in cs.images:
        print(im.placement, im.size, im.width, "x", im.height, im.response_id)
    save("image_series", rec, key)


@step("review_spark")
def _review_spark(client: MuseClient, rec: Recorder, key: str) -> None:
    from studio.agents import reviewer
    from studio.agents.schemas import CopyPack

    rec.name = "review_spark"
    image_png = (OUT / "files" / "image_series-1.png").read_bytes()
    copy = json.loads((OUT / "copy_contributor.json").read_text())
    content = copy["interactions"][0]["response"]["json"]["choices"][0]["message"]["content"]
    variant = CopyPack.model_validate_json(content).variants[0]
    result = reviewer.review(client, image_png, variant, run_id="rec")
    print(result.model_dump_json(indent=1))
    save("review_spark", rec, key)


def main() -> None:
    rec = Recorder()
    client, key = open_client(rec)
    before = client.budget.spent() if client.budget else 0.0
    for name in sys.argv[1:]:
        STEPS[name](client, rec, key)
    if client.budget:
        print(f"this run ${client.budget.spent() - before:.5f}; "
              f"today ${client.budget.spent():.5f} of ${client.budget.limit_usd:.2f}")


if __name__ == "__main__":
    main()
