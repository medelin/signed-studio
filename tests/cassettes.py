"""Cassette loading for contract tests.

A cassette holds ``interactions[]`` of ``{request: {method, path, json}, response: {status,
json} | {status, sse: [event, ...]}}``. Large base64 payloads are stored as PNG files next to
the cassettes and referenced as ``"@file:<path relative to the repo>"``; they are inflated
back to base64 here. Inline images in recorded requests are replaced by
``data:<mime>;sha256,<hex>`` (tests compare paths, not image bytes).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASSETTES = ROOT / "fixtures" / "cassettes"
FAKE_KEY = "test-key-not-a-secret"


def inflate(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("@file:"):
        return base64.b64encode((ROOT / value[6:]).read_bytes()).decode()
    if isinstance(value, dict):
        return {k: inflate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [inflate(v) for v in value]
    return value


def load(name: str) -> dict[str, Any]:
    return json.loads((CASSETTES / f"{name}.json").read_text())


def interactions(name: str) -> list[dict[str, Any]]:
    return inflate(load(name)["interactions"])


def sse_body(events: list[dict[str, Any]]) -> bytes:
    out = []
    for ev in events:
        out.append(f"event: {ev.get('type', 'message')}\ndata: {json.dumps(ev)}\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode()


def mount(router: Any, name: str, base_url: str, key: str = FAKE_KEY) -> list[dict[str, Any]]:
    """Serve the cassette's responses in order; return the sent request bodies."""
    sent: list[dict[str, Any]] = []
    items = interactions(name)

    def handler(request: httpx.Request) -> httpx.Response:
        i = len(sent)
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == f"Bearer {key}"
        assert request.url.path.endswith(items[i]["request"]["path"]), (
            request.url.path, items[i]["request"]["path"])
        r = items[i]["response"]
        if "sse" in r:
            return httpx.Response(r["status"], content=sse_body(r["sse"]),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(r["status"], json=r["json"])

    router.post(url__startswith=base_url).mock(side_effect=handler)
    return sent
