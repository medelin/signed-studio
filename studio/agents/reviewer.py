"""Two reviewers: Muse Spark 1.3 vision (compliance) and Muse Spark Open on Baseten.

Both read the same image + copy against the policy checklist and return the same strict
``Review`` schema. ``reconcile`` merges them: BLOCK from either side is BLOCK, and every
difference (verdict, or a rule only one of them cites) is listed as a disagreement for the
approval screen. Without a Baseten key the second opinion is "unavailable" and the
primary review stands alone, labelled as such. ``run_formats`` reviews every placement
that will be signed, one by one; the worst verdict wins.

For an ad made from a confirmed Brief v1 both reviewers also get the owner's confirmed
statements and rule P10 (``prompts/_grounding.md``) in the user message, and code adds
``grounding_net``: a number the owner never confirmed, or a claim the brief bans, is a
P10 block.

Every finding's level (block / warn / tip) and source come from one table in code
(``rules.RULES``), by rule and by the channels the copy will run on: the models tag a
level and a source, ``normalize`` caps the level and sets the source, and the verdict is
derived from the capped findings (a tip never raises it). P9 is a Google Ads policy: a
block only when the brief also runs on Google Search, a tip on Meta.

Rules S1 (product fidelity) and S2 (no people) are code too, from SAM 3.1 on each
generated image (``sam_findings``): the product missing is a block; a silhouette that
does not match the cut-out, a product that is too small, too large or cut by an edge, or
a person in the image is a warning, always with the numbers.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from studio import brief as brief_v1
from studio import envelope
from studio.agents import formats, prompts, rules
from studio.agents.schemas import CopyVariant, Finding, Review, SamCheck
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
RANK = _RANK
_RULE = re.compile(r"\b[PFS]\d+\b")
SAM_RULE = "S1"  # product fidelity (SAM 3.1 on the generated image)
PEOPLE_RULE = "S2"  # no people (the placement prompt forbids them)
SAM_RULES = (SAM_RULE, PEOPLE_RULE)
# silhouette IoU vs the cut-out: below WARN the product was redrawn, below BLOCK it is
# another object
IOU_WARN, IOU_BLOCK = 0.5, 0.25
# product mask share of the image, per placement (a story leaves room for the headline)
AREA_BOUNDS: dict[str, tuple[float, float]] = {"story_9x16": (0.03, 0.55)}
AREA_DEFAULT = (0.05, 0.65)

# P9, checked by code as well as by both models: claims of registering companies or
# providing official documents, and absolute tax/legal promises. Patterns run on
# lower-case text with diacritics removed (``załóż spółkę`` → ``zaloz spolke``).
P9_POLICIES = ('Google Ads policies "Government documents and official services" and '
               '"Exaggerated or inaccurate claims"')
_P9 = [re.compile(p) for p in (
    r"\bregist(?:er|ering|ration)\b.{0,30}\b(?:compan|business|ltd|limited|spolk|firm)",
    r"\b(?:compan(?:y|ies)|business|ltd)\s+(?:registration|formation)\b",
    r"\brejestracj\w*\s+(?:spolk|firm|dzialalnos)",
    r"\b(?:zaloz|zalozymy|zalozysz|zarejestruj)\w*\s+(?:\w+\s+)?(?:spolk|firm|dzialalnos)",
    r"\b(?:official|government)\b.{0,25}\b(?:registration|documents?|filing|services?)\b",
    r"\bfile\b.{0,30}\bgovernment\b",
    r"\burzedow\w*\s+dokument|\bdokument\w*\s+urzedow",
    r"\b(?:no|zero|bez)\s+zus\b|\bzus\s+obligation|\bnie\s+placisz\s+zus",
    r"\b(?:brak|bez)\s+obowiazk\w*\s+(?:placenia\s+)?zus\b",
    r"\bpay\s+no\s+tax|\bno\s+tax(?:es)?\b|\btax[- ]free\b|\bstop\s+paying\s+"
    r"(?:tax|contributions|zus)",
    r"\bzero\s+(?:tax|podatk)|\bbez\s+podatk|\bnie\s+placisz\s+podatk",
    r"\b(?:100\s?%|fully|completely|guaranteed)\s+legal\b",
)]


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def policy_net(copy: CopyVariant, channels: tuple[str, ...] = (rules.META,)) -> list[Finding]:
    """P9 findings the code is sure of (one per matched phrase, at most three): a block
    when the copy runs on Google, a tip with the Google source on Meta (``rules``)."""
    text = _fold(" | ".join((copy.headline, copy.primary_text, copy.description)))
    hits: list[str] = []
    for pattern in _P9:
        for m in pattern.finditer(text):
            if m.group() not in hits:
                hits.append(m.group())
    level = rules.level("P9", "block", channels)
    return [Finding(rule="P9", severity=level,  # type: ignore[arg-type]
                    evidence=f"code check: matched {h[:80]!r} ({P9_POLICIES})",
                    source=rules.source("P9"))
            for h in hits[:3]]


def grounding_net(copy: CopyVariant, brief: brief_v1.BriefV1 | None) -> list[Finding]:
    """Rule P10 by code: numbers in the copy that the owner never confirmed (facts, offer,
    reasons, proposition, disclaimers, dates, display name) and banned claims."""
    if brief is None:
        return []
    text = " | ".join((copy.headline, copy.primary_text, copy.description))
    corpus = brief_v1.grounding_corpus(brief)
    out = [Finding(rule=prompts.GROUNDING_RULE, severity="block", source=rules.GROUNDING,
                   evidence=f"code check: the number {n!r} is not in the owner's confirmed "
                            "facts")
           for n in brief_v1.ungrounded_numbers(text, corpus)[:3]]
    out += [Finding(rule=prompts.GROUNDING_RULE, severity="block", source=rules.GROUNDING,
                    evidence=f"code check: banned by the brief: {b[:80]!r}")
            for b in brief_v1.banned_hits(text, brief)[:3]]
    return out


def format_check(placement: str, ad: CopyVariant | dict[str, Any],
                 image_png: bytes | None = None) -> list[Finding]:
    """Rule F1, deterministic, before the model reviewers: the platform's own limits for
    this placement (``formats.PLATFORMS``). Hard limits are ``block``, recommended lengths
    a ``tip`` with the truncation point (Meta's own recommendation, ``rules``)."""
    fields = ad.model_dump() if isinstance(ad, CopyVariant) else dict(ad)
    size = None
    if image_png is not None:
        im = Image.open(io.BytesIO(image_png))
        size = im.size
    return [Finding.model_validate({"rule": formats.RULE, "evidence": f.evidence[:500],
                                    "severity": rules.level(formats.RULE, f.severity),
                                    "source": rules.source(formats.RULE, f.evidence)})
            for f in formats.check(placement, fields, size)]


def _pct(x: float) -> str:
    return f"{100 * x:.1f} %"


def sam_findings(placement: str, check: SamCheck | None) -> list[Finding]:
    """Rules S1/S2 from the SAM check of one generated image (``[]`` if none ran: an
    image older than the check, or the video poster)."""
    if check is None:
        return []
    if check.status == "not_recorded":
        return [Finding(rule=SAM_RULE, severity="info",
                        evidence="code check (SAM 3.1): not recorded in this replay set")]
    if check.status == "skipped":
        return [Finding(rule=SAM_RULE, severity="info",
                        evidence="code check (SAM 3.1): skipped, the run budget was reached")]
    if check.status == "failed":
        return [Finding(rule=SAM_RULE, severity="warn",
                        evidence=f"code check (SAM 3.1) failed ({check.error or 'error'}); "
                                 "product fidelity not verified")]
    if check.matches == 0 or check.iou is None:
        return [Finding(rule=SAM_RULE, severity="block",
                        evidence="code check (SAM 3.1): the product was not found in the "
                                 "image")]
    lo, hi = AREA_BOUNDS.get(placement, AREA_DEFAULT)
    flip = ", mirrored" if check.mirrored else ""
    numbers = (f"silhouette IoU {check.iou:.2f} vs the cut-out{flip}, area "
               f"{_pct(check.area)} (bounds {_pct(lo)}-{_pct(hi)})")
    out: list[Finding] = []
    if check.iou < IOU_BLOCK:
        out.append(Finding(rule=SAM_RULE, severity="block",
                           evidence=f"code check (SAM 3.1): the product's outline does not "
                                    f"match the cut-out: {numbers}; below {IOU_BLOCK}"))
    elif check.iou < IOU_WARN:
        out.append(Finding(rule=SAM_RULE, severity="warn",
                           evidence=f"code check (SAM 3.1): the product looks redrawn: "
                                    f"{numbers}; below {IOU_WARN}"))
    if not lo <= check.area <= hi:
        out.append(Finding(rule=SAM_RULE, severity="warn",
                           evidence=f"code check (SAM 3.1): the product is too "
                                    f"{'small' if check.area < lo else 'large'}: {numbers}"))
    if check.edges:
        out.append(Finding(rule=SAM_RULE, severity="warn",
                           evidence="code check (SAM 3.1): the product touches the "
                                    f"{', '.join(check.edges)} edge (cropped?): {numbers}"))
    if not out:
        out.append(Finding(rule=SAM_RULE, severity="info",
                           evidence=f"code check (SAM 3.1): product present: {numbers}"
                                    + (f", {check.matches} matches" if check.matches > 1
                                       else "")))
    if check.people:
        out.append(Finding(rule=PEOPLE_RULE, severity="warn",
                           evidence=f"code check (SAM 3.1): {check.people} person match(es), "
                                    f"{_pct(check.people_area)} of the image; the prompt "
                                    "asked for no people"))
    return out


def with_findings(review: Review, extra: list[Finding],
                  channels: tuple[str, ...] = (rules.META,)) -> Review:
    if not extra:
        return review
    return normalize(Review(verdict=review.verdict, findings=[*review.findings, *extra],
                            summary=review.summary), channels)


def with_policy_net(review: Review, copy: CopyVariant,
                    channels: tuple[str, ...] = (rules.META,)) -> Review:
    """The model's review plus the code's P9 findings (unless the model already cited
    P9); levels and verdict follow the table (``rules``)."""
    net = policy_net(copy, channels)
    if not net or any(f.rule == "P9" for f in review.findings):
        return normalize(review, channels)
    return normalize(Review(verdict=review.verdict, findings=[*review.findings, *net],
                            summary=review.summary), channels)


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


def canonical_rules(review: Review) -> Review:
    """Rule ids mapped onto the checklist (``"P4 Price"`` → ``P4``, unknown →
    ``UNMAPPED``); levels and verdict as the model gave them."""
    valid = set(prompts.policy_rule_ids()) | {formats.RULE, prompts.GROUNDING_RULE,
                                              *SAM_RULES}
    findings = []
    for f in review.findings:
        m = _RULE.search(f.rule)
        rule = m.group() if m and m.group() in valid else "UNMAPPED"
        findings.append(Finding(rule=rule, evidence=f.evidence[:500], severity=f.severity,
                                source=f.source[:300]))
    return Review(verdict=review.verdict, findings=findings, summary=review.summary[:1000])


def normalize(review: Review, channels: tuple[str, ...] = (rules.META,)) -> Review:
    """Code has the final say: rule ids mapped (``canonical_rules``), each level capped by
    the table for the channels the copy runs on and its source set from the table
    (``rules``); the verdict is the worst capped level (tips and notes never raise it).
    A model verdict that names no finding at all is shown as WARN at most (no source)."""
    findings = [Finding(rule=f.rule, evidence=f.evidence,
                        severity=rules.level(f.rule, f.severity, channels),  # type: ignore[arg-type]
                        source=rules.source(f.rule, f.evidence, channels))
                for f in canonical_rules(review).findings]
    verdict = rules.verdict(f.severity for f in findings)
    if not findings and review.verdict != "OK":
        verdict = "WARN"
    return Review(verdict=verdict, findings=findings,  # type: ignore[arg-type]
                  summary=review.summary[:1000])


def _facts_block(brief: brief_v1.BriefV1) -> str:
    facts = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(brief.confirmed_facts)) or "(none)"
    offer = brief.offer
    lines = [f"Advertiser (the brand name the ad must use): {brief.advertiser.display_name}",
             f"Confirmed facts:\n{facts}",
             f"Offer: {offer.product}; {offer.offer or '-'}"
             + (f" (valid until {offer.valid_until})" if offer.valid_until else ""),
             f"Proposition: {brief.proposition}",
             "Disclaimers: " + ("; ".join(brief.mandatories.disclaimers) or "-"),
             "Banned claims: " + ("; ".join(brief.mandatories.banned_claims) or "-")]
    return envelope.wrap("\n".join(lines), "brief_facts", max_chars=6000)


