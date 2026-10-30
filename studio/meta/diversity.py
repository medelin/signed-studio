"""Creative diversity: are the ads in a campaign different enough to reach different people?

Meta's ad delivery since 2025 (the Andromeda retrieval stage) matches each *creative* to
people; targeting stays broad (Advantage+ audience, ``studio.meta.audience``). Ads that
say the same thing with the same picture are treated as one creative: they compete for the
same people instead of finding new ones. So the studio plans *angles* (a reason to buy,
its own hook and its own visual) instead of personas, and checks before signing that the
variants which run are not near-duplicates.

``check(variants, images)``: for every pair of copy variants, the Jaccard similarity of
their word sets (primary text + headline, case- and punctuation-folded) and whether they
share an image. A pair at or above ``SIMILAR`` is a warning naming both variants; the
result is advisory (``ok`` / ``warn``), shown with the review and signed with the payload,
never a block.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

SIMILAR = 0.6
_WORD = re.compile(r"\w+", re.UNICODE)


def words(variant: Mapping[str, Any]) -> frozenset[str]:
    text = f"{variant.get('primary_text', '')} {variant.get('headline', '')}"
    return frozenset(w for w in _WORD.findall(text.casefold()) if len(w) > 2)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def check(variants: Sequence[Mapping[str, Any]],
          images: Sequence[str] | None = None) -> dict[str, Any]:
    """``images[i]`` is variant i's image (a hash or path), when each has its own."""
    sets = [words(v) for v in variants]
    pairs = []
    for i, j in combinations(range(len(variants)), 2):
        score = round(similarity(sets[i], sets[j]), 2)
        same_image = images is not None and len(images) > max(i, j) and images[i] == images[j]
        if score >= SIMILAR:
            pairs.append({"variants": [i + 1, j + 1], "similarity": score,
                          "same_image": same_image})
    distinct_images = len(set(images)) if images else 1
    return {"status": "warn" if pairs else "ok", "variants": len(variants),
            "distinct_images": distinct_images, "near_duplicates": pairs}
