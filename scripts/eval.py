"""Evaluation numbers for docs/eval.md — offline and re-runnable (``make eval``).

1. ``studio verify`` against N generated tampered exports per tamper class (and N clean
   exports, which must pass).
2. The attack suites (``tests/attack/``), run with pytest, counted per file.
3. Reviewer catch rate on the seeded ad set (``fixtures/eval/ads.SYNTHETIC.json``: 20 ads
   that each break one rule, 5 clean ones), from recorded real Muse Spark 1.3 reviews
   (``fixtures/eval/reviews.json``; ``--record-reviews`` makes the real calls once).
4. Spark vision vs Spark Open agreement (pending until a Baseten key exists).
5. Cost per campaign run, from the receipts of the recorded runs replayed offline.

Usage: ``python scripts/eval.py [--n 25] [--write docs/eval.md] [--record-reviews]``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
import xml.etree.ElementTree as ET  # noqa: S405 - parses the junit file pytest just wrote
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from studio import approvals as ap  # noqa: E402
from studio import db  # noqa: E402
from studio.receipts import Receipt, public_key_raw, sha256_hex, verify  # noqa: E402

EVAL = ROOT / "fixtures" / "eval"
REVIEWS = EVAL / "reviews.json"
OPEN_REVIEWS = EVAL / "reviews_open.json"
P9_CASSETTE = ROOT / "fixtures" / "cassettes" / "review_p9.json"


# -- 1. tamper suite -----------------------------------------------------------------


class _Case:
    def __init__(self, tmp: Path, rnd: random.Random) -> None:
        c = db.connect(tmp / "studio.db")
        db.init(c)
        self.led = db.open_ledger(c, tmp / "ledger.key")
        for _ in range(rnd.randint(1, 4)):
            self.led.append("run-1", agent="producer", kind="model_call", model="muse-spark-1.3",
                            tokens_in=rnd.randint(100, 9000), cost_usd=rnd.random() / 100)
        key = Ed25519PrivateKey.generate()
        approver = ap.Keys.register(c, "s1", public_key_raw(key.public_key()))
        payload = {"message": f"reply {rnd.random()}"}
        with db.transaction(c):
            p = ap.Pending.create("reply.post", f"c-{rnd.randint(1, 99)}", payload,
                                  ledger=self.led, run_id="run-1")
            p.save(c)
        approval = ap.Approval(pending_id=p.id, **p.fields(approver),
                               signature=key.sign(p.message(approver)).hex())
        with db.transaction(c):
            ok = ap.verify_and_consume(c, approval, payload, ledger=self.led, session_id="s1")
        self.led.append("run-1", agent="community", kind="tool_call",
                        details={"tool": "reply.post", "side_effects": "external",
                                 "approval_id": ok.pending_id})
        self.write = self.led.append("run-1", agent="community", kind="external_write",
                                     output_sha256=p.content_sha256,
                                     details=ok.write_details())
        for _ in range(rnd.randint(1, 3)):
            self.led.append("run-2", agent="analyst", kind="model_call", model="muse-spark-1.3")
        self.approvers: dict[str, Ed25519PublicKey] = dict(ap.Keys.registry(c))
        self.tmp = tmp
        self.rnd = rnd

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(x) for x in self.led.export_jsonl().splitlines()]

    def check(self, lines: list[dict[str, Any]], *, min_seq: int = 0,
              approvers: dict[str, Ed25519PublicKey] | None = None) -> bool:
        path = self.tmp / "export.jsonl"
        path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in lines))
        report = verify(path, self.led.public_key, min_seq=min_seq,
                        approver_keys=self.approvers if approvers is None else approvers)
        return report.ok


def _receipts(lines: list[dict[str, Any]]) -> list[int]:
    return [i for i, x in enumerate(lines) if "manifest" not in x]


def _edit(c: _Case) -> bool:
    lines = c.lines()
    i = c.rnd.choice(_receipts(lines))
    field = c.rnd.choice(["model", "agent", "tokens_in", "cost_usd", "output_sha256", "ts"])
    old = lines[i][field]
    changes: dict[str, Callable[[Any], Any]] = {
        "model": lambda v: "forged", "agent": lambda v: "forged",
        "tokens_in": lambda v: v + 1, "cost_usd": lambda v: v + 0.5,
        "output_sha256": lambda v: "0" * 64, "ts": lambda v: v + 1}
    lines[i][field] = changes[field](old)  # always a real change
    return c.check(lines)


def _rehash(c: _Case) -> bool:
    lines = c.lines()
    i = c.rnd.choice(_receipts(lines))
    body = Receipt(**lines[i]).body()
    body["cost_usd"] += 0.5  # a real change, hash recomputed, signature kept
    lines[i] = {**body, "hash": Receipt(**body).compute_hash(), "sig": lines[i]["sig"]}
    return c.check(lines)


def _delete_middle(c: _Case) -> bool:
    lines = c.lines()
    idx = _receipts(lines)
    run1 = [i for i in idx if lines[i]["run_id"] == "run-1"][:-1]
    del lines[c.rnd.choice(run1)]
    return c.check(lines)


def _reorder(c: _Case) -> bool:
    lines = c.lines()
    run1 = [i for i in _receipts(lines) if lines[i]["run_id"] == "run-1"]
    i = c.rnd.choice(run1[:-1])
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return c.check(lines)


def _duplicate(c: _Case) -> bool:
    lines = c.lines()
    i = c.rnd.choice(_receipts(lines))
    lines.insert(i + 1, dict(lines[i]))
    return c.check(lines)


def _truncate(c: _Case) -> bool:
    lines = c.lines()
    run = c.rnd.choice(["run-1", "run-2"])
    last = [i for i in _receipts(lines) if lines[i]["run_id"] == run][-1]
    del lines[last]
    return c.check(lines)


def _drop_run(c: _Case) -> bool:
    lines = [x for x in c.lines() if x.get("run_id") != "run-2"]
    return c.check(lines)


def _rollback(c: _Case) -> bool:
    lines = c.lines()
    return c.check(lines, min_seq=c.led.manifest()["seq"] + c.rnd.randint(1, 5))


def _forge_manifest(c: _Case) -> bool:
    lines = c.lines()
    lines[-1]["manifest"]["seq"] += c.rnd.randint(1, 50)
    return c.check(lines)


def _drop_manifest(c: _Case) -> bool:
    return c.check(c.lines()[:-1])


def _wrong_approver_key(c: _Case) -> bool:
    other = Ed25519PrivateKey.generate().public_key()
    return c.check(c.lines(), approvers={a: other for a in c.approvers})


def _unknown_approver(c: _Case) -> bool:
    return c.check(c.lines(), approvers={})


def _write_without_approval(c: _Case) -> bool:  # signed by the real ledger key
    c.led.append("run-2", agent="media_buyer", kind="external_write",
                 output_sha256=sha256_hex(str(c.rnd.random())),
                 details={"action": "reply.post", "item_id": "c-1"})
    return c.check(c.lines())


def _second_write(c: _Case) -> bool:
    c.led.append("run-1", agent="community", kind="external_write",
                 output_sha256=c.write.output_sha256, details=c.write.details)
    return c.check(c.lines())


def _unauthorised_step(c: _Case) -> bool:
    c.led.append("run-2", agent="media_buyer", kind="tool_call",
                 details={"tool": c.rnd.choice(["campaign", "adset", "ad", "activate"]),
                          "side_effects": "external"})
    return c.check(c.lines())


def _future_dated(c: _Case) -> bool:
    import studio.receipts as rec

    real = rec.time.time
    rec.time.time = lambda: real() + 3600  # type: ignore[assignment]
    try:
        c.led.append("run-2", agent="analyst", kind="model_call", model="muse-spark-1.3")
    finally:
        rec.time.time = real  # type: ignore[assignment]
    return c.check(c.lines())


TAMPER: dict[str, tuple[str, Callable[[_Case], bool]]] = {
    "edited field": ("someone without the ledger key", _edit),
    "re-hashed without the ledger key": ("someone without the ledger key", _rehash),
    "receipt deleted mid-run": ("someone without the ledger key", _delete_middle),
    "receipts reordered": ("someone without the ledger key", _reorder),
    "receipt duplicated": ("someone without the ledger key", _duplicate),
    "tail truncated": ("someone without the ledger key", _truncate),
    "whole run deleted": ("someone without the ledger key", _drop_run),
    "older export (rollback)": ("someone without the ledger key", _rollback),
    "manifest forged": ("someone without the ledger key", _forge_manifest),
    "manifest removed": ("someone without the ledger key", _drop_manifest),
    "approval checked against another key": ("forged approver", _wrong_approver_key),
    "approval by an unregistered approver": ("forged approver", _unknown_approver),
    "external write without approval": ("the server itself", _write_without_approval),
    "second write on one approval": ("the server itself", _second_write),
    "external step without approval": ("the server itself", _unauthorised_step),
    "receipt dated after the manifest": ("the server itself", _future_dated),
}


def tamper_suite(n: int, seed: int = 4) -> dict[str, Any]:
    rnd = random.Random(seed)  # noqa: S311 - reproducible test data
    rows = []
    clean_ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        k = 0
        for _ in range(n):
            k += 1
            d = Path(tmp) / f"c{k}"
            d.mkdir()
            c = _Case(d, rnd)
            clean_ok += c.check(c.lines())
        for name, (who, fn) in TAMPER.items():
            rejected = 0
            for _ in range(n):
                k += 1
                d = Path(tmp) / f"c{k}"
                d.mkdir()
                rejected += not fn(_Case(d, rnd))
            rows.append({"class": name, "attacker": who, "n": n, "rejected": rejected})
    return {"rows": rows, "clean": {"n": n, "accepted": clean_ok}}


# -- 2. attack suites ----------------------------------------------------------------

ATTACK_FILES = ["test_dispatcher.py", "test_paused_only.py", "test_injection.py",
                "test_untrusted_paths.py", "test_external_steps.py", "test_review_binding.py",
                "test_activation_chain.py", "test_unfilled_fact.py", "test_guest_views.py",
                "test_comment_pii.py", "test_parallel_cost.py", "test_placements_cost.py",
                "test_review_every_format.py", "test_voice_transcript.py"]


def attack_suites() -> list[dict[str, Any]]:
    import pytest

    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "junit.xml"
        pytest.main(["-q", "-p", "no:cacheprovider", f"--junitxml={xml}",
                     *[str(ROOT / "tests" / "attack" / f) for f in ATTACK_FILES]])
        tree = ET.parse(xml)  # noqa: S314 - our own file
    per: dict[str, dict[str, int]] = {}
    for case in tree.iter("testcase"):
        name = case.get("classname", "").split(".")[-1] + ".py"
        row = per.setdefault(name, {"tests": 0, "passed": 0})
        row["tests"] += 1
        row["passed"] += not any(ch.tag in ("failure", "error") for ch in case)
    return [{"file": f, **per.get(f, {"tests": 0, "passed": 0})} for f in ATTACK_FILES]


# -- 3. reviewer catch rate ----------------------------------------------------------


def _ads() -> list[dict[str, Any]]:
    return json.loads((EVAL / "ads.SYNTHETIC.json").read_text())["ads"]  # type: ignore[no-any-return]


def record_reviews(env_file: Path, max_cost: float = 0.25) -> None:
    """Real Muse Spark 1.3 vision reviews of every seeded ad (operator key, once)."""
    import httpx

    from studio.agents import reviewer
    from studio.agents.schemas import CopyVariant
    from studio.muse.client import MuseClient
    from studio.receipts import Ledger
    from studio.settings import Budget, Settings

    settings = Settings.load(env_file=env_file)
    captured: list[dict[str, Any]] = []

    class Capture(httpx.BaseTransport):
        inner = httpx.HTTPTransport()

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            resp = self.inner.handle_request(request)
            body = resp.read()
            captured.append({"request": json.loads(request.content), "status": resp.status_code,
                             "response": json.loads(body)})
            return httpx.Response(resp.status_code, content=body,
                                  headers={"content-type": "application/json"})

    led = Ledger()
    client = MuseClient(settings.model_api_key, settings.model_api_base_url, ledger=led,
                        budget=Budget(max_cost), transport=Capture())
    out = []
    for ad in _ads():
        image = (EVAL / f"{ad['image']}.SYNTHETIC.png").read_bytes()
        copy = CopyVariant(**ad["copy"])
        review = reviewer.review(client, image, copy, run_id="eval")
        r = led.receipts("eval")[-1]
        out.append({"id": ad["id"], "expect": ad["expect"], "verdict": review.verdict,
                    "findings": [f.model_dump() for f in review.findings],
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                    "cost_usd": r.cost_usd, "model": r.model})
        if ad["id"] == "p9-zaloz-spolke":
            _save_p9_cassette(captured[-1])
        print(f"{ad['id']:<18} {review.verdict:<5} {[f.rule for f in review.findings]}")
    REVIEWS.write_text(json.dumps({
        "_provenance": "recorded", "recorded": time.strftime("%Y-%m-%d", time.gmtime()),
        "model": "muse-spark-1.3", "reviews": out,
        "cost_usd": round(sum(x["cost_usd"] for x in out), 6)}, indent=1,
        ensure_ascii=False) + "\n")


def record_open_reviews(env_file: Path) -> None:
    """Real Muse Spark Open (Baseten) reviews of every seeded ad: the same prompt, image and
    schema as the Spark vision reviews, for the agreement table."""
    from studio.agents import reviewer
    from studio.agents.schemas import CopyVariant
    from studio.muse.client import MuseError
    from studio.muse.openweights import MODEL, OpenWeightsClient
    from studio.receipts import Ledger
    from studio.settings import Settings

    settings = Settings.load(env_file=env_file)
    if not settings.baseten_api_key:
        raise SystemExit("BASETEN_API_KEY is not set")
    led = Ledger()
    client = OpenWeightsClient(settings.baseten_api_key, ledger=led)
    out = []
    for ad in _ads():
        image = (EVAL / f"{ad['image']}.SYNTHETIC.png").read_bytes()
        copy = CopyVariant(**ad["copy"])
        try:
            review = reviewer.second_opinion(client, image, copy, run_id="eval")
            verdict, findings = review.verdict, [f.model_dump() for f in review.findings]
        except MuseError as exc:
            verdict, findings = f"ERROR:{exc.code}", []
        r = led.receipts("eval")[-1]
        out.append({"id": ad["id"], "expect": ad["expect"], "verdict": verdict,
                    "findings": findings, "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "reasoning_tokens": r.details.get("reasoning_tokens", 0),
                    "cost_usd": r.cost_usd, "model": r.model})
        print(f"{ad['id']:<18} {verdict:<5} {[f['rule'] for f in findings]}")
    OPEN_REVIEWS.write_text(json.dumps({
        "_provenance": "recorded", "recorded": time.strftime("%Y-%m-%d", time.gmtime()),
        "model": MODEL, "cost_basis": "Baseten Model APIs price $1.25 in / $4.25 out per 1M",
        "reviews": out, "cost_usd": round(sum(x["cost_usd"] for x in out), 6)}, indent=1,
        ensure_ascii=False) + "\n")


def agreement() -> dict[str, Any] | None:
    """Spark vision vs Spark Open on the same seeded ads (model verdicts, before the code
    check)."""
    if not (REVIEWS.exists() and OPEN_REVIEWS.exists()):
        return None
    spark = {r["id"]: r for r in json.loads(REVIEWS.read_text())["reviews"]}
    data = json.loads(OPEN_REVIEWS.read_text())
    rows = []
    for o in data["reviews"]:
        s = spark[o["id"]]
        s_rules = sorted({f["rule"] for f in s["findings"] if f["severity"] != "info"})
        o_rules = sorted({f["rule"] for f in o["findings"] if f["severity"] != "info"})
        rows.append({"id": o["id"], "expect": o["expect"], "spark": s["verdict"],
                     "open": o["verdict"], "spark_rules": s_rules, "open_rules": o_rules,
                     "same_verdict": s["verdict"] == o["verdict"],
                     "same_decision": (s["verdict"] == "OK") == (o["verdict"] == "OK")})
    bad = [r for r in rows if r["expect"] != "OK"]
    return {"rows": rows, "n": len(rows), "cost_usd": data["cost_usd"],
            "recorded": data["recorded"],
            "tokens_in": sum(r["tokens_in"] for r in data["reviews"]),
            "tokens_out": sum(r["tokens_out"] for r in data["reviews"]),
            "same_verdict": sum(r["same_verdict"] for r in rows),
            "same_decision": sum(r["same_decision"] for r in rows),
            "open_flagged": sum(r["open"] not in ("OK",) and not r["open"].startswith("ERROR")
                                for r in bad),
            "either_flagged": sum(r["open"] not in ("OK",) or r["spark"] != "OK" for r in bad),
            "n_bad": len(bad),
            "open_p9_blocked": sum(r["open"] == "BLOCK" for r in bad if r["expect"] == "P9"),
            "open_clean_blocked": sum(r["open"] == "BLOCK" for r in rows
                                      if r["expect"] == "OK"),
            "errors": sum(r["open"].startswith("ERROR") for r in rows)}


def _save_p9_cassette(item: dict[str, Any]) -> None:
    save_cassette(item, P9_CASSETTE, "Real Muse Spark 1.3 vision review of the synthetic P9 ad "
                  "(fixtures/eval/ads.SYNTHETIC.json p9-zaloz-spolke), without the code check.")


def save_cassette(item: dict[str, Any], path: Path, note: str,
                  request_path: str = "/chat/completions") -> None:
    from studio.muse.client import input_hashes

    req = item["request"]
    hashes = iter(input_hashes(req))
    for msg in req["messages"]:
        if isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if part.get("type") == "image_url":
                    part["image_url"]["url"] = "data:image/png;sha256," + next(hashes).split(
                        ":")[-1]
    path.write_text(json.dumps({
        "_provenance": "recorded", "note": note,
        "interactions": [{"request": {"method": "POST", "path": request_path,
                                      "json": req},
                          "response": {"status": item["status"], "json": item["response"]}}],
    }, indent=1, ensure_ascii=False) + "\n")


def reviewer_metrics() -> dict[str, Any] | None:
    if not REVIEWS.exists():
        return None
    from studio.agents import reviewer
    from studio.agents.schemas import CopyVariant, Review

    data = json.loads(REVIEWS.read_text())
    ads = {a["id"]: a for a in _ads()}
    rows = []
    for r in data["reviews"]:
        model = Review.model_validate({"verdict": r["verdict"], "findings": r["findings"],
                                       "summary": ""})
        merged = reviewer.with_policy_net(model, CopyVariant(**ads[r["id"]]["copy"]))
        rules = sorted({f.rule for f in model.findings})
        rows.append({"id": r["id"], "expect": r["expect"], "model_verdict": r["verdict"],
                     "model_rules": rules, "final_verdict": merged.verdict,
                     "rule_cited": r["expect"] in rules})
    bad = [x for x in rows if x["expect"] != "OK"]
    clean = [x for x in rows if x["expect"] == "OK"]
    p9 = [x for x in bad if x["expect"] == "P9"]
    return {
        "rows": rows, "n_bad": len(bad), "n_clean": len(clean), "cost_usd": data["cost_usd"],
        "recorded": data["recorded"],
        "model_flagged": sum(x["model_verdict"] != "OK" for x in bad),
        "model_blocked": sum(x["model_verdict"] == "BLOCK" for x in bad),
        "model_rule": sum(x["rule_cited"] for x in bad),
        "final_flagged": sum(x["final_verdict"] != "OK" for x in bad),
        "final_blocked": sum(x["final_verdict"] == "BLOCK" for x in bad),
        "p9_n": len(p9), "p9_model_blocked": sum(x["model_verdict"] == "BLOCK" for x in p9),
        "p9_final_blocked": sum(x["final_verdict"] == "BLOCK" for x in p9),
        "clean_ok": sum(x["final_verdict"] == "OK" for x in clean),
        "clean_blocked": sum(x["final_verdict"] == "BLOCK" for x in clean),
    }


# -- 6. format rule F1 ------------------------------------------------------------


def format_cases() -> dict[str, Any]:
    from studio.agents import formats

    cases = json.loads((EVAL / "formats.SYNTHETIC.json").read_text())["cases"]
    rows = []
    for c in cases:
        size = tuple(c["image"]) if c.get("image") else None
        got = sorted({f.severity for f in formats.check(c["placement"], c["ad"], size)})
        rows.append({"id": c["id"], "placement": c["placement"], "expect": sorted(c["expect"]),
                     "got": got, "ok": got == sorted(c["expect"])})
    return {"rows": rows, "n": len(rows), "passed": sum(r["ok"] for r in rows)}


# -- 5. cost per run -----------------------------------------------------------------


def run_costs() -> list[dict[str, Any]]:
    from studio import demo, replay

    out = []
    for path in sorted(replay.REPLAY_DIR.glob("*/replay.json")):
        name = path.parent.name
        with tempfile.TemporaryDirectory() as tmp:
            s = demo.run(name, data_dir=Path(tmp), log=lambda _: None)
            receipts = [json.loads(x) for x in (Path(tmp) / "receipts.jsonl").read_text()
                        .splitlines() if '"manifest"' not in x]
        calls = [r for r in receipts if r["kind"] == "model_call"]
        by_agent: dict[str, float] = {}
        for r in calls:
            if r["run_id"] == s["run_id"]:
                by_agent[r["agent"]] = by_agent.get(r["agent"], 0.0) + r["cost_usd"]
        out.append({"set": name, "status": s["status"], "campaign": s["campaign_status"],
                    "run_cost": s["run_cost_usd"], "loop_cost": s["cost_usd"],
                    "model_calls": len(calls), "by_agent": by_agent,
                    "replay_seconds": s["seconds"], "verify_exit": s["verify_exit"]})
    return out


# -- report --------------------------------------------------------------------------


def render(t: dict[str, Any], attacks: list[dict[str, Any]], rv: dict[str, Any] | None,
           costs: list[dict[str, Any]], ag: dict[str, Any] | None = None,
           fm: dict[str, Any] | None = None) -> str:
    total = sum(r["n"] for r in t["rows"])
    rej = sum(r["rejected"] for r in t["rows"])
    a_tests = sum(a["tests"] for a in attacks)
    a_pass = sum(a["passed"] for a in attacks)
    out = ["# Evaluation", "",
           "Generated by `scripts/eval.py` (`make eval`) on "
           f"{time.strftime('%Y-%m-%d', time.gmtime())}. Offline: sections 1, 2 and 5 are "
           "computed on every run; section 3 reads recorded real Muse Spark 1.3 reviews.", "",
           "| Measure | Result |", "|---|---|",
           f"| `studio verify` on tampered exports | **{rej}/{total} rejected** "
           f"({len(t['rows'])} classes × {t['rows'][0]['n']}) |",
           f"| `studio verify` on clean exports | {t['clean']['accepted']}/{t['clean']['n']} "
           "accepted |",
           f"| Attack suites | **{a_pass}/{a_tests} pass** ({len(attacks)} files) |"]
    if rv:
        out += [f"| Reviewer: bad ads stopped (WARN or BLOCK) | **{rv['final_flagged']}/"
                f"{rv['n_bad']}** (model alone {rv['model_flagged']}/{rv['n_bad']}) |",
                f"| Reviewer: P9 ads blocked | **{rv['p9_final_blocked']}/{rv['p9_n']}** "
                f"(model alone {rv['p9_model_blocked']}/{rv['p9_n']}) |",
                f"| Reviewer: clean ads blocked (false positives) | {rv['clean_blocked']}/"
                f"{rv['n_clean']} |"]
    if costs:
        demo_row = next((c for c in costs if c["set"] == "demo"), costs[0])
        out.append(f"| Cost of one campaign run (brief → approval) | **${demo_row['run_cost']:.4f}"
                   f"** (`{demo_row['set']}` brief; all briefs below) |")
    if fm:
        out.append(f"| Format rule F1 (platform limits, code) on seeded cases | **{fm['passed']}/"
                   f"{fm['n']}** as expected |")
    if ag:
        out.append(f"| Spark vision vs Spark Open (Baseten) agreement | **{ag['same_decision']}/"
                   f"{ag['n']} same OK-vs-flag decision**, {ag['same_verdict']}/{ag['n']} same "
                   "verdict |")
    else:
        out.append("| Spark vision vs Spark Open agreement | pending — needs `BASETEN_API_KEY` |")
    out += ["", "## 1. Tamper suite (`studio verify`)", "",
            "Each case is a fresh ledger (random number of receipts, a signed reply approval, "
            "its adapter step and its write, a second run), exported and then tampered with. "
            "Verified with the ledger key, the approver registry and, for rollback, the "
            "latest known manifest seq. \"The server itself\" rows are logs signed with the "
            "real ledger key: what verify still catches if the server lies.", "",
            "| Tamper class | Who could do it | N | Rejected |", "|---|---|---|---|"]
    out += [f"| {r['class']} | {r['attacker']} | {r['n']} | {r['rejected']} |"
            for r in t["rows"]]
    out += [f"| *(control)* clean export | — | {t['clean']['n']} | "
            f"{t['clean']['n'] - t['clean']['accepted']} |", "",
            "Not covered (by design, see `docs/SECURITY.md`): a server that never writes a "
            "receipt for a write it made, and a verifier that trusts the server's key on first "
            "use (`--server` without a pinned `ledger.pub`).", "",
            "## 2. Attack suites (`tests/attack/`)", "", "| File | Tests | Pass |",
            "|---|---|---|"]
    out += [f"| `{a['file']}` | {a['tests']} | {a['passed']} |" for a in attacks]
    out += ["", "Highlights: the dispatcher refuses every external skill a scripted model "
            "asks for (no approval, no write); every Meta object is created PAUSED and an "
            "ACTIVE status anywhere in a creation payload is refused before any adapter call; "
            "a 10-comment prompt-injection corpus with a fully obedient model yields drafts "
            "only — no tools are sent, nothing is approved or written.", ""]
    out += ["## 3. Reviewer catch rate (seeded bad ads)", "",
            "Why P9 exists: a model-invented legal/tax claim once ran in a paid promotion of a "
            "small firm's ad, and two human-written ads promising company formation and "
            "\"no ZUS obligation\" were later disapproved by Google Ads. P9 (no official-"
            "service or company-registration claims, no absolute tax/legal promises) and the "
            "unfilled-`[FACT]` rule (the copywriter may not invent a fact; an unfilled "
            "placeholder cannot be signed) come from those cases.", ""]
    if rv:
        out += [f"25 hand-written ads (`fixtures/eval/ads.SYNTHETIC.json`): {rv['n_bad']} break "
                f"one checklist rule each (P1–P9, five of them P9), {rv['n_clean']} follow the "
                "brand sheet. Each was reviewed once by Muse Spark 1.3 vision (image + copy, "
                f"strict `Review` schema) on {rv['recorded']}; {len(rv['rows'])} real calls, "
                f"${rv['cost_usd']:.4f}. \"Final\" adds the code check for P9 "
                "(`reviewer.policy_net`) the product runs after the model. The set was written "
                "before these runs and was not used to tune the prompts.", "",
                "| Ad | Expected rule | Model verdict | Rules cited | Final verdict |",
                "|---|---|---|---|---|"]
        out += [f"| `{r['id']}` | {r['expect']} | {r['model_verdict']} | "
                f"{', '.join(r['model_rules']) or '—'} | {r['final_verdict']} |"
                for r in rv["rows"]]
        out += ["", f"- Bad ads stopped: model {rv['model_flagged']}/{rv['n_bad']}, final "
                f"{rv['final_flagged']}/{rv['n_bad']}; blocked outright: model "
                f"{rv['model_blocked']}, final {rv['final_blocked']}.",
                f"- Expected rule cited by the model: {rv['model_rule']}/{rv['n_bad']}.",
                f"- Clean ads passed OK: {rv['clean_ok']}/{rv['n_clean']}; blocked: "
                f"{rv['clean_blocked']}.", ""]
    else:
        out += ["Not recorded yet (`python scripts/eval.py --record-reviews`).", ""]
    out += ["## 4. Spark vision vs Spark Open (Baseten) agreement", ""]
    if ag:
        pct = 100 * ag["same_decision"] / ag["n"]
        out += [f"The same {ag['n']} ads, prompt, image and strict `Review` schema, reviewed by "
                "Muse Spark Open (`meta-models/muse-spark-open`, open weights on Baseten, a "
                f"reasoning model) on {ag['recorded']}: {ag['n']} real calls, "
                f"{ag['tokens_in']:,} input / {ag['tokens_out']:,} output tokens (reasoning "
                f"included), ${ag['cost_usd']:.4f} at Baseten's published Model APIs price "
                "($1.25 / $4.25 per 1M input / output tokens). Model verdicts only, before the "
                "P9 code check.", "",
                f"- Same decision (OK vs WARN/BLOCK): **{ag['same_decision']}/{ag['n']} "
                f"({pct:.0f} %)**; same verdict: {ag['same_verdict']}/{ag['n']}.",
                f"- Bad ads flagged by Spark Open: {ag['open_flagged']}/{ag['n_bad']}; by either "
                f"reviewer (what the product does: BLOCK from either side wins): "
                f"{ag['either_flagged']}/{ag['n_bad']}.",
                f"- P9 ads blocked by Spark Open: {ag['open_p9_blocked']}/5; clean ads blocked: "
                f"{ag['open_clean_blocked']}/5; failed calls: {ag['errors']}.", "",
                "Disagreements (shown to the human on the approval screen):", "",
                "| Ad | Expected | Spark vision | Spark Open | Rules only one cites |",
                "|---|---|---|---|---|"]
        diff = [r for r in ag["rows"] if not r["same_verdict"]
                or r["spark_rules"] != r["open_rules"]]
        for r in diff:
            only = sorted(set(r["spark_rules"]) ^ set(r["open_rules"]))
            out.append(f"| `{r['id']}` | {r['expect']} | {r['spark']} | {r['open']} | "
                       f"{', '.join(only) or '—'} |")
        if not diff:
            out.append("| — | | | | |")
    else:
        out += ["Pending — needs `BASETEN_API_KEY`. The code path exists (`reviewer.run`, "
                "`reconcile`: BLOCK from either side wins, every disagreement is shown on the "
                "approval screen); without a key the second opinion is labelled "
                "\"unavailable\"."]
    out += [
            "", "## 5. Cost per campaign run (from receipts)", "",
            "Each recorded brief replayed offline through the whole loop; costs are the "
            "receipts' `cost_usd` (the price of the recorded real call, from the published "
            "price table). \"Run\" is brief → plan, copy, cut-out, images, reviews → pending "
            "approval; \"loop\" adds comment triage, lead rationale and the analyst report.", "",
            "| Brief | Run status | Campaign | Run cost | Loop cost | Model calls | "
            "Replay time |", "|---|---|---|---|---|---|---|"]
    out += [f"| `{c['set']}` | {c['status']} | {c['campaign'] or '—'} | ${c['run_cost']:.4f} | "
            f"${c['loop_cost']:.4f} | {c['model_calls']} | {c['replay_seconds']:.1f} s |"
            for c in costs]
    if costs:
        agents = sorted({a for c in costs for a in c["by_agent"]})
        out += ["", "Run cost by agent:", "", "| Brief | " + " | ".join(agents) + " |",
                "|---|" + "---|" * len(agents)]
        out += [f"| `{c['set']}` | " + " | ".join(f"${c['by_agent'].get(a, 0):.4f}"
                                                  for a in agents) + " |" for c in costs]
    if fm:
        out += ["", "## 6. Format rule F1 (platform limits, deterministic)", "",
                "Limits from each platform's own pages (retrieved 2026-10-28, see "
                "[policy.md](policy.md)); hard limits block, recommended lengths warn with "
                "the truncation point. Only Meta is wired to publishing; Google Ads and TikTok "
                "rules check exported copy variants.", "",
                "| Case | Placement | Expected | Got |", "|---|---|---|---|"]
        out += [f"| `{r['id']}` | `{r['placement']}` | {', '.join(r['expect']) or 'none'} | "
                f"{', '.join(r['got']) or 'none'}{'' if r['ok'] else ' ✗'} |"
                for r in fm["rows"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=25, help="cases per tamper class")
    p.add_argument("--write", type=Path, help="write the markdown report here")
    p.add_argument("--record-reviews", action="store_true",
                   help="real Model API calls: review the seeded ads once")
    p.add_argument("--record-open-reviews", action="store_true",
                   help="real Baseten calls: Spark Open reviews of the seeded ads once")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = p.parse_args(argv)
    if args.record_reviews:
        record_reviews(args.env_file)
    if args.record_open_reviews:
        record_open_reviews(args.env_file)
    report = render(tamper_suite(args.n), attack_suites(), reviewer_metrics(), run_costs(),
                    agreement(), format_cases())
    if args.write:
        args.write.write_text(report)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
