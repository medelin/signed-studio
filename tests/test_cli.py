import json

import httpx
import respx

from studio import cli
from studio.muse.client import BASE_URL, MuseClient
from studio.receipts import Ledger

CONTENT_WORD = "zebra-content-must-not-print"


def test_smoke_live_prints_usage_only(capsys, tmp_path):
    body = {"id": "x", "model": "muse-spark-1.3-contributor", "choices": [{
        "index": 0, "finish_reason": "stop",
        "message": {"role": "assistant", "refusal": None,
                    "content": json.dumps({"ok": True, "word": CONTENT_WORD})}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42}}
    client = MuseClient("test-key-not-a-secret", ledger=Ledger())
    with respx.mock() as router:
        route = router.post(f"{BASE_URL}/chat/completions").mock(
            return_value=httpx.Response(200, json=body))
        assert cli.smoke_live(tmp_path / ".env", client=client) == 0
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["json_schema"]["strict"] is True
    out = capsys.readouterr().out
    assert "input=12" in out and "output=30" in out and "cost_usd=" in out
    assert CONTENT_WORD not in out and "test-key" not in out


def test_smoke_live_without_key_fails_cleanly(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert cli.main(["smoke-live", "--env-file", str(tmp_path / "missing.env")]) == 2
    assert "MODEL_API_KEY" in capsys.readouterr().err
