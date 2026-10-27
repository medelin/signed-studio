"""Record Meta Model API cassettes for the contract tests (operator machine only).

Makes 4 small real calls with the key from ``.env`` (mode 600); pass cassette names
(``spark_structured spark_tool_call spark_vision``) to re-record only those. Cassettes keep
only the request path and JSON body and the response status and JSON body; no headers are
written, and the file is refused if the key would appear in it.

    .venv/bin/python scripts/record_cassettes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.muse.client import MuseClient  # noqa: E402
from studio.muse.structured import structured  # noqa: E402
from studio.muse.vision import chat_image_part  # noqa: E402
from studio.receipts import Ledger  # noqa: E402
from studio.settings import Budget, Settings  # noqa: E402

OUT = ROOT / "fixtures" / "cassettes"


class AdLine(BaseModel):
    headline: str
    cta: str


class Recorder(httpx.BaseTransport):
    def __init__(self) -> None:
        self.inner = httpx.HTTPTransport()
        self.interactions: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.inner.handle_request(request)
        response.read()
        base = "/v1"
        path = request.url.path
        if path.startswith(base):
            path = path[len(base):]
        self.interactions.append({
            "request": {"method": request.method, "path": path,
                        "json": json.loads(request.content)},
            "response": {"status": response.status_code, "json": response.json()},
        })
        return response


def save(name: str, rec: Recorder, key: str) -> None:
    text = json.dumps({"interactions": rec.interactions}, indent=2, ensure_ascii=False) + "\n"
    if key and key in text:
        raise SystemExit(f"refusing to write {name}: API key found in cassette")
    (OUT / f"{name}.json").write_text(text)
    for it in rec.interactions:
        print(name, it["response"]["status"], json.dumps(it["response"]["json"].get("usage")))
    rec.interactions = []


def main() -> None:
    settings = Settings.load(env_file=ROOT / ".env")
    key = settings.model_api_key
    rec = Recorder()
    client = MuseClient(key, settings.model_api_base_url, ledger=Ledger(), budget=Budget(0.5),
                        transport=rec)
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:]) or {"spark_structured", "spark_tool_call", "spark_vision"}

    if "spark_structured" in only:
        _structured(client, rec, key)
    if "spark_tool_call" in only:
        _tool_call(client, rec, key)
    if "spark_vision" in only:
        _vision(client, rec, key)
    print(f"total cost ${client.budget.spent():.5f}" if client.budget else "")


def _structured(client: MuseClient, rec: Recorder, key: str) -> None:
    structured(client, "muse-spark-1.3",
               [{"role": "user", "content": "Write one ad line for a sourdough bakery."}],
               AdLine, tier="standard", tier_reason="test", run_id="rec", agent="copywriter",
               max_tokens=400, reasoning_effort="minimal")
    save("spark_structured", rec, key)


def _tool_call(client: MuseClient, rec: Recorder, key: str) -> None:
    tools = [{
        "type": "function", "name": "lookup_price",
        "description": "Price of a product in PLN.",
        "parameters": {"type": "object", "properties": {"product": {"type": "string"}},
                       "required": ["product"], "additionalProperties": False},
    }]
    first = client.respond("muse-spark-1.3",
                           "How much is the sourdough subscription? Use the tool.",
                           tools=tools, reasoning={"effort": "low"}, run_id="rec",
                           agent="producer", max_output_tokens=300)
    call = first.tool_calls[0]
    client.respond("muse-spark-1.3",
                   [{"type": "function_call_output", "call_id": call.call_id,
                     "output": "49 PLN"}],
                   tools=tools, previous_response_id=first.response_id,
                   reasoning={"effort": "low"}, run_id="rec", agent="producer",
                   max_output_tokens=300)
    save("spark_tool_call", rec, key)


def _vision(client: MuseClient, rec: Recorder, key: str) -> None:
    image = (ROOT / "fixtures" / "images" / "red-16.png").read_bytes()
    client.chat("muse-spark-1.3",
                [{"role": "user", "content": [
                    {"type": "text", "text": "What colour is this square? One word."},
                    chat_image_part(image)]}],
                run_id="rec", agent="reviewer", max_tokens=1500, reasoning_effort="low")
    save("spark_vision", rec, key)


if __name__ == "__main__":
    main()
