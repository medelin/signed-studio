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

Brief v1 (``studio.brief``): ``draft_brief`` turns the owner's free text or edited voice
transcript into a draft whose gaps are open ``[FACT]`` questions. A run made from the
confirmed brief (``Context.brief_v1``) plans from it, takes the owner's budget and
placements over the model's, gives the copywriter and the reviewers only its confirmed
facts, and binds ``brief_sha256`` into the approval. When the brief's objective promotes a
pixel (website leads or sales), ``check_pixel`` runs the adapter's read-only pixel check
before the approval is requested; the result goes into the signed payload
(``pixel_check``) and the run state, so the person sees it before signing.
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

from studio import brief as brief_v1
from studio import envelope, leadads
from studio.agents import art_director, copywriter, formats, prompts, reviewer
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
from studio.approvals import Pending, confirmed_brief, transaction
from studio.meta import audience, objectives, pixel
from studio.meta.adapter import MOCK_PAGE
from studio.meta.currency import to_minor
from studio.muse.client import MuseClient, MuseError, ToolCall
from studio.muse.openweights import OpenWeightsClient
from studio.muse.structured import structured
from studio.receipts import canonical, is_opaque, sha256_hex

AGENT = "producer"
MODEL = "muse-spark-1.3"
MAX_STEPS = 12
MAX_COST_USD = 1.0
MAX_BRIEF = 2000
MAX_BRIEF_V1 = 8000  # a rendered Brief v1 (``brief.render``)
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
    # "operator" (typed brief) or "transcript" (a voice brief, possibly edited): a
    # transcript is customer-grade text — enveloped, declared, never on Contributor.
    brief_source: str = "operator"
    max_daily_budget: int = 50  # STUDIO_MAX_DAILY_BUDGET, in the ad account's currency
    currency: str = "PLN"  # the ad account's currency (Adapter.account_info)
    # The confirmed Brief v1 this run is made from (None: a free-text brief) and its hash
    # (the ``brief.confirmed`` receipt is written before the run starts).
    brief_v1: brief_v1.BriefV1 | None = None
    brief_sha256: str = ""
    page_id: str = MOCK_PAGE  # the Facebook Page the campaign promotes (META_PAGE_ID)
    # the targeting search of the Meta adapter (read-only): interest and language hints →
    # Meta ids, resolved by code before the approval is requested
    search: audience.Search | None = None
    # the Google Ads adapter (None: Google Search is not offered to this run)
    google: Any = None
    # the Meta adapter's read-only pixel check (``Adapter.pixel_check``); None: a pixel
    # objective gets a failed "pixel_unverified" result, which cannot be signed
    pixel_check: Callable[..., dict[str, Any]] | None = None


def _limit(ctx: Context) -> int:
    return MAX_BRIEF_V1 if ctx.brief_v1 is not None else MAX_BRIEF


def brief_block(ctx: Context, brief: str) -> str:
    """The brief as it enters a prompt: plain for the operator, enveloped for a voice
    transcript."""
    if ctx.brief_source == "transcript":
        return envelope.wrap(brief, "transcript", max_chars=_limit(ctx))
    return brief


def _system(ctx: Context) -> str:
    base = prompts.system(AGENT)
    return base + "\n\n" + envelope.SYSTEM_RULE if ctx.brief_source == "transcript" else base


def _sources(ctx: Context) -> list[str]:
    return ["transcript"] if ctx.brief_source == "transcript" else []


