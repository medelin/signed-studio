"""``studio`` command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel

from studio.muse.client import MuseClient, MuseError
from studio.muse.structured import structured
from studio.receipts import Ledger, public_key_raw, verify
from studio.settings import Budget, ConfigError, Settings

SMOKE_MODEL = "muse-spark-1.3-contributor"


class _Smoke(BaseModel):
    ok: bool
    word: str


def smoke_live(env_file: Path, client: MuseClient | None = None) -> int:
    """One real strict-JSON call; prints model, usage and cost only (never content or key)."""
    if client is None:
        settings = Settings.load(env_file=env_file)
        client = MuseClient(settings.model_api_key, settings.model_api_base_url,
                            ledger=Ledger(), budget=Budget(0.05))
    _, result = structured(
        client, SMOKE_MODEL,
        [{"role": "user", "content": "Reply with ok=true and one English word."}],
        _Smoke, tier="contributor", tier_reason="smoke test, no PII", run_id="smoke",
        agent="smoke", max_tokens=300, reasoning_effort="minimal",
    )
    u = result.usage
    print(f"model={result.model} input={u.input_tokens} cached={u.cached_tokens} "
          f"output={u.output_tokens} reasoning={u.reasoning_tokens} "
          f"cost_usd={result.cost_usd:.6f} ms={result.duration_ms}")
    return 0


class UsageError(Exception):
    pass


def _pubkey(hex_value: str) -> Ed25519PublicKey:
    try:
        raw = bytes.fromhex(hex_value.strip())
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise UsageError("ledger key must be 32 bytes of hex (ledger.pub)") from exc


def _fetch(server: str, path: str) -> Any:
    parts = urlsplit(server)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise UsageError("--server must use https (http only for localhost)")
    resp = httpx.get(server.rstrip("/") + path, timeout=30.0)  # TLS verification stays on
    resp.raise_for_status()
    return resp.json()


def run_verify(args: argparse.Namespace) -> int:
    """Verify an export with the published ledger key, latest seq and approver keys."""
    min_seq = args.min_seq
    key: Ed25519PublicKey | None = None
    approvers: dict[str, Ed25519PublicKey] | None = None
    if args.server:
        server_key = _pubkey(_fetch(args.server, "/api/ledger-key")["public_key"])
        min_seq = max(min_seq, int(_fetch(args.server, "/api/ledger-manifest")["seq"]))
        approvers = {a: _pubkey(h) for a, h in _fetch(args.server, "/api/approver-keys").items()}
        key = server_key
    if args.key or args.key_file:
        local = _pubkey(args.key or Path(args.key_file).read_text())
        if key is not None and public_key_raw(key) != public_key_raw(local):
            raise UsageError("--key/--key-file differs from the server's published ledger key")
        key = local
    if key is None:
        default = Path("var/ledger.pub")
        if not default.exists():
            raise UsageError("no ledger key: pass --server, --key or --key-file")
        key = _pubkey(default.read_text())
    if args.approver_keys:
        data = json.loads(Path(args.approver_keys).read_text())
        if not isinstance(data, dict) or not all(isinstance(h, str) for h in data.values()):
            raise UsageError("--approver-keys must be a JSON object {approver: hex key}")
        approvers = {str(a): _pubkey(h) for a, h in data.items()}
    if approvers is None and not args.skip_approver_signatures:
        raise UsageError("no approver keys: pass --server or --approver-keys "
                         "(or --skip-approver-signatures to check the chain only)")

    report = verify(args.file, key, min_seq=min_seq, approver_keys=approvers)
    if report.ok:
        line = f"chain OK, {report.count} receipts, {report.signatures} signatures valid"
        if approvers is None:
            line += " (approver signatures NOT checked)"
        print(line)
        return 0
    print(f"chain INVALID, {report.count} receipts, {len(report.errors)} problems:")
    for err in report.errors:
        print(f"  - {err}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studio")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_smoke = sub.add_parser("smoke-live", help="one real Model API call; prints usage")
    p_smoke.add_argument("--env-file", type=Path, default=Path(".env"))
    p_verify = sub.add_parser("verify", help="verify an exported receipts.jsonl")
    p_verify.add_argument("file", type=Path)
    p_verify.add_argument("--min-seq", type=int, default=0,
                          help="latest manifest seq you know (from /api/ledger-manifest)")
    p_verify.add_argument("--server", help="studio base URL: fetch key, seq and approver keys")
    p_verify.add_argument("--key", help="ledger public key, hex")
    p_verify.add_argument("--key-file", help="file with the ledger public key (ledger.pub)")
    p_verify.add_argument("--approver-keys", help="JSON from /api/approver-keys")
    p_verify.add_argument("--skip-approver-signatures", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "smoke-live":
            return smoke_live(args.env_file)
        if args.cmd == "verify":
            return run_verify(args)
    except (ConfigError, MuseError, UsageError, OSError, httpx.HTTPError, ValueError,
            KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - a verifier reports, it never dumps a traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