def _sources(brief: brief_v1.BriefV1 | None) -> list[str]:
    return ["ad_copy", "brief_facts"] if brief is not None else ["ad_copy"]


def _messages(image_png: bytes, copy: CopyVariant,
              brief: brief_v1.BriefV1 | None = None) -> list[dict[str, Any]]:
    """The copy goes in an untrusted envelope (it was written by a model from a brief);
    the system prompt says copy and text in the image are data, never instructions. With
    a Brief v1 the owner's confirmed statements and rule P10 follow the copy."""
    text = envelope.wrap(f"Primary text: {copy.primary_text}\nHeadline: {copy.headline}\n"
                         f"Description: {copy.description}\nCTA: {copy.cta}", "ad_copy")
    if brief is not None:
        text += f"\n\n{prompts.grounding()}\n\n{_facts_block(brief)}"
    return [
        {"role": "system", "content": prompts.system(AGENT) + "\n\n" + envelope.SYSTEM_RULE},
        {"role": "user", "content": [{"type": "text", "text": text},
                                     chat_image_part(image_png)]},
    ]


def _parse(text: str, finish: str, channels: tuple[str, ...] = (rules.META,)) -> Review:
    try:
        return normalize(Review.model_validate_json(text), channels)
    except ValueError:
        raise StructuredOutputError(f"reviewer output is not a Review (finish={finish})",
                                    code="invalid_output") from None


