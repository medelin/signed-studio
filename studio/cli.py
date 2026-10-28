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

from studio import cli_account
from studio.muse.client import MuseClient, MuseError
from studio.muse.structured import structured
from studio.receipts import Ledger, public_key_raw, verify
from studio.settings import Budget, ConfigError, ReadOnlyMode, Settings

SMOKE_MODEL = "muse-spark-1.3-contributor"
EXIT_INVALID = 2  # verify: the chain is broken; every problem names a receipt (or run)
EXIT_ERROR = 3  # the command could not run: usage, I/O, network, configuration


class _Smoke(BaseModel):
    ok: bool
    word: str


def smoke_live(env_file: Path, client: MuseClient | None = None) -> int:
    """One real strict-JSON call; prints model, usage and cost only (never content or key)."""
    if client is None:
        settings = Settings.load(env_file=env_file)
        client = MuseClient(settings.model_api_key, settings.model_api_base_url,
                            ledger=Ledger(), budget=Budget(0.05),
                            read_only=settings.read_only)  # the kill switch covers smoke too
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


def _studio(env_file: Path) -> tuple[Any, Any, Any, Any]:
    """``(settings, conn, ledger, adapter)`` of the configured studio (its database and
    ledger key under ``STUDIO_DATA_DIR``)."""
    from studio import db
    from studio.meta import adapter as meta_adapter

    settings = Settings.load(env_file=env_file)
    conn = db.connect(settings.data_dir / "studio.db")
    ledger = db.open_ledger(conn, settings.data_dir / "ledger.key")
    return settings, conn, ledger, meta_adapter.make(settings, conn=conn)


def insights_pull(env_file: Path) -> int:
    """One ``insights.snapshot`` per campaign plus recommendations; prints counts only.
    Executing a recommendation still needs the owner's signature in the studio."""
    from studio.meta import measurement

    settings, conn, ledger, adapter = _studio(env_file)
    out = measurement.pull_all(conn, ledger, adapter, settings.max_daily_budget)
    from studio.google import adapter as google_adapter
    from studio.google import store as google_store

    pulled = google_store.pull_all(conn, ledger, google_adapter.make(settings, conn=conn))
    if pulled["google_campaigns"]:
        out.update(pulled)
    print(" ".join(f"{k}={v}" for k, v in out.items()))
    return 0


def leads_poll(env_file: Path) -> int:
    """New instant-form leads (``GET /{form_id}/leads``) → lead store → lead scorer; prints
    counts only."""
    from studio import leadads

    settings, conn, ledger, adapter = _studio(env_file)
    client = MuseClient.from_settings(settings, ledger) if settings.model_available else None
    out = leadads.poll(conn, ledger, adapter, client)
    print(" ".join(f"{k}={v}" for k, v in out.items()))
    return 0


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
    return EXIT_INVALID


def run_demo(args: argparse.Namespace) -> int:
    """The whole loop headless; replay by default, ``--record`` against the real API."""
    import tempfile

    from studio import demo

    key = open_key = ""
    if args.record:
        loaded = Settings.load(env_file=args.env_file)
        key, open_key = loaded.model_api_key, loaded.baseten_api_key
        if not key:
            raise UsageError("--record needs MODEL_API_KEY (in --env-file or the environment)")
        if args.set not in demo.BRIEFS:
            raise UsageError(f"--record knows the briefs {sorted(demo.BRIEFS)}")
    with tempfile.TemporaryDirectory(prefix="studio-demo-") as tmp:
        data_dir = Path(args.data_dir) if args.data_dir else Path(tmp)
        summary = demo.run(args.set, data_dir=data_dir, record=args.record,
                           model_api_key=key, baseten_api_key=open_key,
                           max_cost_usd=args.max_cost)
    print(json.dumps({k: v for k, v in summary.items() if k != "steps"}, indent=1))
    ok = summary["verify_exit"] == 0 and summary["campaign_status"] == "PAUSED"
    return 0 if ok or args.record else EXIT_INVALID


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
    p_demo = sub.add_parser("demo", help="run the whole loop headless and verify it "
                            "(replays a recorded set; --record calls the real Model API)")
    p_demo.add_argument("--set", default="demo", help="replay set (fixtures/replay/<set>)")
    p_demo.add_argument("--data-dir", help="keep the database and export here (default: temp)")
    p_demo.add_argument("--record", action="store_true",
                        help="real Model API calls; writes fixtures/replay/<set>/replay.json")
    p_demo.add_argument("--env-file", type=Path, default=Path(".env"))
    p_demo.add_argument("--max-cost", type=float, default=0.40, help="USD cap for --record")
    cli_account.add_parser(sub)
    p_leads = sub.add_parser("leads-poll", help="fetch new instant-form leads into the lead "
                             "store and scorer (read-only on Meta)")
    p_leads.add_argument("--env-file", type=Path, default=Path(".env"))
    p_ins = sub.add_parser("insights-pull", help="objective-aware insights snapshot of every "
                           "campaign and fresh recommendations (read-only on Meta)")
    p_ins.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    try:
        if args.cmd == "smoke-live":
            return smoke_live(args.env_file)
        if args.cmd == "verify":
            return run_verify(args)
        if args.cmd == "demo":
            return run_demo(args)
        if args.cmd == "account":
            return cli_account.run(args)
        if args.cmd == "leads-poll":
            return leads_poll(args.env_file)
        if args.cmd == "insights-pull":
            return insights_pull(args.env_file)
    except (ConfigError, MuseError, ReadOnlyMode, UsageError, cli_account.UsageError, OSError,
            httpx.HTTPError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - a verifier reports, it never dumps a traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
