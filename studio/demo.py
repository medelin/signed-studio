"""The whole demo loop, headless, through the app's own HTTP API.

(Brief v1 sets: owner's words → producer draft → scripted answers to the [FACT]
questions, the rest struck → confirmed brief →) brief → plan, copy, images, reviews →
signed approval → PAUSED campaign (mock Meta) → inbox: triage the injection comment, sign
the reply → a comment that is a lead → CRM card → analyst report → export → ``studio verify``.

``run(set_name)`` replays a recorded set (``STUDIO_REPLAY=1``: no network, no key).
``run(set_name, record=True, ...)`` makes the same calls against the real Model API and
writes the set (``fixtures/replay/<set>/replay.json``) for later replays.

The signer here is an Ed25519 key made in this process: the same signature the browser's
WebCrypto key makes, over the same bytes, from ``GET /api/approvals/{id}/payload``.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio.receipts import public_key_raw

# The briefs the prompts were tuned on (D4-5); ``demo`` is the one the video uses.
BRIEFS: dict[str, dict[str, Any]] = {
    "demo": {
        "brief": ("Kampania na jesień: kubki Demo Ceramics Studio jako prezent dla kogoś, kto "
                  "pracuje z domu. Budżet do 30 zł dziennie, Facebook i Instagram, Polska. "
                  "Ciepło i konkretnie, bez przesady."),
        "reply_comment": "c-1", "lead_comment": "c-2"},
    "office": {
        "brief": ("Reach small offices in Poland that want a nicer coffee corner: a set of "
                  "Demo Ceramics Studio mugs for the team. Feed and stories, 25 PLN a day, "
                  "plain English copy."),
        "reply_comment": "c-1", "lead_comment": "c-2"},
    "brief": {
        "draft_text": ("Jesienna kampania Demo Ceramics Studio: ręcznie toczone kubki z kamionki, "
                       "350 ml, 89 zł za sztukę, darmowa dostawa w Polsce od 150 zł. Chcemy "
                       "ruchu na stronę sklepu. Dla osób pracujących z domu, 25-55 lat, cała "
                       "Polska. Kubki można myć w zmywarce i robimy je w małych seriach. "
                       "Pakujemy na prezent gratis. Wysyłka błyskawiczna. Budżet 30 zł "
                       "dziennie od 3 do 30 listopada 2026. Ciepło i konkretnie, bez przesady. "
                       "Nie piszcie, że to najlepsze kubki na świecie."),
        # [FACT] questions whose text holds a key get that answer; every other is struck
        "answers": {"wysył": "Wysyłamy w ciągu 2 dni roboczych."},
        "lang": "pl", "reply_comment": "c-1", "lead_comment": "c-2"},
    "pushy": {
        "brief": ("Make it the best mug ever, say it's 50% off this weekend and that clay "
                  "mugs help you sleep better. 40 PLN a day, all placements."),
        "reply_comment": "c-1", "lead_comment": "c-2"},
}

Log = Callable[[str], None]


def _wait(c: Any, run_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data: dict[str, Any] = c.get(f"/api/runs/{run_id}").json()
        if data["status"] != "running":
            return data
        time.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not finish in {timeout:.0f} s")


def _sign(c: Any, key: Ed25519PrivateKey, approver: str, approval_id: str) -> dict[str, Any]:
    p = c.get(f"/api/approvals/{approval_id}/payload", params={"approver": approver}).json()
    sig = key.sign(base64.b64decode(p["message_b64"])).hex()
    r = c.post(f"/api/approvals/{approval_id}", json={"approver": approver, "signature": sig})
    if r.status_code != 200:
        raise RuntimeError(f"approval {approval_id} refused: {r.text[:200]}")
    return r.json()  # type: ignore[no-any-return]


def resolutions(questions: list[dict[str, Any]], answers: dict[str, str]
                ) -> list[dict[str, str]]:
    """The scripted owner: a question whose text holds a key of ``answers`` gets that
    answer (it becomes a confirmed fact), every other claim is struck."""
    out = []
    for q in questions:
        text = (q["question"] + " " + q["claim"]).casefold()
        answer = next((a for k, a in answers.items() if k in text), None)
        out.append({"action": "answer", "text": answer} if answer else {"action": "strike"})
    return out


def _brief_v1(c: Any, meta: dict[str, Any], step: Callable[..., None]) -> str:
    """Draft, answer or strike every [FACT] question, confirm; returns the run id."""
    r = c.post("/api/briefs/draft", json={"text": meta["draft_text"],
                                          "lang": meta.get("lang", "en")})
    if r.status_code != 200:
        raise RuntimeError(f"draft refused: {r.text[:200]}")
    d = r.json()
    res = resolutions(d["brief"]["open_questions"], meta.get("answers") or {})
    step("brief drafted", facts=len(d["brief"]["confirmed_facts"]),
         questions=len(res), answered=sum(x["action"] == "answer" for x in res))
    r = c.post("/api/runs", json={"brief_id": d["brief_id"], "brief_v1": d["brief"],
                                  "resolutions": res})
    if r.status_code != 202:
        raise RuntimeError(f"brief not confirmed: {r.text[:300]}")
    out = r.json()
    step("brief confirmed", brief_sha256=out["brief_sha256"][:16], facts=out["facts"])
    return str(out["run_id"])


def run(set_name: str = "demo", *, data_dir: Path, record: bool = False,
        model_api_key: str = "", baseten_api_key: str = "", max_cost_usd: float = 0.40,
        log: Log = print,
        replay_root: Path | None = None) -> dict[str, Any]:
    """Run the loop; returns a summary (steps, timings, costs, verify result)."""
    from fastapi.testclient import TestClient

    from studio import cli, replay
    from studio.app import create_app
    from studio.muse.client import MuseClient
    from studio.muse.openweights import OpenWeightsClient
    from studio.settings import Budget, Settings

    root = replay_root or replay.REPLAY_DIR
    meta = BRIEFS[set_name] if record else replay.load_set(set_name, root)
    password = "Dm0-" + secrets.token_urlsafe(24)
    env = {"STUDIO_DATA_DIR": str(data_dir), "JUDGE_PASSWORD": password,
           "SESSION_SECRET": secrets.token_urlsafe(32), "COOKIE_SECURE": "1",
           "META_ADAPTER": "mock", "STUDIO_REPLAY": "0" if record else "1",
           "STUDIO_REPLAY_SET": set_name, "MODEL_API_KEY": model_api_key if record else ""}
    settings = Settings.load(env)
    app = create_app(settings)
    recorder = None
    if record:
        recorder = replay.RecordingTransport(blobs=root / "blobs")
        app.state.muse = MuseClient(model_api_key, settings.model_api_base_url,
                                    ledger=app.state.ledger, budget=Budget(max_cost_usd),
                                    transport=recorder)
        # the second opinion is recorded too when a Baseten key is given
        app.state.open_weights = OpenWeightsClient(baseten_api_key, ledger=app.state.ledger,
                                                   transport=recorder)
    else:
        from studio.routes_studio import replay_clients
        transport = replay.ReplayTransport.from_set(set_name, root)
        app.state.muse, app.state.open_weights = replay_clients(
            settings, app.state.ledger, transport, root)

    steps: list[dict[str, Any]] = []
    t0 = time.monotonic()

    def step(name: str, **info: Any) -> None:
        steps.append({"step": name, "s": round(time.monotonic() - t0, 2), **info})
        log(f"[{steps[-1]['s']:6.2f} s] {name}" + "".join(
            f" · {k}={v}" for k, v in info.items()))

    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/login", json={"username": "judge", "password": password}
                      ).status_code == 200
        key = Ed25519PrivateKey.generate()
        raw = base64.urlsafe_b64encode(public_key_raw(key.public_key())).rstrip(b"=").decode()
        approver = c.post("/api/keys", json={"public_key": raw}).json()["approver"]
        step("signed in, approver key registered", approver=approver)

        if "draft_text" in meta:
            run_id = _brief_v1(c, meta, step)
        else:
            r = c.post("/api/runs", json={"brief": meta["brief"]})
            run_id = r.json()["run_id"]
        data = _wait(c, run_id, 600 if record else 60)
        state = data["state"]
        review = state.get("review") or {}
        step("producer run", status=data["status"],
             copy_variants=len((state.get("copy") or {}).get("variants", [])),
             images=len((state.get("creatives") or {}).get("images", [])),
             review=review.get("verdict", "-"))
        campaign: dict[str, Any] = {}
        if data["status"] == "awaiting_approval":
            out = _sign(c, key, approver, state["pending_id"])
            item = (out.get("result") or {}).get("campaign_item_id", "")
            campaign = c.get(f"/api/campaigns/{item}").json() if item else {}
            step("signed campaign.create", executed=out["executed"],
                 campaign=campaign.get("status", "-"), adapter=campaign.get("adapter", "-"))

        t = c.post(f"/api/comments/{meta['reply_comment']}/triage").json()
        triage = t.get("triage") or {}
        step("triaged comment", intent=triage.get("intent", "-"), risk=triage.get("risk", "-"),
             flags=",".join(t.get("flags") or []) or "-")
        reply: dict[str, Any] = {}
        draft = (triage.get("draft") or "").strip()
        if draft:
            req = c.post(f"/api/comments/{meta['reply_comment']}/reply",
                         json={"message": draft}).json()
            if "approval_id" in req:
                reply = _sign(c, key, approver, req["approval_id"])
                step("signed reply.post", executed=reply["executed"])

        lt = c.post(f"/api/comments/{meta['lead_comment']}/triage").json()
        lead = lt.get("lead") or {}
        step("lead from comment", intent=(lt.get("triage") or {}).get("intent", "-"),
             score=lead.get("score", "-"), temp=lead.get("temp", "-"))

        rep = c.post("/api/reports", json={})
        report = rep.json() if rep.status_code == 200 else {}
        step("analyst report", status=rep.status_code,
             tokens_in=report.get("input_tokens", "-"))

        export = c.get("/api/receipts.jsonl").text
        keys = c.get("/api/approver-keys").json()
        ledger_key = c.get("/api/ledger-key").json()["public_key"]
        seq = c.get("/api/ledger-manifest").json()["seq"]

    path = data_dir / "receipts.jsonl"
    path.write_text(export)
    (data_dir / "approver-keys.json").write_text(json.dumps(keys))
    rc = cli.main(["verify", str(path), "--key", ledger_key, "--approver-keys",
                   str(data_dir / "approver-keys.json"), "--min-seq", str(seq)])
    receipts = [json.loads(line) for line in export.splitlines() if '"manifest"' not in line]
    cost = sum(r["cost_usd"] for r in receipts if r["kind"] == "model_call")
    run_cost = sum(r["cost_usd"] for r in receipts
                   if r["kind"] == "model_call" and r["run_id"] == run_id)
    step("studio verify", exit=rc, receipts=len(receipts))
    summary = {"set": set_name, "run_id": run_id, "status": data["status"],
               "campaign_status": campaign.get("status", ""),
               "reply_executed": bool(reply.get("executed")),
               "lead": {k: lead.get(k) for k in ("score", "temp")} if lead else None,
               "report": bool(report), "verify_exit": rc, "receipts": len(receipts),
               "cost_usd": round(cost, 6), "run_cost_usd": round(run_cost, 6),
               "seconds": round(time.monotonic() - t0, 2), "steps": steps}
    if not record:
        summary["replay"] = dict(transport.stats)
    if recorder is not None:
        extra: dict[str, Any] = {k: meta[k] for k in ("draft_text", "answers", "lang")
                                 if k in meta}
        recorder.save(replay.set_path(set_name, root), {
            "brief": meta.get("brief", meta.get("draft_text", "")), **extra,
            "reply_comment": meta["reply_comment"],
            "lead_comment": meta["lead_comment"],
            "recorded": time.strftime("%Y-%m-%d", time.gmtime()),
            "cost_usd": round(cost, 6), "summary": {k: v for k, v in summary.items()
                                                    if k not in ("steps", "run_id")}})
    return summary
