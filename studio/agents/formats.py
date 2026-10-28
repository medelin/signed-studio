"""Format limits from each ad platform's own published specifications (rule ``F1``).

Deterministic: runs before the model reviewers. A platform's HARD limit (the field or the
upload is refused) is a ``block`` finding; a RECOMMENDED length (text is cut off with
"…See more" or the ad performs worse) is a ``warn`` finding that names the truncation
point. Only Meta is wired to publishing; the Google Ads and TikTok rules check copy
variants exported for those platforms. Sources, all retrieved 2026-10-28 (see
docs/policy.md):

- Meta Ads Guide, Facebook Feed image:
  https://www.facebook.com/business/ads-guide/update/image/facebook-feed
- Meta Ads Guide, Instagram Feed image:
  https://www.facebook.com/business/ads-guide/update/image/instagram-feed
- Meta Ads Guide, Instagram Stories image:
  https://www.facebook.com/business/ads-guide/update/image/instagram-story
- Meta Ads Guide, Instagram Reels image:
  https://www.facebook.com/business/ads-guide/update/image/instagram-reels
- Meta Ads Guide, Facebook Feed carousel:
  https://www.facebook.com/business/ads-guide/update/carousel/facebook-feed
- Meta Ads Guide, Instagram Reels video:
  https://www.facebook.com/business/ads-guide/update/video/instagram-reels
- Meta Marketing API, Ad Creative "Fields-Level Limits":
  https://developers.facebook.com/docs/marketing-api/reference/ad-creative
- Google Ads Help, responsive search ads: https://support.google.com/google-ads/answer/7684791
- Google Ads Help, Performance Max asset specs:
  https://support.google.com/google-ads/answer/17091269
- Google Ads Help, Demand Gen asset specs: https://support.google.com/google-ads/answer/17091672
- TikTok Ads Help, Auction In-Feed Ads: https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

RULE = "F1"
RETRIEVED = "2026-10-28"

META_FB_FEED = "https://www.facebook.com/business/ads-guide/update/image/facebook-feed"
META_IG_FEED = "https://www.facebook.com/business/ads-guide/update/image/instagram-feed"
META_IG_STORY = "https://www.facebook.com/business/ads-guide/update/image/instagram-story"
META_IG_REELS = "https://www.facebook.com/business/ads-guide/update/image/instagram-reels"
META_API = "https://developers.facebook.com/docs/marketing-api/reference/ad-creative"
META_CAROUSEL = "https://www.facebook.com/business/ads-guide/update/carousel/facebook-feed"
META_REELS_VIDEO = "https://www.facebook.com/business/ads-guide/update/video/instagram-reels"
GOOGLE_RSA = "https://support.google.com/google-ads/answer/7684791"
GOOGLE_PMAX = "https://support.google.com/google-ads/answer/17091269"
GOOGLE_DGEN = "https://support.google.com/google-ads/answer/17091672"
TIKTOK_IN_FEED = "https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads"


@dataclass(frozen=True)
class TextLimit:
    field: str  # primary_text | headline | description | long_headline | business_name | path
    max_chars: int
    hard: bool  # True: the platform refuses longer text; False: recommended (cut off)
    surface: str
    source: str
    double_width: bool = False  # CJK counts as 2 (Google Ads)


@dataclass(frozen=True)
class ImageSpec:
    surface: str
    ratios: tuple[tuple[int, int], ...]  # allowed width:height ratios
    tolerance: float  # relative
    min_width: int
    min_height: int
    source: str
    hard: bool = True


# placement → text limits and image specs. Meta placements are the studio's own.
PLATFORMS: dict[str, dict[str, Any]] = {
    "feed_1x1": {"platform": "meta", "text": [
        TextLimit("primary_text", 150, False, "Facebook Feed", META_FB_FEED),
        TextLimit("headline", 27, False, "Facebook Feed", META_FB_FEED),
        TextLimit("primary_text", 125, False, "Instagram Feed", META_IG_FEED),
        TextLimit("headline", 40, False, "Instagram Feed", META_IG_FEED),
    ], "image": [
        ImageSpec("Instagram Feed (4:5 to 1.91:1)", ((4, 5), (191, 100)), 0.01, 500, 0,
                  META_IG_FEED),
    ]},
    "feed_4x5": {"platform": "meta", "text": [
        TextLimit("primary_text", 150, False, "Facebook Feed", META_FB_FEED),
        TextLimit("headline", 27, False, "Facebook Feed", META_FB_FEED),
        TextLimit("primary_text", 125, False, "Instagram Feed", META_IG_FEED),
        TextLimit("headline", 40, False, "Instagram Feed", META_IG_FEED),
    ], "image": [
        ImageSpec("Facebook Feed 4:5", ((4, 5),), 0.03, 600, 750, META_FB_FEED),
    ]},
    "story_9x16": {"platform": "meta", "text": [
        TextLimit("primary_text", 125, False, "Instagram Stories", META_IG_STORY),
        TextLimit("primary_text", 44, False, "Instagram Reels", META_IG_REELS),
    ], "image": [
        ImageSpec("Instagram Stories / Reels 9:16", ((9, 16),), 0.01, 500, 0, META_IG_STORY),
    ]},
    # one card of a carousel (placements card_1 … card_10): 2-10 cards, 1:1 ±3 %;
    # "at least 1080 x 1080" is the guide's recommendation (warn), 600 px the hard floor
    # of a Facebook Feed image
    "carousel_card": {"platform": "meta", "text": [
        TextLimit("primary_text", 80, False, "Facebook Feed carousel", META_CAROUSEL),
        TextLimit("headline", 20, False, "Facebook Feed carousel", META_CAROUSEL),
        TextLimit("description", 18, False, "Facebook Feed carousel", META_CAROUSEL),
    ], "image": [
        ImageSpec("Facebook Feed carousel card 1:1", ((1, 1),), 0.03, 600, 600, META_CAROUSEL),
    ], "recommended": (1080, 1080, META_CAROUSEL)},
    # the poster frame of the 9:16 video (placement video_9x16)
    "video_9x16": {"platform": "meta", "text": [
        TextLimit("primary_text", 125, False, "Instagram Stories", META_IG_STORY),
        TextLimit("primary_text", 44, False, "Instagram Reels", META_REELS_VIDEO),
    ], "image": [
        ImageSpec("Instagram Reels video 9:16", ((9, 16),), 0.01, 500, 0, META_REELS_VIDEO),
    ]},
    "google_rsa": {"platform": "google", "text": [
        TextLimit("headline", 30, True, "Google responsive search ad", GOOGLE_RSA, True),
        TextLimit("description", 90, True, "Google responsive search ad", GOOGLE_RSA, True),
        TextLimit("path", 15, True, "Google responsive search ad", GOOGLE_RSA, True),
    ], "image": []},
    "google_pmax": {"platform": "google", "text": [
        TextLimit("headline", 30, True, "Google Performance Max", GOOGLE_PMAX, True),
        TextLimit("long_headline", 90, True, "Google Performance Max", GOOGLE_PMAX, True),
        TextLimit("description", 90, True, "Google Performance Max", GOOGLE_PMAX, True),
        TextLimit("business_name", 25, True, "Google Performance Max", GOOGLE_PMAX, True),
    ], "image": [
        ImageSpec("Performance Max landscape 1.91:1", ((191, 100),), 0.01, 600, 314,
                  GOOGLE_PMAX),
        ImageSpec("Performance Max square 1:1", ((1, 1),), 0.01, 300, 300, GOOGLE_PMAX),
        ImageSpec("Performance Max portrait 4:5", ((4, 5),), 0.01, 480, 600, GOOGLE_PMAX),
    ]},
    "google_demand_gen": {"platform": "google", "text": [
        TextLimit("headline", 40, True, "Google Demand Gen", GOOGLE_DGEN),
        TextLimit("description", 90, True, "Google Demand Gen", GOOGLE_DGEN),
        TextLimit("business_name", 25, True, "Google Demand Gen", GOOGLE_DGEN),
    ], "image": [
        ImageSpec("Demand Gen landscape 1.91:1", ((191, 100),), 0.01, 600, 314, GOOGLE_DGEN),
        ImageSpec("Demand Gen square 1:1", ((1, 1),), 0.01, 300, 300, GOOGLE_DGEN),
        ImageSpec("Demand Gen portrait 4:5", ((4, 5),), 0.01, 480, 600, GOOGLE_DGEN),
    ]},
    "tiktok_in_feed": {"platform": "tiktok", "text": [], "image": []},
}
EXPORT_ONLY = frozenset(p for p, v in PLATFORMS.items() if v["platform"] != "meta")
CAROUSEL_CARDS = (2, 10)  # Meta: "2 to 10" cards (META_CAROUSEL)
VIDEO_MAX_S = 15 * 60  # Reels: "0 seconds to 15 minutes" (META_REELS_VIDEO)
VIDEO_MAX_BYTES = 4 * 1024**3  # "Maximum file size: 4 GB"


def surface(placement: str) -> str:
    """The ``PLATFORMS`` key of a placement: ``card_<n>`` is one carousel card."""
    if placement.startswith("card_") and placement[5:].isdigit():
        return "carousel_card"
    return placement


def check_video(seconds: float, size_bytes: int) -> list[FormatFinding]:
    """Hard video limits of the Reels / Stories placements (duration, file size)."""
    out = []
    if not 0 < seconds <= VIDEO_MAX_S:
        out.append(FormatFinding("block", f"Meta video: {seconds:.1f} s, allowed up to "
                                          f"{VIDEO_MAX_S} s ({META_REELS_VIDEO}, retrieved "
                                          f"{RETRIEVED})"))
    if size_bytes > VIDEO_MAX_BYTES:
        out.append(FormatFinding("block", f"Meta video: {size_bytes} bytes, maximum 4 GB "
                                          f"({META_REELS_VIDEO}, retrieved {RETRIEVED})"))
    return out


def check_carousel(cards: int) -> list[FormatFinding]:
    lo, hi = CAROUSEL_CARDS
    if lo <= cards <= hi:
        return []
    return [FormatFinding("block", f"Meta carousel: {cards} cards, allowed {lo} to {hi} "
                                   f"({META_CAROUSEL}, retrieved {RETRIEVED})")]
META_URL_MAX = 1000  # Marketing API: "Maximum length of a URL: 1000" (META_API)
TIKTOK_NAME_MAX = 20  # "maximum of 10 characters (CJK) or 20 characters in other languages"
TIKTOK_NAME_MAX_CJK = 10


def _is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def length(text: str, double_width: bool = False) -> int:
    """Characters as the platform counts them (NFC; CJK twice for Google Ads)."""
    text = unicodedata.normalize("NFC", text)
    return sum(2 if double_width and _is_wide(c) else 1 for c in text)


def _cut(text: str, n: int) -> str:
    return unicodedata.normalize("NFC", text)[:n]


@dataclass(frozen=True)
class FormatFinding:
    severity: str  # block | warn
    evidence: str

    def finding(self) -> dict[str, str]:
        return {"rule": RULE, "evidence": self.evidence, "severity": self.severity}


def check(placement: str, ad: dict[str, Any],
          image_size: tuple[int, int] | None = None) -> list[FormatFinding]:
    """Findings for one ad on one placement. ``ad`` holds text fields (``primary_text``,
    ``headline``, ``description``, optional ``long_headline``, ``business_name``,
    ``path``, ``display_name``, ``link``)."""
    spec = PLATFORMS.get(surface(placement))
    if spec is None:
        raise ValueError(f"unknown placement {placement!r}")
    out: list[FormatFinding] = []
    for lim in spec["text"]:
        value = ad.get(lim.field)
        if not isinstance(value, str) or not value:
            continue
        n = length(value, lim.double_width)
        if n <= lim.max_chars:
            continue
        kind = "limit" if lim.hard else "recommended length"
        cut = _cut(value, lim.max_chars)
        out.append(FormatFinding(
            "block" if lim.hard else "warn",
            f"{lim.surface}: {lim.field} is {n} characters, {kind} {lim.max_chars}"
            + ("" if lim.hard else f" — shown as {cut!r}…") + f" ({lim.source}, "
            f"retrieved {RETRIEVED})"))
    link = ad.get("link")
    if spec["platform"] == "meta" and isinstance(link, str) and len(link) > META_URL_MAX:
        out.append(FormatFinding("block", f"Meta: link is {len(link)} characters, maximum "
                                          f"{META_URL_MAX} ({META_API}, retrieved {RETRIEVED})"))
    if placement == "tiktok_in_feed":
        out += _tiktok(ad)
    if image_size is not None and spec["image"]:
        out += _image(spec["image"], image_size)
        rec = spec.get("recommended")
        if rec and not out and (image_size[0] < rec[0] or image_size[1] < rec[1]):
            out.append(FormatFinding("warn", f"image {image_size[0]}×{image_size[1]} is "
                                             f"below the recommended {rec[0]}×{rec[1]} "
                                             f"({rec[2]}, retrieved {RETRIEVED})"))
    return out


def _tiktok(ad: dict[str, Any]) -> list[FormatFinding]:
    out = []
    name = ad.get("display_name")
    if isinstance(name, str) and name:
        cjk = any(_is_wide(c) for c in name)
        limit = TIKTOK_NAME_MAX_CJK if cjk else TIKTOK_NAME_MAX
        if len(name) > limit:
            out.append(FormatFinding("block", f"TikTok: display name is {len(name)} "
                                              f"characters, maximum {limit} ({TIKTOK_IN_FEED}, "
                                              f"retrieved {RETRIEVED})"))
    text = " ".join(str(ad.get(k) or "") for k in ("primary_text", "headline", "description"))
    if re.search(r"https?://|www\.|[@#]\w", text):
        out.append(FormatFinding("warn", "TikTok non-Spark ad text does not support links, "
                                         "@ or hashtags; they show as plain text "
                                         f"({TIKTOK_IN_FEED}, retrieved {RETRIEVED})"))
    return out


def _image(specs: list[ImageSpec], size: tuple[int, int]) -> list[FormatFinding]:
    """The image must fit one of the placement's specs (ratio within tolerance, or inside a
    ratio range when two ratios are given; minimum size)."""
    w, h = size
    problems = []
    for s in specs:
        r = w / h
        ratios = [a / b for a, b in s.ratios]
        if len(ratios) == 2:
            lo, hi = min(ratios), max(ratios)
            ratio_ok = lo * (1 - s.tolerance) <= r <= hi * (1 + s.tolerance)
        else:
            ratio_ok = abs(r - ratios[0]) <= ratios[0] * s.tolerance
        size_ok = w >= s.min_width and h >= s.min_height
        if ratio_ok and size_ok:
            return []
        want = " to ".join(f"{a}:{b}" for a, b in s.ratios)
        problems.append(f"{s.surface} needs {want}"
                        + (f" (±{s.tolerance:.0%})" if len(ratios) == 1 else "")
                        + f", at least {s.min_width}×{s.min_height} px ({s.source})")
    return [FormatFinding("block", f"image {w}×{h} fits no spec of this placement: "
                                   + "; ".join(problems) + f" (retrieved {RETRIEVED})")]