@dataclass
class RunResult:
    run_id: str
    status: str  # awaiting_approval | done | max_steps | max_cost | failed
    plan: Plan | None = None
    copy: CopyPack | None = None
    creatives: CreativeSet | None = None
    review: dict[str, Any] | None = None
    pending_id: str = ""
    google_pending_id: str = ""  # the Google Search approval, when the brief chose it
    google_error: str = ""  # why there is none (the Meta approval is unaffected)
    pixel_check: dict[str, Any] | None = None  # the pixel check (pixel objectives only)
    angle_index: int = 0  # the plan angle the current copy was written for
    steps: int = 0
    cost_usd: float = 0.0
    summary: str = ""
    reasoning: list[str] = field(default_factory=list)

    def state(self) -> dict[str, Any]:
        return {
            "plan": self.plan.model_dump() if self.plan else None,
            "copy": self.copy.model_dump() if self.copy else None,
            "creatives": self.creatives.model_dump() if self.creatives else None,
            "review": self.review, "pending_id": self.pending_id,
            "google_pending_id": self.google_pending_id, "google_error": self.google_error,
            "pixel_check": self.pixel_check,
            "angle_index": self.angle_index, "steps": self.steps,
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
                     link: str = "https://example.com/", *,
                     ceiling: int = 50,
                     currency: str = "PLN",
                     brief: brief_v1.BriefV1 | None = None,
                     page_id: str = MOCK_PAGE,
                     resolved: audience.Resolved | None = None,
                     pixel_check: dict[str, Any] | None = None) -> dict[str, Any]:
    """The exact content a ``campaign.create`` approval authorises (built by code). A
    daily budget above the activation ceiling is refused here, before anyone signs a
    campaign that could never be activated. ``delivery`` is the objective mapping
    (``objectives.delivery``: objective, optimisation goal, billing event, destination,
    promoted object) for the brief's goal and sub-choice, or traffic for a free-text brief.
    With a confirmed ``brief`` the payload also carries its ``brief_sha256``, objective,
    budget and the brief's targeting: Advantage+ audience unless the owner turned it off,
    the hints ``resolved`` to Meta ids (``audience``, shown before signing), exclusions,
    the owner's custom audiences and, from the owner's source audience, a lookalike.
    ``pixel_check`` (``check_pixel``) is signed with the rest: ``pixel.gate`` refuses a
    pixel objective without a passing check of its own pixel."""
    assert run.plan and run.copy and run.creatives and run.review
    if run.plan.budget_daily > ceiling:
        raise ToolError(f"daily budget {run.plan.budget_daily} {currency} is above the "
                        f"{ceiling} {currency} ceiling; lower it in the plan")
    try:
        spec = (objectives.delivery(brief.objective.goal, brief.objective.sub,
                                    page_id=page_id, promotion=brief.promotion.model_dump())
                if brief is not None else objectives.delivery("traffic", page_id=page_id))
    except objectives.DeliveryError as exc:
        raise ToolError(f"the objective cannot be mapped: {exc}") from None
    v = run.copy.variants[variant_index]
    payload = {
        "action": "campaign.create",
        "run_id": run.run_id,
        "delivery": spec,
        "campaign": {"objective": spec["objective"], "status": "PAUSED",
                     "daily_budget_minor": to_minor(run.plan.budget_daily, currency),
                     "currency": currency, "angle": run.plan.angles[run.angle_index].name},
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
    ab = [x["variant_index"] for x in run.review.get("ab", [])]
    if ab:  # copy A/B: every reviewed variant runs (dynamic creative or separate ads)
        payload["ad_variants"] = [
            {k: getattr(run.copy.variants[i], k) for k in ("primary_text", "headline",
                                                            "description", "cta")}
            for i in [variant_index, *ab]]
    cards, clip = run.creatives.cards, run.creatives.video
    if cards or clip:
        payload["format"] = {"cards": list(cards), "video": {
            "sha256": clip.sha256, "seconds": clip.seconds, "poster": clip.poster,
            "storyboard": clip.storyboard} if clip else None}
    if pixel_check is not None:
        payload["pixel_check"] = pixel.signed(pixel_check)
    if spec["creative"] == "lead_form":
        assert brief is not None  # an instant form exists only for a confirmed brief
        # the questions the owner chose and the owner's own privacy page (Meta requires it)
        payload["lead_form"] = leadads.form_spec(
            f"Muse Studio · {run.plan.angles[run.angle_index].name} · form",
            spec["lead_fields"], brief.promotion.privacy_policy_url, link)
    if brief is not None:
        # what the owner confirmed, by hash, plus the parts the campaign is built from
        payload.update({
            "brief_sha256": brief_v1.brief_sha256(brief),
            "brief": {"schema": brief_v1.SCHEMA, "goal": brief.objective.goal,
                      "sub": spec["sub"],
                      "kpi": brief.objective.kpi, "budget": brief.budget.model_dump(),
                      "automatic_placements": brief.placements.automatic,
                      "facts": len(brief.confirmed_facts)},
        })
        au = brief.audience
        res = resolved or audience.Resolved()
        payload["targeting"] = audience.targeting(
            countries=brief.advertiser.countries, age_min=au.age_min, age_max=au.age_max,
            genders=au.genders, resolved=res, excluded_countries=au.excluded_countries,
            custom_audiences=au.custom_audience_ids,
            excluded_custom_audiences=au.excluded_custom_audience_ids,
            advantage=au.advantage_audience)
        payload["audience_resolution"] = res.as_payload()
        if au.lookalike_source_id:
            payload["lookalike"] = audience.lookalike_spec(
                au.lookalike_source_id, au.lookalike_percent, brief.advertiser.countries[0],
                f"Muse Studio · lookalike {au.lookalike_percent}%")
    return payload


def request_google(ctx: Context, run: RunResult, variants: list[int], link: str) -> None:
    """When the confirmed brief chose Google Search: a second approval (``google.create``)
    for one PAUSED Search campaign made from the same brief and the reviewed copy. A brief
    that cannot make a valid Responsive Search Ad leaves ``google_error`` (the Meta
    approval is unaffected)."""
    brief = ctx.brief_v1
    if brief is None or not brief.channels.google_search or ctx.google is None:
        return
    assert run.copy
    from studio.agents import google_buyer
    from studio.google.adapter import SpecError

    item_id = opaque_id("gcmp_")
    try:
        info = ctx.google.account_info()
        payload = google_buyer.payload_for(
            run_id=run.run_id, item_id=item_id, brief=brief, brief_sha256=ctx.brief_sha256,
            link=link, variants=[run.copy.variants[i] for i in variants],
            customer_id=ctx.google.customer_id, currency=info["currency"],
            ceiling=ctx.max_daily_budget)
    except SpecError as exc:
        run.google_error = exc.code
        return
    except Exception as exc:  # noqa: BLE001 - the Google account is unreachable
        run.google_error = getattr(exc, "code", "") or type(exc).__name__
        return
    pending = Pending.create("google.create", item_id, payload, ledger=ctx.client.ledger,
                             run_id=run.run_id, brief_sha256=ctx.brief_sha256)
    with transaction(ctx.conn):
        pending.save(ctx.conn)
        ctx.conn.execute(
            "INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
            "VALUES (?, ?, ?, ?)",
            (pending.id, run.run_id, canonical(payload).decode(), time.time()))
    run.google_pending_id = pending.id


def resolve_audience(ctx: Context, run_id: str,
                     brief: brief_v1.BriefV1) -> audience.Resolved:
    """The brief's interest and language hints → Meta ids (targeting search, read-only),
    with one receipt naming how many were looked up and resolved."""
    au = brief.audience
    try:
        res = audience.resolve(ctx.search, au.interests, au.excluded_interests, au.languages)
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - a failed lookup leaves the hints unresolved
        res = audience.Resolved(unresolved=[*au.interests, *au.excluded_interests])
        status = getattr(exc, "code", type(exc).__name__)
    ctx.client.ledger.append(run_id, agent=AGENT, kind="tool_call", details={
        "tool": "targeting.search", "status": status, "side_effects": "read",
        "searches": res.searches, "interests": len(res.interests) + len(
            res.excluded_interests), "locales": len(res.locales),
        "unresolved": len(res.unresolved)})
    return res


def check_pixel(ctx: Context, run_id: str, brief: brief_v1.BriefV1,
                link: str) -> dict[str, Any] | None:
    """The read-only pixel check for a brief whose objective promotes a pixel (website
    leads, sales); None for every other objective (an instant form, traffic …), which
    skips it entirely. One ``pixel.check`` receipt (``side_effects: read``) carries the
    status, the check codes and the result's hash; the pixel id is kept by hash only."""
    try:
        r = objectives.row(brief.objective.goal, brief.objective.sub)
    except objectives.DeliveryError:
        return None  # campaign_payload refuses the objective itself
    if "pixel_id" not in r.promoted:
        return None
    pid = brief.promotion.pixel_id
    event = brief.promotion.custom_event_type or r.event
    status = "ok"
    if ctx.pixel_check is None:
        result = pixel.unverified(pid, "no_adapter", link)
        status = "unavailable"
    else:
        try:
            result = ctx.pixel_check(pid, link, event_type=event)
        except Exception as exc:  # noqa: BLE001 - an adapter bug must not pass the gate
            result = pixel.unverified(pid, getattr(exc, "code", "") or type(exc).__name__,
                                      link)
            status = getattr(exc, "code", "") or type(exc).__name__
    ctx.client.ledger.append(run_id, agent=AGENT, kind="tool_call", details={
        "tool": pixel.TOOL, "status": status, "side_effects": "read",
        "pixel_sha256": sha256_hex(pid), "pixel_status": result["status"],
        "checks": [f"{c['code']}:{c['level']}" for c in result["checks"]],
        "events_7d": (result.get("events_7d") or {}).get("total"),
        "result_sha256": sha256_hex(canonical(result))})
    return result


def ab_variants(ctx: Context, run: RunResult, chosen: int) -> list[int]:
    """The copy variants that will run: the chosen one, then (copy A/B in the brief) the
    next ones in order, up to ``placements.copy_variants``."""
    assert run.copy
    n = ctx.brief_v1.placements.copy_variants if ctx.brief_v1 is not None else 1
    order = [chosen] + [i for i in range(len(run.copy.variants)) if i != chosen]
    return order[:max(1, min(n, len(order)))]


def review_binding(run: RunResult, variant_index: int, ab: list[int] | None = None) -> str:
    """Hash of exactly what a review covers: the chosen variant (and the A/B variants)
    and every image, carousel card and video."""
    assert run.copy and run.creatives
    body: dict[str, Any] = {
        "variant_index": variant_index,
        "variant": run.copy.variants[variant_index].model_dump(),
        "images": [{"placement": i.placement, "sha256": i.sha256}
                   for i in run.creatives.images],
    }
    if ab:
        body["ab"] = [{"index": i, "variant": run.copy.variants[i].model_dump()} for i in ab]
    if run.creatives.video:
        body["video"] = run.creatives.video.sha256
    return sha256_hex(canonical(body))


# A signed run stays ``approved``: the producer's last writes never move it back (the
# CASE in both upserts below).


def _save_state(conn: sqlite3.Connection, run: RunResult, status: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO run_state (run_id, status, state, updated_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET status = CASE WHEN run_state.status = "
            "'approved' THEN 'approved' ELSE excluded.status END, "
            "state = excluded.state, updated_ts = excluded.updated_ts",
            (run.run_id, status, json.dumps(run.state(), ensure_ascii=False), time.time()),
        )


def mark_failed(conn: sqlite3.Connection, run_id: str, error_type: str) -> None:
    """A crashed run is ``failed`` (an existing state is kept; ``approved`` stays)."""
    with transaction(conn):
        conn.execute(
            "INSERT INTO run_state (run_id, status, state, updated_ts) "
            "VALUES (?, 'failed', ?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET status = CASE WHEN run_state.status = "
            "'approved' THEN 'approved' ELSE excluded.status END, "
            "updated_ts = excluded.updated_ts",
            (run_id, json.dumps({"error": error_type}), time.time()),
        )


def _cost(ctx: Context, run_id: str) -> float:
    return sum(r.cost_usd for r in ctx.client.ledger.receipts(run_id))


def make_plan(ctx: Context, brief: str, run_id: str) -> Plan:
    """The model's plan; with a confirmed Brief v1 the owner's daily budget and
    deliverables replace the model's (code, not the model, copies them)."""
    plan, _ = structured(
        ctx.client, MODEL,
        [{"role": "system", "content": _system(ctx)},
         {"role": "user", "content": f"Brief:\n{brief_block(ctx, brief)}\n\n"
                                     f"Ad account currency: {ctx.currency}\n"
                                     "Write the campaign plan only."}],
        Plan, tier="standard", tier_reason=(
            "producer plan from a voice transcript" if ctx.brief_source == "transcript"
            else "producer plan from the operator's brief"),
        run_id=run_id, agent=AGENT, max_tokens=4000, reasoning_effort="low",
        envelope_sources=_sources(ctx),
    )
    if ctx.brief_v1 is not None:
        plan = plan.model_copy(update={
            "budget_daily": brief_v1.daily_budget(ctx.brief_v1),
            "placements": list(ctx.brief_v1.placements.deliverables)})
    return plan


def draft_brief(client: MuseClient, text: str, *, run_id: str, source: str = "operator",
                lang: str = "en",
                landing_url: str = "https://example.com/") -> brief_v1.BriefV1:
    """Brief v1 drafted by Muse Spark from the owner's text or edited voice transcript
    (strict ``BriefV1`` schema), then ``brief.ground_draft``: a fact holding a number the
    owner never wrote becomes an open ``[FACT]`` question."""
    text = text.strip()
    if not text or len(text) > brief_v1.MAX_SOURCE:
        raise ValueError(f"brief text must be 1-{brief_v1.MAX_SOURCE} characters")
    transcript = source == "transcript"
    owner = envelope.wrap(text, "transcript", max_chars=brief_v1.MAX_SOURCE) if transcript \
        else text
    system = prompts.brief_system() + ("\n\n" + envelope.SYSTEM_RULE if transcript else "")
    draft, _ = structured(
        client, MODEL,
        [{"role": "system", "content": system},
         {"role": "user", "content": f"Owner's language: {lang}\nDefault landing page: "
                                     f"{landing_url}\n\nOwner's words:\n{owner}\n\n"
                                     "Draft the Brief v1 only."}],
        brief_v1.BriefV1, tier="standard", tier_reason=(
            "brief draft from a voice transcript" if transcript
            else "brief draft from the owner's text"),
        run_id=run_id, agent=AGENT, max_tokens=4000, reasoning_effort="low",
        envelope_sources=["transcript"] if transcript else (),
    )
    return brief_v1.ground_draft(draft, text, lang=lang)


def _headline_key(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def _handlers(ctx: Context, run: RunResult, brief: str,
              max_cost_usd: float = MAX_COST_USD) -> dict[str, Any]:
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
        run.copy, _ = copywriter.write(
            ctx.client, angle, angle.persona, run_id=run.run_id, brief=brief,
            brief_source=ctx.brief_source,
            facts=ctx.brief_v1.confirmed_facts if ctx.brief_v1 is not None else None)
        run.angle_index = args.angle_index
        emit("copy")
        return {"variants": [v.model_dump() for v in run.copy.variants],
                "risks": run.copy.risks}

    def run_art_director(args: ArtDirectorArgs) -> dict[str, Any]:
        """The headline in the image is a copy variant's headline (the model only picks
        which one); the placements are the plan's."""
        assert run.plan
        if run.copy is None:
            raise ToolError("run_copywriter must finish first")
        wanted = _headline_key(args.headline)
        headline = next((v.headline for v in run.copy.variants
                         if _headline_key(v.headline) == wanted), None)
        if headline is None:
            raise ToolError("headline must be one of the copy variants' headlines")
        new_content()
        run.creatives = None
        fmt = ctx.brief_v1.placements if ctx.brief_v1 is not None else None
        try:
            run.creatives = art_director.run(
                ctx.client, ctx.photo_png, headline[:60], run_id=run.run_id,
                concept=ctx.concept, data_dir=ctx.data_dir,
                placements=art_director.clean_placements(run.plan.placements),
                can_spend=lambda usd: _cost(ctx, run.run_id) + usd <= max_cost_usd,
                carousel_cards=fmt.carousel_cards if fmt else 0,
                with_video=bool(fmt and fmt.video),
                advertiser=ctx.brief_v1.advertiser.display_name if ctx.brief_v1 else "")
        except art_director.CostCapReached:
            raise ToolError("cost cap reached while rendering; no creative set") from None
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
                                      run.copy.variants[args.variant_index], run_id=run.run_id,
                                      brief=ctx.brief_v1)
        # copy A/B: every further variant that will run is reviewed on every image too
        ab = []
        for i in ab_variants(ctx, run, args.variant_index)[1:]:
            extra = reviewer.run_formats(ctx.client, ctx.open_client, images,
                                         run.copy.variants[i], run_id=run.run_id,
                                         brief=ctx.brief_v1)
            ab.append({**extra, "variant_index": i})
        worst = max([result["verdict"], *(x["verdict"] for x in ab)],
                    key=reviewer.RANK.__getitem__)
        run.review = {**result, "verdict": worst, "primary_verdict": result["verdict"],
                      "ab": ab, "variant_index": args.variant_index,
                      "binding": review_binding(run, args.variant_index,
                                                [x["variant_index"] for x in ab])}
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
        ab = [x["variant_index"] for x in run.review.get("ab", [])]
        if run.review.get("binding") != review_binding(run, index, ab):
            raise ToolError("content changed since review; run_reviewers again")
        assert run.copy
        if run.copy.unfilled_facts > 0:
            raise ToolError(f"the copy reports {run.copy.unfilled_facts} unfilled "
                            f"{copywriter.FACT} placeholder(s); rewrite it before approval")
        for i in [index, *ab]:
            if copywriter.has_unfilled_fact(run.copy.variants[i]):
                raise ToolError(f"variant {i} has an unfilled {copywriter.FACT} "
                                "placeholder; it cannot be sent for approval")
            bad = copywriter.unsupported_character(run.copy.variants[i])
            if bad:
                raise ToolError(bad)
        if run.creatives and run.creatives.video:
            found = formats.check_video(run.creatives.video.seconds, run.creatives.video.bytes)
            if found:
                raise ToolError(found[0].evidence)
        link = ctx.landing_url
        if ctx.brief_v1 is not None:
            if confirmed_brief(ctx.client.ledger, run.run_id) != ctx.brief_sha256 or \
                    brief_v1.brief_sha256(ctx.brief_v1) != ctx.brief_sha256:
                raise ToolError("the confirmed brief changed during the run; confirm it again")
            for i in [index, *ab]:
                ungrounded = reviewer.grounding_net(run.copy.variants[i], ctx.brief_v1)
                if ungrounded:
                    raise ToolError(f"variant {i} states something the owner did not "
                                    f"confirm (rule P10): {ungrounded[0].evidence[:200]}")
            link = ctx.brief_v1.advertiser.landing_url
        resolved = None
        if ctx.brief_v1 is not None:
            resolved = resolve_audience(ctx, run.run_id, ctx.brief_v1)
            run.pixel_check = check_pixel(ctx, run.run_id, ctx.brief_v1, link)
        payload = campaign_payload(run, index, link, ceiling=ctx.max_daily_budget,
                                   currency=ctx.currency,
                                   brief=ctx.brief_v1, page_id=ctx.page_id,
                                   resolved=resolved, pixel_check=run.pixel_check)
        item_id = opaque_id("cmp_")
        pending = Pending.create("campaign.create", item_id, payload,
                                 ledger=ctx.client.ledger, run_id=run.run_id,
                                 brief_sha256=ctx.brief_sha256)
        with transaction(ctx.conn):
            pending.save(ctx.conn)
            ctx.conn.execute(
                "INSERT INTO pending_payloads (approval_id, run_id, payload, created_ts) "
                "VALUES (?, ?, ?, ?)",
                (pending.id, run.run_id, canonical(payload).decode(), time.time()))
        run.pending_id = pending.id
        request_google(ctx, run, [index, *ab], link)
        emit("approval")
        out = {"pending_id": pending.id, "content_sha256": pending.content_sha256,
               "note": "the human must sign this in the studio; nothing was sent"}
        if run.pixel_check is not None:
            out["pixel_check"] = run.pixel_check["status"]
            if run.pixel_check["status"] == pixel.FAIL:
                out["note"] = ("the pixel check failed: this approval cannot be signed "
                               "until the owner fixes the pixel and runs again")
        return out

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


TURN_RETRY_DELAY_S = 1.5


def _replayable(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A turn's output as input for a stateless request: function calls and assistant
    text only (no item ids, no reasoning items that live only on the server)."""
    out: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") == "function_call":
            out.append({"type": "function_call", "call_id": item.get("call_id", ""),
                        "name": item.get("name", ""), "arguments": item.get("arguments", "")})
        elif item.get("type") == "message":
            text = "".join(str(c.get("text", "")) for c in item.get("content") or []
                           if isinstance(c, dict) and c.get("type") == "output_text")
            if text:
                out.append({"role": "assistant", "content": text})
    return out


def _turn(ctx: Context, next_input: list[dict[str, Any]], run_id: str,
          tools: list[dict[str, Any]], previous: str | None,
          transcript: list[dict[str, Any]] | None = None) -> Any:
    """One producer turn. A chained turn that gets a 400 ("referenced response not found
    or expired", seen in real runs) is retried once; if it fails again, the whole
    conversation so far (``transcript`` + ``next_input``) is sent without chaining."""
    def call(inp: list[dict[str, Any]], prev: str | None) -> Any:
        return ctx.client.respond(
            MODEL, inp, run_id=run_id, agent=AGENT, tools=tools,
            reasoning={"effort": "medium", "summary": "auto"},
            previous_response_id=prev, max_output_tokens=4000,
            tier="standard", tier_reason="producer loop", parallel_tool_calls=True,
            # the brief travels in the first turn (or the full transcript); later turns
            # chain to it
            envelope_sources=_sources(ctx) if prev is None else (),
        )

    for attempt in range(2):
        try:
            return call(next_input, previous)
        except MuseError as exc:
            if previous is None or exc.status != 400:
                raise
        if attempt == 0:
            time.sleep(TURN_RETRY_DELAY_S)
    if transcript is None:
        raise MuseError("chained turn failed twice and there is no transcript",
                        status=400, code="previous_response_unavailable")
    return call([*transcript, *next_input], None)


def run(
    ctx: Context,
    brief: str,
    *,
    run_id: str | None = None,
    max_steps: int = MAX_STEPS,
    max_cost_usd: float = MAX_COST_USD,
) -> RunResult:
    brief = brief.strip()
    if not brief or len(brief) > _limit(ctx):
        raise ValueError(f"brief must be 1-{_limit(ctx)} characters")
    run_id = run_id or new_run_id()
    result = RunResult(run_id=run_id, status="running")
    _save_state(ctx.conn, result, "running")
    try:
        result.plan = make_plan(ctx, brief, run_id)
        _save_state(ctx.conn, result, "running")
        if ctx.on_event:
            ctx.on_event("plan", {})
        dispatcher = Dispatcher(_handlers(ctx, result, brief, max_cost_usd),
                                ledger=ctx.client.ledger, run_id=run_id)
        tools = dispatcher.tools()
        next_input: list[dict[str, Any]] = [
            {"role": "system", "content": _system(ctx)},
            {"role": "user", "content": (
                f"Brief:\n{brief_block(ctx, brief)}\n\nPlan (already written):\n"
                f"{result.plan.model_dump_json()}\n\nRun the plan with your tools now.")},
        ]
        previous: str | None = None
        transcript: list[dict[str, Any]] = []  # everything before next_input (fallback)
        used: Counter[str] = Counter()
        result.status = "max_steps"
        for step in range(max_steps):
            if _cost(ctx, run_id) >= max_cost_usd:
                result.status = "max_cost"
                break
            try:
                turn = _turn(ctx, next_input, run_id, tools, previous, transcript)
            except MuseError as exc:
                if not result.pending_id:
                    raise
                # The approval is already requested: the run is complete, only the
                # closing summary is missing (the failed call has its error receipt).
                result.summary = f"(summary unavailable: the model call failed, {exc.code})"
                result.status = "awaiting_approval"
                break
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
            transcript += [*next_input, *_replayable(turn.output_items)]
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
