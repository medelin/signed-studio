"""``studio`` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from studio.muse.client import MuseClient, MuseError
from studio.muse.structured import structured
from studio.receipts import Ledger
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studio")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_smoke = sub.add_parser("smoke-live", help="one real Model API call; prints usage")
    p_smoke.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    try:
        if args.cmd == "smoke-live":
            return smoke_live(args.env_file)
    except (ConfigError, MuseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