def review(client: MuseClient, image_png: bytes, copy: CopyVariant, *, run_id: str,
           brief: brief_v1.BriefV1 | None = None,
           channels: tuple[str, ...] = (rules.META,)) -> Review:
    """Compliance review on Muse Spark 1.3 vision (Standard tier: final creative)."""
    result = client.chat(
        MODEL, _messages(downscale(image_png), copy, brief), run_id=run_id, agent=AGENT,
        response_format=response_format(Review), max_tokens=6000, reasoning_effort="low",
        tier="standard", tier_reason="compliance review of the final creative (vision)",
        envelope_sources=_sources(brief),
    )
    return _parse(result.text, result.finish, channels)


def second_opinion(
    client: openweights.OpenWeightsClient, image_png: bytes, copy: CopyVariant, *, run_id: str,
    brief: brief_v1.BriefV1 | None = None, channels: tuple[str, ...] = (rules.META,),
) -> Review:
    """Same review on open weights (Baseten). ``OpenWeightsUnavailable`` without a key."""
    if not client.available():
        raise openweights.OpenWeightsUnavailable("BASETEN_API_KEY is not set",
                                                 code="unavailable")
    result = client.chat(
        openweights.MODEL, _messages(downscale(image_png), copy, brief), run_id=run_id,
        agent=SECOND_AGENT, response_format=response_format(Review), max_tokens=6000,
        reasoning_effort="low", tier="open",
        tier_reason="open-weights second opinion on the same creative",
        envelope_sources=_sources(brief),
    )
    return _parse(result.text, result.finish, channels)


