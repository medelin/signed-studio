"""OKLCH → WCAG relative luminance and contrast, and a reader for tokens.css palettes."""

import math
import re
from pathlib import Path

TOKENS = Path(__file__).resolve().parents[1] / "studio" / "ui" / "static" / "tokens.css"
OKLCH = re.compile(r"oklch\(([\d.]+) ([\d.]+) ([\d.]+)\)")


def luminance(L: float, C: float, h: float) -> float:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    r, g, bl = (min(max(v, 0.0), 1.0) for v in (r, g, bl))
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl


def ratio(c1: tuple[float, float, float], c2: tuple[float, float, float]) -> float:
    y1, y2 = luminance(*c1), luminance(*c2)
    return (max(y1, y2) + 0.05) / (min(y1, y2) + 0.05)


def _block(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start:css.index("\n}", start)]


def _values(block: str) -> dict[str, list[tuple[float, float, float]]]:
    out = {}
    for name, value in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block):
        colors = [tuple(map(float, m)) for m in OKLCH.findall(value)]
        if colors:
            out[name] = colors * (2 if len(colors) == 1 else 1)  # one value = both modes
    return out


def palette(name: str, css: str | None = None) -> dict[str, dict[str, tuple[float, float, float]]]:
    """``{"light": {token: oklch}, "dark": {...}}`` for a palette (base + its overrides)."""
    css = css if css is not None else TOKENS.read_text()
    tokens = _values(_block(css, ":root"))
    if name != "graphite":
        tokens.update(_values(_block(css, f':root[data-palette="{name}"]')))
    return {mode: {k: v[i] for k, v in tokens.items()} for i, mode in enumerate(("light",
                                                                                 "dark"))}
