"""Open-weights agreement: Muse Spark 1.3 vs Muse Spark Open on comment triage.

``--record --env-file .env`` triages every comment of ``fixtures/eval/comments.SYNTHETIC.json``
twice through ``community.triage`` (same prompt, same strict ``Triage`` schema, same code
rules): once on Muse Spark 1.3 (Model API, Standard) and once in open-weights mode on Muse
Spark Open (Baseten, tier ``open``), and writes ``fixtures/eval/triage_open.json``. With
``--glimmer`` (``STUDIO_GLIMMER=1`` + ``GLIMMER_API_KEY``) it also runs the Muse Glimmer
pre-filter; without access the Glimmer column says "not measured", never a number.

Without ``--record`` it prints the agreement table from the recorded file (no network).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COMMENTS = ROOT / "fixtures" / "eval" / "comments.SYNTHETIC.json"
RESULTS = ROOT / "fixtures" / "eval" / "triage_open.json"


def _row(t: Any, receipt: Any) -> dict[str, Any]:
    return {"intent": t.triage.intent, "risk": t.triage.risk,
            "requires_human": t.triage.requires_human, "flags": sorted(t.flags),
            "model": receipt.model, "tokens_in": receipt.tokens_in,
            "tokens_out": receipt.tokens_out, "cost_usd": receipt.cost_usd}


def record(env_file: Path, glimmer: bool = False, max_cost: float = 0.5) -> dict[str, Any]:
    from studio.agents import community
    from studio.muse.client import MuseClient, MuseError
    from studio.muse.glimmer import GlimmerClient
    from studio.muse.openweights import OpenWeightsClient
    from studio.receipts import Ledger
    from studio.settings import Budget, Settings

    settings = Settings.load(env_file=env_file)
    if not settings.model_api_key or not settings.baseten_api_key:
        raise SystemExit("needs MODEL_API_KEY and BASETEN_API_KEY")
    led = Ledger()
    spark = MuseClient(settings.model_api_key, settings.model_api_base_url, ledger=led,
                       budget=Budget(max_cost))
    spark_open = OpenWeightsClient(settings.baseten_api_key, ledger=led)
    glim = GlimmerClient.from_settings(settings, led) if glimmer else None
    out = []
    for c in json.loads(COMMENTS.read_text())["comments"]:
        row: dict[str, Any] = {"id": c["id"], "expected": c["expected"]}
        for name, client, mode in (("spark", spark, False), ("open", spark_open, True)):
            run_id = f"ev_{name}_{c['id']}".replace("-", "_")
            try:
                t = community.triage(client, c["text"], run_id=run_id, open_weights=mode,
                                     prescreen_client=glim if mode else None)
                row[name] = _row(t, [r for r in led.receipts(run_id)
                                     if r.agent == community.AGENT][-1])
                if mode and glim is not None:
                    row["glimmer"] = t.prescreen or {"status": t.prescreen_status}
            except MuseError as exc:
                row[name] = {"error": exc.code}
        print(f"{c['id']:<5} spark={row['spark'].get('intent', row['spark'].get('error')):<10}"
              f" open={row['open'].get('intent', row['open'].get('error'))}")
        out.append(row)
    data = {"_provenance": "recorded", "recorded": time.strftime("%Y-%m-%d", time.gmtime()),
            "comments": out,
            "cost_usd": round(sum(r.cost_usd for r in led.receipts()), 6)}
    data["agreement"] = agreement(data)
    RESULTS.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return data


def _human(row: dict[str, Any]) -> bool:
    return bool(row.get("requires_human"))


def agreement(data: dict[str, Any]) -> dict[str, Any]:
    """Counts over the comments both models answered."""
    rows = [r for r in data["comments"] if "intent" in r.get("spark", {})
            and "intent" in r.get("open", {})]
    n = len(rows)

    def count(pred: Any) -> int:
        return sum(1 for r in rows if pred(r))

    out: dict[str, Any] = {
        "n": n, "failed": len(data["comments"]) - n,
        "same_intent": count(lambda r: r["spark"]["intent"] == r["open"]["intent"]),
        "same_requires_human": count(lambda r: _human(r["spark"]) == _human(r["open"])),
        "same_risk": count(lambda r: r["spark"]["risk"] == r["open"]["risk"]),
        "spark_intent_correct": count(lambda r: r["spark"]["intent"]
                                      == r["expected"]["intent"]),
        "open_intent_correct": count(lambda r: r["open"]["intent"]
                                     == r["expected"]["intent"]),
        "spark_cost_usd": round(sum(r["spark"].get("cost_usd", 0) for r in rows), 6),
        "open_cost_usd": round(sum(r["open"].get("cost_usd", 0) for r in rows), 6),
        "disagreements": [
            {"id": r["id"], "expected": r["expected"]["intent"],
             "spark": r["spark"]["intent"], "open": r["open"]["intent"]}
            for r in rows if r["spark"]["intent"] != r["open"]["intent"]],
    }
    glim = [r for r in rows if isinstance(r.get("glimmer"), dict) and "agrees" in r["glimmer"]]
    out["glimmer"] = ({"n": len(glim), "agrees_all": sum(
        all(r["glimmer"]["agrees"].values()) for r in glim)} if glim else "not measured")
    return out


def table(data: dict[str, Any]) -> str:
    a = data.get("agreement") or agreement(data)
    lines = [f"Muse Spark 1.3 vs Muse Spark Open on {a['n']} comments "
             f"(recorded {data.get('recorded', '?')}, failed calls: {a['failed']}):", "",
             "| Measure | Count |", "|---|---|",
             f"| Same intent | {a['same_intent']}/{a['n']} |",
             f"| Same needs-a-human decision | {a['same_requires_human']}/{a['n']} |",
             f"| Same risk level | {a['same_risk']}/{a['n']} |",
             f"| Intent as a human labelled it: Spark 1.3 | {a['spark_intent_correct']}/{a['n']} |",
             f"| Intent as a human labelled it: Spark Open | {a['open_intent_correct']}/{a['n']} |",
             f"| Muse Glimmer pre-filter agreement | {a['glimmer']} |"]
    if a["disagreements"]:
        lines += ["", "| Comment | Human label | Spark 1.3 | Spark Open |", "|---|---|---|---|"]
        lines += [f"| {d['id']} | {d['expected']} | {d['spark']} | {d['open']} |"
                  for d in a["disagreements"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--record", action="store_true", help="real calls (Model API + Baseten)")
    p.add_argument("--glimmer", action="store_true", help="also run the Glimmer pre-filter")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = p.parse_args(argv)
    data = (record(args.env_file, args.glimmer) if args.record
            else json.loads(RESULTS.read_text()))
    print(table(data))
    print(f"\ncost: ${data['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
