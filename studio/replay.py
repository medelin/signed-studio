"""Replay mode (``STUDIO_REPLAY=1``): recorded Muse / Baseten / Graph responses, served by
request hash, so the demo runs without network and cannot fail on it.

A replay set is ``fixtures/replay/<name>/replay.json``: what the run was (brief, comments)
and ``interactions[]`` of ``{request_sha256, route, request, response}``.
Large payloads (images, long
event streams) live once in ``fixtures/replay/blobs/<sha256>.<ext>`` and are referenced as
``"@blob:<file>"``; recorded PNGs are downscaled to 512 px / 256 colours like the cassettes.

- ``request_key`` hashes a *normalized* request: method, host, path (digit runs folded),
  and the JSON body with every image replaced by ``<image>`` and volatile values (hex ids,
  run ids, timestamps) folded, so the same brief gives the same key on every run.
- ``ReplayTransport`` serves the recorded response for that key; requests whose text
  carries run-specific data (the analyst reads the ledger, which is new every run) fall
  back to the next recording of the same *route* (host, path, model, tool names, stream)
  in recorded order. Both cycle when exhausted, so a replay can be run again in the same
  process. Anything else is a ``599 replay_miss`` (the client records an error receipt).
- ``RecordingTransport`` wraps a real transport and writes a set; it stores no headers
  (no keys or tokens) and no request images.

Receipts written during a replay name the provider ``<provider>-replay``; their tokens and
cost are those of the recorded call.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
REPLAY_DIR = ROOT / "fixtures" / "replay"
BLOBS = REPLAY_DIR / "blobs"
DEFAULT_SET = "demo"
FIXTURE_MAX_SIDE = 512
_BLOB_MIN = 4096  # base64 strings longer than this become blob files
_TEXT_BLOB_MIN = 64 * 1024  # event-stream bodies longer than this become blob files

_VOLATILE = [
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?"), "<time>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[A-Za-z]{1,4}[_-][0-9a-f]{6,}\b"), "<id>"),
    (re.compile(r"[0-9a-f]{16,}"), "<hex>"),
    (re.compile(r"\b\d{10}(?:\.\d+)?\b"), "<ts>"),
]
_DATA_URL = re.compile(r"data:[\w/+.-]+;(?:base64|sha256),", re.I)
_B64 = re.compile(r"[A-Za-z0-9+/=\s]+")


class ReplayMiss(Exception):
    pass


# -- normalization -------------------------------------------------------------------


def _fold(text: str) -> str:
    for pattern, repl in _VOLATILE:
        text = pattern.sub(repl, text)
    return text


def _looks_b64(value: str) -> bool:
    return len(value) > _BLOB_MIN and bool(_B64.fullmatch(value[:8192]))


def normalize(value: Any) -> Any:
    """The request as the key sees it: images out, volatile values folded."""
    if isinstance(value, str):
        if _DATA_URL.match(value) or _looks_b64(value):
            return "<image>"
        return _fold(value)
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def _body(request: httpx.Request) -> tuple[Any, bool]:
    """``(normalized body, is_json)``."""
    ctype = request.headers.get("content-type", "")
    raw = request.content
    if "json" in ctype or (raw[:1] in (b"{", b"[")):
        try:
            return normalize(json.loads(raw)), True
        except ValueError:
            pass
    if "multipart/" in ctype:
        boundary = ctype.split("boundary=")[-1].strip('"').encode()
        raw = raw.replace(boundary, b"<boundary>") if boundary else raw
    elif "x-www-form-urlencoded" in ctype:
        form = dict(httpx.QueryParams(raw.decode(errors="replace")))
        form.pop("access_token", None)
        form.pop("appsecret_proof", None)
        return normalize(form), True
    return "sha256:" + hashlib.sha256(raw).hexdigest(), False


def route_of(request: httpx.Request, body: Any) -> str:
    path = re.sub(r"\d{5,}", "<n>", request.url.path)
    parts = [request.method, request.url.host, path]
    if isinstance(body, dict):
        tools = sorted(str(t.get("name") or t.get("type", "")) for t in body.get("tools") or []
                       if isinstance(t, dict))
        parts += [str(body.get("model", "")), "stream" if body.get("stream") else "",
                  ",".join(tools)]
    return " ".join(parts)


def request_key(request: httpx.Request) -> tuple[str, str, Any]:
    """``(key, route, normalized body)`` of one request."""
    body, _ = _body(request)
    route = route_of(request, body)
    key = hashlib.sha256(json.dumps({"route": route, "body": body}, sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()
    return key, route, body


# -- blobs ---------------------------------------------------------------------------


def fixture_png(png: bytes) -> bytes:
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    im.thumbnail((FIXTURE_MAX_SIDE, FIXTURE_MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.quantize(256).save(out, "PNG", optimize=True)
    return out.getvalue()


def _put_blob(data: bytes, ext: str, blobs: Path) -> str:
    blobs.mkdir(parents=True, exist_ok=True)
    name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    path = blobs / name
    if not path.exists():
        path.write_bytes(data)
    return f"@blob:{name}"


def _store_payloads(value: Any, blobs: Path) -> Any:
    if isinstance(value, str) and _looks_b64(value):
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            return value
        if raw[:8] == b"\x89PNG\r\n\x1a\n":
            return _put_blob(fixture_png(raw), "png", blobs)
        return _put_blob(raw, "bin", blobs)
    if isinstance(value, dict):
        return {k: _store_payloads(v, blobs) for k, v in value.items()}
    if isinstance(value, list):
        return [_store_payloads(v, blobs) for v in value]
    return value


def _inflate(value: Any, blobs: Path) -> Any:
    if isinstance(value, str) and value.startswith("@blob:"):
        name = value[6:]
        if "/" in name or name.startswith("."):
            raise ReplayMiss(f"bad blob name {name!r}")
        data = (blobs / name).read_bytes()
        return data.decode() if name.endswith(".txt") else base64.b64encode(data).decode()
    if isinstance(value, dict):
        return {k: _inflate(v, blobs) for k, v in value.items()}
    if isinstance(value, list):
        return [_inflate(v, blobs) for v in value]
    return value


# -- transports ----------------------------------------------------------------------


def set_path(name: str = DEFAULT_SET, root: Path = REPLAY_DIR) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", name):
        raise ValueError(f"bad replay set name {name!r}")
    return root / name / "replay.json"


def load_set(name: str = DEFAULT_SET, root: Path = REPLAY_DIR) -> dict[str, Any]:
    return json.loads(set_path(name, root).read_text())  # type: ignore[no-any-return]


class ReplayTransport(httpx.BaseTransport):
    """Serves a recorded set by request key, then by route, cycling when exhausted."""

    def __init__(self, data: dict[str, Any], blobs: Path = BLOBS) -> None:
        self._items: list[dict[str, Any]] = list(data.get("interactions") or [])
        self._blobs = blobs
        self._lock = threading.Lock()
        self._by_key: dict[str, list[int]] = {}
        self._by_route: dict[str, list[int]] = {}
        for i, item in enumerate(self._items):
            self._by_key.setdefault(item["request_sha256"], []).append(i)
            self._by_route.setdefault(item["route"], []).append(i)
        self._used: set[int] = set()
        self._cursor: dict[str, int] = {}
        self.stats = {"exact": 0, "route": 0, "miss": 0}

    @classmethod
    def from_set(cls, name: str = DEFAULT_SET, root: Path = REPLAY_DIR) -> ReplayTransport:
        return cls(load_set(name, root), root / "blobs")

    def _first_unused(self, idx: list[int] | None) -> int | None:
        return next((i for i in idx or [] if i not in self._used), None)

    def _cycle(self, table: dict[str, list[int]], k: str) -> int | None:
        idx = table.get(k)
        if not idx:
            return None
        n = self._cursor.get(k, 0)
        self._cursor[k] = n + 1
        return idx[n % len(idx)]

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key, route, _ = request_key(request)
        with self._lock:
            kind = "exact"
            i = self._first_unused(self._by_key.get(key))
            if i is None:
                kind = "route"
                i = self._first_unused(self._by_route.get(route))
            if i is None:  # every recording of this route served: cycle (a second pass)
                kind = "exact" if key in self._by_key else "route"
                i = self._cycle(self._by_key, key) if kind == "exact" else self._cycle(
                    self._by_route, route)
            if i is None:
                self.stats["miss"] += 1
                return httpx.Response(599, json={"error": {
                    "code": "replay_miss", "message": f"no recording for {route}"}})
            self._used.add(i)
            self.stats[kind] += 1
            resp = self._items[i]["response"]
        if "text" in resp:
            content = _inflate(resp["text"], self._blobs).encode()
            return httpx.Response(resp["status"], content=content,
                                  headers={"content-type": resp.get("content_type",
                                                                    "text/plain")})
        return httpx.Response(resp["status"], json=_inflate(resp.get("json"), self._blobs))


class RecordingTransport(httpx.BaseTransport):
    """Sends through ``inner`` and keeps what replay needs (never headers)."""

    def __init__(self, inner: httpx.BaseTransport | None = None, blobs: Path = BLOBS) -> None:
        self.inner = inner or httpx.HTTPTransport()
        self.blobs = blobs
        self.interactions: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        key, route, body = request_key(request)
        started = time.time()
        response = self.inner.handle_request(request)
        content = response.read()
        ctype = response.headers.get("content-type", "")
        entry: dict[str, Any] = {"request_sha256": key, "route": route, "request": body,
                                 "ts": round(started, 3)}
        try:
            parsed = json.loads(content) if "json" in ctype else None
        except ValueError:
            parsed = None
        if parsed is not None:
            entry["response"] = {"status": response.status_code,
                                 "json": _store_payloads(parsed, self.blobs)}
        else:
            text = content.decode(errors="replace")
            if len(text) > _TEXT_BLOB_MIN:
                text = _put_blob(text.encode(), "txt", self.blobs)
            entry["response"] = {"status": response.status_code, "text": text,
                                 "content_type": ctype.split(";")[0] or "text/plain"}
        with self._lock:
            self.interactions.append(entry)
        return httpx.Response(response.status_code, content=content,
                              headers={"content-type": ctype} if ctype else None)

    def save(self, path: Path, meta: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            items = sorted(self.interactions, key=lambda e: e["ts"])
        data = {"_provenance": "recorded", **meta, "interactions": items}
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
