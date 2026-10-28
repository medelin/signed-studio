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
    assert cli.main(["smoke-live", "--env-file", str(tmp_path / "missing.env")]) == 3
    assert "MODEL_API_KEY" in capsys.readouterr().err


def test_smoke_live_respects_read_only(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "test-key-not-a-secret")
    monkeypatch.setenv("STUDIO_READ_ONLY", "1")
    with respx.mock(assert_all_called=False) as router:
        route = router.post(url__startswith=BASE_URL)
        rc = cli.main(["smoke-live", "--env-file", str(tmp_path / "missing.env")])
    assert rc == 3 and not route.called
    assert "STUDIO_READ_ONLY" in capsys.readouterr().err


# -- studio verify -------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from studio import approvals as ap  # noqa: E402
from studio import db  # noqa: E402
from studio.receipts import public_key_raw  # noqa: E402


def _export(tmp_path):
    c = db.connect(tmp_path / "studio.db")
    db.init(c)
    led = db.open_ledger(c, tmp_path / "var" / "ledger.key")
    led.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3")
    key = Ed25519PrivateKey.generate()
    approver = ap.Keys.register(c, "s1", public_key_raw(key.public_key()))
    payload = {"message": "hi"}
    with db.transaction(c):
        p = ap.Pending.create("reply.post", "c-1", payload, ledger=led, run_id="run-1")
        p.save(c)
    approval = ap.Approval(pending_id=p.id, **p.fields(approver),
                           signature=key.sign(p.message(approver)).hex())
    with db.transaction(c):
        ok = ap.verify_and_consume(c, approval, payload, ledger=led, session_id="s1")
    led.append("run-1", agent="media_buyer", kind="external_write",
               output_sha256=p.content_sha256, details=ok.write_details())
    path = tmp_path / "receipts.jsonl"
    path.write_text(led.export_jsonl())
    keys = tmp_path / "approvers.json"
    keys.write_text(json.dumps({a: public_key_raw(k).hex()
                                for a, k in ap.Keys.registry(c).items()}))
    return led, path, keys, tmp_path / "var" / "ledger.pub"


def test_verify_ok_offline(tmp_path, capsys):
    led, path, keys, pub = _export(tmp_path)
    seq = led.manifest()["seq"]
    rc = cli.main(["verify", str(path), "--key-file", str(pub), "--approver-keys", str(keys),
                   "--min-seq", str(seq)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "chain OK, 3 receipts, 1 signatures valid" in out


def test_verify_reports_tampered_id(tmp_path, capsys):
    _, path, keys, pub = _export(tmp_path)
    lines = path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["model"] = "forged"
    lines[0] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    rc = cli.main(["verify", str(path), "--key-file", str(pub), "--approver-keys", str(keys)])
    assert rc == 2
    assert rec["id"] in capsys.readouterr().out


def test_verify_rejects_stale_export(tmp_path, capsys):
    led, path, keys, pub = _export(tmp_path)
    rc = cli.main(["verify", str(path), "--key-file", str(pub), "--approver-keys", str(keys),
                   "--min-seq", str(led.manifest()["seq"] + 1)])
    assert rc == 2 and "older than the latest known" in capsys.readouterr().out


def test_verify_requires_an_approver_registry(tmp_path, capsys):
    _, path, _, pub = _export(tmp_path)
    assert cli.main(["verify", str(path), "--key-file", str(pub)]) == 3
    assert "approver" in capsys.readouterr().err
    rc = cli.main(["verify", str(path), "--key-file", str(pub), "--skip-approver-signatures"])
    assert rc == 0
    assert "approver signatures NOT checked" in capsys.readouterr().out


def test_verify_against_server(tmp_path, capsys):
    led, path, keys, pub = _export(tmp_path)
    base = "https://studio.example"
    with respx.mock() as router:
        router.get(f"{base}/api/ledger-key").mock(return_value=httpx.Response(
            200, json={"public_key": pub.read_text().strip(), "epoch": led.epoch}))
        router.get(f"{base}/api/ledger-manifest").mock(
            return_value=httpx.Response(200, json=led.manifest()))
        router.get(f"{base}/api/approver-keys").mock(
            return_value=httpx.Response(200, json=json.loads(keys.read_text())))
        rc = cli.main(["verify", str(path), "--server", base])
    assert rc == 0, capsys.readouterr()


def test_verify_server_must_be_https(tmp_path, capsys):
    _, path, _, _ = _export(tmp_path)
    assert cli.main(["verify", str(path), "--server", "http://studio.example"]) == 3
    assert "https" in capsys.readouterr().err


def test_verify_reports_unexpected_errors_instead_of_a_traceback(tmp_path, capsys, monkeypatch):
    _, path, keys, pub = _export(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cli, "verify", boom)
    rc = cli.main(["verify", str(path), "--key-file", str(pub), "--approver-keys", str(keys)])
    assert rc == 3
    assert "unexpected" in capsys.readouterr().err


def test_verify_bad_approver_keys_file_is_a_clean_error(tmp_path, capsys):
    _, path, keys, pub = _export(tmp_path)
    keys.write_text("[1, 2]")
    rc = cli.main(["verify", str(path), "--key-file", str(pub), "--approver-keys", str(keys)])
    assert rc == 3
    assert capsys.readouterr().err.startswith("error:")
