"""Producer (coordinator): one brief → plan → copy → images → reviews → pending approval.

1. The plan is a strict ``Plan`` from Muse Spark 1.3 (Chat Completions, Standard tier).
2. A bounded Responses API loop (``max_steps``, ``max_cost_usd``, ``parallel_tool_calls``,
   ``reasoning.summary``) lets the model call the four model-callable skills through the
   ``Dispatcher``. ``request_approval`` only creates a ``Pending`` approval for a payload
   that code builds from the run's state (the model supplies a one-line summary, never the
   payload), stored next to it in the same transaction. Nothing external happens here.
   The cost cap is checked before every tool call (parallel calls included); a turn
   dispatches at most ``MAX_CALLS_PER_TURN`` calls and the expensive tools have per-run
   caps (``TOOL_LIMITS``).
3. Every step is a receipt; ``run_state`` holds what the UI shows.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.agents import art_director, copywriter, prompts, reviewer
from studio.agents.dispatcher import (
    ApprovalArgs,
    ArtDirectorArgs,
    CopywriterArgs,
    Dispatcher,
    Outcome,
    ReviewersArgs,
    ToolError,
)
from studio.agents.schemas import CopyPack, CreativeSet, Plan
from studio.approvals import Pending, transaction
from studio.muse.client import MuseClient, ToolCall
from studio.muse.openweights import OpenWeightsClient
from studio.muse.structured import structured
from studio.receipts import canonical, is_opaque, sha256_hex

AGENT = "producer"
MODEL = "muse-spark-1.3"
MAX_STEPS = 12
MAX_COST_USD = 1.0
MAX_BRIEF = 2000
MAX_CALLS_PER_TURN = 4
# Per-run caps on the expensive tools (each art director call is a SAM + 3 image series,
# each review is one or two vision calls per format).
TOOL_LIMITS = {"run_art_director": 2, "run_reviewers": 2}


@dataclass
class Context:
    client: MuseClient
    open_client: OpenWeightsClient | None
    conn: sqlite3.Connection
    photo_png: bytes
    concept: str
    data_dir: Path
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    landing_url: str = "https://example.com/"


@dataclass
class RunResult:
    run_id: str
    status: str  # awaiting_approval | done | max_steps | max_cost | failed
    plan: Plan | None = None
    copy: CopyPack | None = None
    creatives: CreativeSet | None = None
    review: dict[str, Any] | None = None
    pending_id: str = ""
    steps: int = 0
    cost_usd: float = 0.0
    summary: str = ""
    reasoning: list[str] = field(default_factory=list)

    def state(self) -> dict[str, Any]:
        return {
            "plan": self.plan.model_dump() if self.plan else None,
            "copy": self.copy.model_dump() if self.copy else None,
            "creatives": self.creatives.model_dump() if self.creatives else None,
            "review": self.review, "pending_id": self.pending_id, "steps": self.steps,
            "cost_usd": round(self.cost_usd, 6), "summary": self.summary,
            "reasoning": self.reasoning[-5:],
        }


def new_run_id() -> str:
    return "run_" + secrets.token_hex(8)


def opaque_id(prefix: str) -> str:
    """``prefix`` + random hex that redaction leaves alone (no phone-like digit runs)."""
    while True:
        candidate = prefix + secrets.token_hex(8)
        if is_opaque(candidate):
            return candidate


def campaign_payload(run: RunResult, variant_index: int,
                     link: str = "https://example.com/") -> dict[str, Any]:
    """The exact content a ``campaign.create`` approval authorises (built by code)."""
    assert run.plan and run.copy and run.creatives and run.review
    v = run.copy.variants[variant_index]
    return {
        "action": "campaign.create",
        "run_id": run.run_id,
        "campaign": {"objective": "OUTCOME_TRAFFIC", "status": "PAUSED",
                     "daily_budget_minor": run.plan.budget_pln_daily * 100,
                     "currency": "PLN", "angle": run.plan.angles[0].name},
        "audience": run.plan.audience,
        "targeting": {"countries": ["PL"], "age_min": 25, "age_max": 55},
        "link": link,
        "ad": {"primary_text": v.primary_text, "headline": v.headline,
               "description": v.description, "cta": v.cta},
        "risks": list(run.copy.risks),
        "images": [{"placement": i.placement, "sha256": i.sha256}
                   for i in run.creatives.images],
        "review": {"verdict": run.review["verdict"],
                   "second_opinion": run.review["second_opinion"],
                   "disagreements": run.review["disagreements"],
                   "formats": [{"placement": f["placement"], "sha256": f["sha256"],
                                "verdict": f["verdict"]} for f in run.review["formats"]]},
    }


def review_binding(run: RunResult, variant_index: int) -> str:
    """Hash of exactly what a review covers: the chosen variant and every image."""
    assert run.copy and run.creatives
    return sha256_hex(canonical({
        "variant_index": variant_index,
        "variant": run.copy.variants[variant_index].model_dump(),
        "images": [{"placement": i.placement, "sha256": i.sha256}
                   for i in run.creatives.images],
    }))


def _save_state(conn: sqlite3.Connection, run: RunResult, status: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO run_state (run_id, status, state, updated_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET status = excluded.status, "
            "state = excluded.state, updated_ts = excluded.updated_ts",
            (run.run_id, status, json.dumps(run.state(), ensure_ascii=False), time.time()),
        )


def _cost(ctx: Context, run_id: str) -> float:
    return sum(r.cost_usd for r in ctx.client.ledger.receipts(run_id))


def make_plan(ctx: Context, brief: str, run_id: str) -> Plan:
    plan, _ = structured(
        ctx.client, MODEL,
        [{"role": "system", "content": prompts.system(AGENT)},
         {"role": "user", "content": f"Brief:\n{brief}\n\nWrite the campaign plan only."}],
        Plan, tier="standard", tier_reason="producer plan from the operator's brief",
        run_id=run_id, agent=AGENT, max_tokens=4000, reasoning_effort="low",
    )
    return plan


def _handlers(ctx: Context, run: RunResult, brief: str) -> dict[str, Any]:
    def emit(kind: str, **data: Any) -> None:
        _save_state(ctx.conn, run, "running")
        if ctx.on_event:
            ctx.on_event(kind, data)

    def new_content() -> None:
        """Copy or images are about to change: the old review no longer applies."""
        if run.pending_id:
            raise ToolError("approval already requested; the run's content is frozen")
        run.review = None

    def run_copywriter(args: CopywriterArgs) -> dict[str, Any]:
        assert run.plan
        new_content()
        if not 0 <= args.angle_index < len(run.plan.angles):
            raise ToolError("angle_index must be 0, 1 or 2")
        angle = run.plan.angles[args.angle_index]
        run.copy, _ = copywriter.write(ctx.client, angle, angle.persona, run_id=run.run_id,
                                       brief=brief)
        emit("copy")
        return {"variants": [v.model_dump() for v in run.copy.variants],
                "risks": run.copy.risks}

    def run_art_director(args: ArtDirectorArgs) -> dict[str, Any]:
        headline = args.headline.strip()[:60]
        if not headline:
            raise ToolError("headline is required")
        new_content()
        run.creatives = art_director.run(
            ctx.client, ctx.photo_png, headline, run_id=run.run_id, concept=ctx.concept,
            data_dir=ctx.data_dir)
        with transaction(ctx.conn):
            for im in run.creatives.images:
                ctx.conn.execute(
                    "INSERT INTO creatives (id, run_id, placement, path, sha256, created_ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("cr_" + secrets.token_hex(6), run.run_id, im.placement, im.path,
                     im.sha256, time.time()))
        emit("creatives")
        return {"images": [{"placement": i.placement, "size": f"{i.width}x{i.height}"}
                           for i in run.creatives.images],
                "foreground_pixels": run.creatives.foreground_pixels}

    def run_reviewers(args: ReviewersArgs) -> dict[str, Any]:
        if run.copy is None or run.creatives is None:
            raise ToolError("run_copywriter and run_art_director must finish first")
        if not 0 <= args.variant_index < len(run.copy.variants):
            raise ToolError("variant_index must be 0, 1 or 2")
        images = []
        for im in run.creatives.images:
            png = (ctx.data_dir / im.path).read_bytes()
            if sha256_hex(png) != im.sha256:
                run.review = None
                raise ToolError(f"{im.placement} changed on disk since it was rendered")
            images.append((im.placement, im.sha256, png))
        result = reviewer.run_formats(ctx.client, ctx.open_client, images,
                                      run.copy.variants[args.variant_index], run_id=run.run_id)
        run.review = {**result, "variant_index": args.variant_index,
                      "binding": review_binding(run, args.variant_index)}
        emit("review")
        return {"verdict": result["verdict"], "second_opinion": result["second_opinion"],
                "disagreements": result["disagreements"],
                "formats": [{"placement": f["placement"], "verdict": f["verdict"],
                             "findings": f["primary"]["findings"]}
                            for f in result["formats"]]}

    def request_approval(args: ApprovalArgs) -> dict[str, Any]:
        if run.pending_id:
            raise ToolError("approval already requested for this run")
        if run.review is None:
            raise ToolError("run_reviewers must finish first")
        if run.review["verdict"] == "BLOCK":
            raise ToolError("the reviewers blocked this creative; fix it before approval")
        index = run.review["variant_index"]
        if run.review.get("binding") != review_binding(run, index):
            raise ToolError("content changed since review; run_reviewers again")
        assert run.copy
        if copywriter.has_unfilled_fact(run.copy.variants[index]):
            raise ToolError(f"variant {index} has an unfilled {copywriter.FACT} placeholder; "
                            "it cannot be sent for approval")
        payload = campaign_payload(run, index, ctx.landing_url)
        item_id = opaque_id("cmp_")
        pending = Pending.create("campaign.create", item_id, payload,
                                 ledger=ctx.client.ledger, run_id=run.run_id)
        with transaction(ctx.conn):
            pending.save(ctx.conn)
            ctx.conn.execute(
                "INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                "VALUES (?, ?, ?, ?)",
                (pending.id, run.run_id, canonical(payload).decode(), time.time()))
        run.pending_id = pending.id
        emit("approval")
        return {"pending_id": pending.id, "content_sha256": pending.content_sha256,
                "note": "the human must sign this in the studio; nothing was sent"}

    return {"run_copywriter": run_copywriter, "run_art_director": run_art_director,
            "run_reviewers": run_reviewers, "request_approval": request_approval}


def _dispatch_turn(ctx: Context, dispatcher: Dispatcher, calls: list[ToolCall],
                   used: Counter[str], max_cost_usd: float, run_id: str) -> list[Outcome]:
    """One turn's calls in order. The cost cap is checked before every call; calls past
    ``MAX_CALLS_PER_TURN`` or a tool's ``TOOL_LIMITS`` are refused (an output for the
    model), never dispatched."""
    outcomes = []
    for i, call in enumerate(calls):
        limit = TOOL_LIMITS.get(call.name)
        if i >= MAX_CALLS_PER_TURN:
            outcomes.append(dispatcher.refuse(
                call, f"too many tool calls per turn (max {MAX_CALLS_PER_TURN})"))
        elif limit is not None and used[call.name] >= limit:
            outcomes.append(dispatcher.refuse(
                call, f"per-run limit reached for this tool (max {limit})"))
        elif _cost(ctx, run_id) >= max_cost_usd:
            outcomes.append(dispatcher.refuse(call, "cost cap reached; call not run"))
        else:
            used[call.name] += 1
            outcomes.append(dispatcher.execute_or_refuse(call))
    return outcomes


def run(
    ctx: Context,
    brief: str,
    *,
    run_id: str | None = None,
    max_steps: int = MAX_STEPS,
    max_cost_usd: float = MAX_COST_USD,
) -> RunResult:
    brief = brief.strip()
    if not brief or len(brief) > MAX_BRIEF:
        raise ValueError(f"brief must be 1-{MAX_BRIEF} characters")
    run_id = run_id or new_run_id()
    result = RunResult(run_id=run_id, status="running")
    _save_state(ctx.conn, result, "running")
    try:
        result.plan = make_plan(ctx, brief, run_id)
        _save_state(ctx.conn, result, "running")
        if ctx.on_event:
            ctx.on_event("plan", {})
        dispatcher = Dispatcher(_handlers(ctx, result, brief), ledger=ctx.client.ledger,
                                run_id=run_id)
        tools = dispatcher.tools()
        next_input: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.system(AGENT)},
            {"role": "user", "content": (
                f"Brief:\n{brief}\n\nPlan (already written):\n"
                f"{result.plan.model_dump_json()}\n\nRun the plan with your tools now.")},
        ]
        previous: str | None = None
        used: Counter[str] = Counter()
        result.status = "max_steps"
        for step in range(max_steps):
            if _cost(ctx, run_id) >= max_cost_usd:
                result.status = "max_cost"
                break
            turn = ctx.client.respond(
                MODEL, next_input, run_id=run_id, agent=AGENT, tools=tools,
                reasoning={"effort": "medium", "summary": "auto"},
                previous_response_id=previous, max_output_tokens=4000,
                tier="standard", tier_reason="producer loop", parallel_tool_calls=True,
            )
            result.steps = step + 1
            if turn.reasoning_summary:
                result.reasoning.append(turn.reasoning_summary[:500])
            previous = turn.response_id
            if not turn.tool_calls:
                result.summary = turn.text[:2000]
                result.status = "awaiting_approval" if result.pending_id else "done"
                break
            outcomes = _dispatch_turn(ctx, dispatcher, turn.tool_calls, used, max_cost_usd,
                                      run_id)
            next_input = [o.as_input() for o in outcomes]
    except Exception:
        result.cost_usd = _cost(ctx, run_id)
        _save_state(ctx.conn, result, "failed")
        raise
    result.cost_usd = _cost(ctx, run_id)
    final = "awaiting_approval" if result.pending_id else (
        "done" if result.status == "done" else "failed")
    _save_state(ctx.conn, result, final)
    if ctx.on_event:
        ctx.on_event("finished", {"status": result.status})
    return result
