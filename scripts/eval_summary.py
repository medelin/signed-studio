"""Write ``docs/eval-summary.json``: the test counts ``/trust`` shows (``make eval-summary``).

Counted from pytest's own collection, never typed by hand: every collected node id is one
test, and an attack test is one under ``tests/attack/``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "eval-summary.json"


def count(node_ids: list[str]) -> dict[str, object]:
    tests = [n for n in node_ids if "::" in n]
    attack = [n for n in tests if n.startswith("tests/attack/")]
    return {"total_tests": len(tests), "attack_tests": len(attack),
            "attack_files": len({n.split("::")[0] for n in attack}),
            "generated": datetime.now(UTC).strftime("%Y-%m-%d")}


def collect() -> list[str]:
    run = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    if run.returncode not in (0, 5):
        sys.stderr.write(run.stdout[-2000:] + run.stderr[-2000:])
        raise SystemExit(f"pytest collection failed (exit {run.returncode})")
    return run.stdout.splitlines()


def main() -> int:
    summary = count(collect())
    OUT.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"{OUT.relative_to(ROOT)}: {summary['total_tests']} tests, "
          f"{summary['attack_tests']} attack tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
