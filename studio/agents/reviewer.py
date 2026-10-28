"""Two reviewers: Muse Spark 1.3 vision (compliance) and Muse Spark Open on Baseten.

Both read the same image + copy against the policy checklist and return the same strict
``Review`` schema. ``reconcile`` merges them: BLOCK from either side is BLOCK, and every
difference (verdict, or a rule only one of them cites) is listed as a disagreement for the
approval screen. Without a Baseten key the second opinion is "unavailable" and the
primary review stands alone, labelled as such. ``run_formats`` reviews every placement
that will be signed, one by one; the worst verdict wins.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from studio.agents import prompts
from studio.agents.schemas import CopyVariant, Finding, Review
from studio.muse import openweights
from studio.muse.client import MuseClient, MuseError
from studio.muse.structured import StructuredOutputError, response_format
from studio.muse.vision import chat_image_part
from studio.receipts import canonical, sha256_hex

AGENT = "reviewer"
SECOND_AGENT = "reviewer_open"
MODEL = "muse-spark-1.3"
MAX_SIDE = 1024  # reviewers see a downscaled copy: enough for policy, fewer image tokens
_RANK = {"OK": 0, "WARN": 1, "BLOCK": 2}
_SEVERITY_FLOOR = {"info": "OK", "warn": "WARN", "block": "BLOCK"}
_RULE = re.compile(r"\bP\d+\b")


@dataclass(frozen=True)
class Reconciled:
    verdict: str
    primary: Review
    second: Review | None
    second_status: str  # "available" | "unavailable" | "failed"
    disagreements: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict, "second_opinion": self.second_status,
            "disagreements": self.disagreements, "primary": self.primary.model_dump(),
            "second": self.second.model_dump() if self.second else None,
        }


def downscale(png: bytes, max_side: int = MAX_SIDE) -> bytes:
    src = Image.open(io.BytesIO(png))
    if max(src.size) <= max_side:
        return png
    im = src.convert("RGB")
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def normalize(review: Review) -> Review:
    """Rule ids mapped onto the checklist (``"P4 Price"`` → ``P4``, unknown → ``UNMAPPED``);
    the verdict is never milder than the worst finding's severity."""
    valid = set(prompts.policy_rule_ids())
    findings = []
    for f in review.findings:
        m = _RULE.search(f.rule)
        rule = m.group() if m and m.group() in valid else "UNMAPPED"
        findings.append(Finding(rule=rule, evidence=f.evidence[:500], severity=f.severity))
    verdict = review.verdict
    for f in findings:
        floor = _SEVERITY_FLOOR[f.severity]
        if _RANK[floor] > _RANK[verdict]:
            verdict = floor  # type: ignore[assignment]
    return Review(verdict=verdict, findings=findings, summary=review.summary[:1000])


def _messages(image_png: bytes, copy: CopyVariant) -> list[dict[str, Any]]:
    text = (f"Primary text: {copy.primary_text}\nHeadline: {copy.headline}\n"
            f"Description: {copy.description}\nCTA: {copy.cta}")
    return [
        {"role": "system", "content": prompts.system(AGENT)},
        {"role": "user", "content": [{"type": "text", "text": text},
                                     chat_image_part(image_png)]},
    ]


def _parse(text: str, finish: str) -> Review:
    try:
        return normalize(Review.model_validate_json(text))
    except ValueError:
        raise StructuredOutputError(f"reviewer output is not a Review (finish={finish})",
                                    code="invalid_output") from None


def review(client: MuseClient, image_png: bytes, copy: CopyVariant, *, run_id: str) -> Review:
    """Compliance review on Muse Spark 1.3 vision (Standard tier: final creative)."""
    result = client.chat(
        MODEL, _messages(downscale(image_png), copy), run_id=run_id, agent=AGENT,
        response_format=response_format(Review), max_tokens=6000, reasoning_effort="low",
        tier="standard", tier_reason="compliance review of the final creative (vision)",
    )
    return _parse(result.text, result.finish)


def second_opinion(
    client: openweights.OpenWeightsClient, image_png: bytes, copy: CopyVariant, *, run_id: str
) -> Review:
    """Same review on open weights (Baseten). ``OpenWeightsUnavailable`` without a key."""
    if not client.available():
        raise openweights.OpenWeightsUnavailable("BASETEN_API_KEY is not set",
                                                 code="unavailable")
    result = client.chat(
        openweights.MODEL, _messages(downscale(image_png), copy), run_id=run_id,
        agent=SECOND_AGENT, response_format=response_format(Review), max_tokens=6000,
        reasoning_effort="low", tier="open",
        tier_reason="open-weights second opinion on the same creative",
    )
    return _parse(result.text, result.finish)


def reconcile(a: Review, b: Review | None, status: str = "available") -> Reconciled:
    if b is None:
        return Reconciled(a.verdict, a, None, status if status != "available"
                          else "unavailable", [])
    verdict = max(a.verdict, b.verdict, key=lambda v: _RANK[v])
    disagreements = []
    if a.verdict != b.verdict:
        disagreements.append(f"verdict: Muse Spark {a.verdict} vs Spark Open {b.verdict}")
    rules_a = {f.rule for f in a.findings if f.severity != "info"}
    rules_b = {f.rule for f in b.findings if f.severity != "info"}
    for rule in sorted(rules_a - rules_b):
        disagreements.append(f"{rule}: flagged by Muse Spark only")
    for rule in sorted(rules_b - rules_a):
        disagreements.append(f"{rule}: flagged by Spark Open only")
    return Reconciled(verdict, a, b, "available", disagreements)


def run(
    client: MuseClient,
    open_client: openweights.OpenWeightsClient | None,
    image_png: bytes,
    copy: CopyVariant,
    *,
    run_id: str,
) -> Reconciled:
    """Both reviews, reconciled, plus one ``review`` receipt with the outcome."""
    primary = review(client, image_png, copy, run_id=run_id)
    second: Review | None = None
    status = "unavailable"
    if open_client is not None and open_client.available():
        try:
            second = second_opinion(open_client, image_png, copy, run_id=run_id)
            status = "available"
        except MuseError:
            status = "failed"  # the failed call has its own receipt; the primary stands
    result = reconcile(primary, second, status)
    client.ledger.append(
        run_id, agent=AGENT, kind="review", input_sha256=[sha256_hex(image_png)],
        output_sha256=sha256_hex(canonical(result.as_dict())),
        details={"verdict": result.verdict, "second_opinion": result.second_status,
                 "disagreements": len(result.disagreements),
                 "rules": sorted({f.rule for f in primary.findings})},
    )
    return result


def run_formats(
    client: MuseClient,
    open_client: openweights.OpenWeightsClient | None,
    images: list[tuple[str, str, bytes]],
    copy: CopyVariant,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Review each ``(placement, sha256, png)``; the combined verdict is the worst one.

    ``disagreements`` are prefixed with the placement; ``second_opinion`` is "available"
    only when every format had one ("failed" if any failed)."""
    if not images:
        raise ValueError("nothing to review")
    formats = []
    for placement, digest, png in images:
        result = run(client, open_client, png, copy, run_id=run_id)
        formats.append({"placement": placement, "sha256": digest, **result.as_dict()})
    statuses = {f["second_opinion"] for f in formats}
    second = ("available" if statuses == {"available"}
              else "failed" if "failed" in statuses else "unavailable")
    return {
        "verdict": max((f["verdict"] for f in formats), key=lambda v: _RANK[v]),
        "second_opinion": second,
        "disagreements": [f"{f['placement']}: {d}" for f in formats
                          for d in f["disagreements"]],
        "formats": formats,
    }