def reconcile(a: Review, b: Review | None, status: str = "available") -> Reconciled:
    if b is None:
        return Reconciled(a.verdict, a, None, status if status != "available"
                          else "unavailable", [])
    verdict = max(a.verdict, b.verdict, key=lambda v: _RANK[v])
    disagreements = []
    if a.verdict != b.verdict:
        disagreements.append(f"verdict: Muse Spark {a.verdict} vs Spark Open {b.verdict}")
    rules_a = {f.rule for f in a.findings if f.severity not in ("info", "tip")}
    rules_b = {f.rule for f in b.findings if f.severity not in ("info", "tip")}
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
    placement: str | None = None,
    brief: brief_v1.BriefV1 | None = None,
    sam_check: SamCheck | None = None,
    channels: tuple[str, ...] = (rules.META,),
) -> Reconciled:
    """Both reviews, reconciled, plus one ``review`` receipt with the outcome. With a
    ``placement`` the format check (F1) runs first and its findings join both reviews;
    with a confirmed ``brief`` the grounding check (P10) joins them too, and with a
    ``sam_check`` the SAM findings (S1/S2). ``channels``: where the copy runs (``meta``,
    plus ``google`` when the brief chose Google Search), for the levels of ``rules``."""
    sam_found = sam_findings(placement or "", sam_check)
    fmt = (format_check(placement, copy, image_png) if placement else []) + \
        grounding_net(copy, brief) + sam_found
    primary = with_findings(
        with_policy_net(review(client, image_png, copy, run_id=run_id, brief=brief,
                               channels=channels), copy, channels),
        fmt, channels)
    second: Review | None = None
    status = "unavailable"
    if open_client is not None and open_client.available():
        try:
            second = with_findings(with_policy_net(
                second_opinion(open_client, image_png, copy, run_id=run_id, brief=brief,
                               channels=channels),
                copy, channels), fmt, channels)
            status = "available"
        except MuseError:
            status = "failed"  # the failed call has its own receipt; the primary stands
    result = reconcile(primary, second, status)
    client.ledger.append(
        run_id, agent=AGENT, kind="review", input_sha256=[sha256_hex(image_png)],
        output_sha256=sha256_hex(canonical(result.as_dict())),
        details={"verdict": result.verdict, "second_opinion": result.second_status,
                 "disagreements": len(result.disagreements),
                 "rules": sorted({f.rule for f in primary.findings}),
                 "policy_net": sorted({f.rule for f in policy_net(copy)}),
                 "channels": list(channels),
                 "format": [f.severity for f in fmt if f.rule == formats.RULE],
                 **({"sam": {"status": sam_check.status,
                             "findings": [f"{f.rule}:{f.severity}" for f in sam_found]}}
                    if sam_check is not None else {}),
                 **({"grounding": [f.severity for f in fmt
                                   if f.rule == prompts.GROUNDING_RULE]}
                    if brief is not None else {})},
    )
    return result


def run_formats(
    client: MuseClient,
    open_client: openweights.OpenWeightsClient | None,
    images: list[tuple[str, str, bytes]],
    copy: CopyVariant,
    *,
    run_id: str,
    brief: brief_v1.BriefV1 | None = None,
    sam_checks: dict[str, SamCheck | None] | None = None,
    channels: tuple[str, ...] = (rules.META,),
) -> dict[str, Any]:
    """Review each ``(placement, sha256, png)``; the combined verdict is the worst one.
    ``sam_checks`` (by placement) add rules S1/S2.

    ``disagreements`` are prefixed with the placement; ``second_opinion`` is "available"
    only when every format had one ("failed" if any failed)."""
    if not images:
        raise ValueError("nothing to review")
    formats = []
    for placement, digest, png in images:
        result = run(client, open_client, png, copy, run_id=run_id, placement=placement,
                     brief=brief, sam_check=(sam_checks or {}).get(placement),
                     channels=channels)
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
