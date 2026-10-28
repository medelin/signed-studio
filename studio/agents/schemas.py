"""Structured outputs of the agents.

Model outputs (``MODEL_OUTPUTS``) are sent as strict JSON schemas, so the schemas carry no
length constraints the strict subset may not support; counts are enforced by validators
after the response is parsed. ``CreativeSet`` is produced by code, never by a model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from studio.muse.structured import strict_schema

__all__ = [
    "MODEL_OUTPUTS",
    "Angle",
    "CopyPack",
    "CopyVariant",
    "CreativeImage",
    "CreativeSet",
    "Finding",
    "Kpi",
    "Plan",
    "Report",
    "Review",
    "Score",
    "Triage",
    "strict_schema",
]

Placement = Literal["feed_1x1", "feed_4x5", "story_9x16"]
Cta = Literal["LEARN_MORE", "SHOP_NOW", "SIGN_UP", "CONTACT_US", "BOOK_NOW", "GET_OFFER"]
Verdict = Literal["OK", "WARN", "BLOCK"]
Severity = Literal["info", "warn", "block"]

PLACEMENT_SIZES: dict[str, str] = {
    "feed_1x1": "1024x1024",
    "feed_4x5": "1024x1280",
    "story_9x16": "1080x1920",
}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Angle(_Closed):
    name: str
    hook: str
    persona: str


class Plan(_Closed):
    audience: str
    angles: list[Angle]
    budget_pln_daily: int
    placements: list[Placement]
    tasks: list[str]

    @model_validator(mode="after")
    def _counts(self) -> Plan:
        if len(self.angles) != 3:
            raise ValueError("a plan has exactly 3 angles")
        if not self.placements:
            raise ValueError("a plan needs at least one placement")
        if len(set(self.placements)) != len(self.placements):
            raise ValueError("placements must be unique")
        if len(self.placements) > len(PLACEMENT_SIZES):
            raise ValueError(f"at most {len(PLACEMENT_SIZES)} placements")
        if not 1 <= self.budget_pln_daily <= 500:
            raise ValueError("daily budget must be 1-500 PLN")
        return self


class CopyVariant(_Closed):
    primary_text: str
    headline: str
    description: str
    cta: Cta


class CopyPack(_Closed):
    variants: list[CopyVariant]
    risks: list[str]
    # How many [FACT] placeholders the copy still holds. Required in the strict schema the
    # model gets; the default only covers recordings made before the field existed. The
    # copywriter raises it to its own count, so it is never lower than what code finds.
    unfilled_facts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _three(self) -> CopyPack:
        if len(self.variants) != 3:
            raise ValueError("a copy pack has exactly 3 variants")
        return self


class CreativeImage(_Closed):
    placement: str
    size: str
    path: str
    sha256: str
    width: int
    height: int
    response_id: str


class CreativeSet(_Closed):
    """Produced by the art director (code), not by a model."""

    cutout_path: str
    cutout_sha256: str
    foreground_pixels: int
    images: list[CreativeImage]
    prompts: list[str]
    response_ids: list[str]


class Finding(_Closed):
    rule: str
    evidence: str
    severity: Severity


class Review(_Closed):
    verdict: Verdict
    findings: list[Finding]
    summary: str


class Triage(_Closed):
    intent: Literal["question", "price", "lead", "complaint", "praise", "spam", "other"]
    sentiment: Literal["positive", "neutral", "negative"]
    risk: Literal["low", "medium", "high"]
    requires_human: bool
    draft: str
    reason: str
    confidence: float


class Score(_Closed):
    score: int
    temp: Literal["HOT", "WARM", "COLD"]
    persona: str
    reasons: list[str]
    next_action: str


class Kpi(_Closed):
    name: str
    value: str


class Report(_Closed):
    summary: str
    kpis: list[Kpi]
    wins: list[str]
    losses: list[str]
    recommendations: list[str]
    evidence_refs: list[str]

    @model_validator(mode="after")
    def _max_three(self) -> Report:
        if len(self.recommendations) > 3:
            raise ValueError("at most 3 recommendations")
        return self


MODEL_OUTPUTS: tuple[type[BaseModel], ...] = (Plan, CopyPack, Review, Triage, Score, Report)
