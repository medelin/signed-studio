"""The "two views" cast: one-person business owners drawn as server-rendered inline SVG.

Two views of one ad:

- the **owner**: a friendly monoline person (Catmull-Rom curves through one set of
  silhouette points), skin from one of five ``--skin-N`` tokens, hair, face, one warm accent
  (the prop of their trade); people stand side by side and never overlap anything;
- the **record**: the ad itself on a dashed card (image block, headline, text lines);

joined by a dotted **trace** whose diamond nodes are the agents (producer, copywriter, art
director, reviewers) and whose last node is the owner's signature. The record is correct
only after the signature; an unsigned record shows the ``!`` field.

Rules (tested in ``tests/test_cast.py``): colours only through classes from
``static/illustrations.css`` (tokens), no ``style`` attribute, ``<style>``, ``id``,
``<defs>``, gradients, filters or images; no words in drawings (digits and signs only);
at most 6 KB per person drawn; informative = ``role="img"`` + label, decorative =
``aria-hidden``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from html import escape

from markupsafe import Markup

Pt = tuple[float, float]

MAX_BYTES = 6 * 1024
AGENTS = ("producer", "copywriter", "art director", "reviewers")


def _n(v: float) -> str:
    """Compact number: integers without a point, others with one decimal."""
    r = round(v, 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def _pts(points: Sequence[Pt]) -> str:
    return " ".join(f"{_n(x)} {_n(y)}" for x, y in points)


def curve(points: Sequence[Pt], closed: bool = False, t: float = 1.0) -> str:
    """Smooth path through ``points`` (Catmull-Rom as cubic Béziers)."""
    p = [*points, *points[:3]] if closed else [points[0], *points, points[-1]]
    out = [f"M{_n(p[1][0])} {_n(p[1][1])}"]
    n = len(points) if closed else len(points) - 1
    for i in range(1, n + 1):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) * t / 6, p1[1] + (p2[1] - p0[1]) * t / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) * t / 6, p2[1] - (p3[1] - p1[1]) * t / 6)
        out.append(f"C{_pts([c1, c2, p2])}")
    return "".join(out) + ("Z" if closed else "")


def mirror(points: Sequence[Pt], cx: float = 60) -> list[Pt]:
    return [(2 * cx - x, y) for x, y in points]


# -- people -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Person:
    """One cast member. Geometry is in a 120 × 140 bust frame, head centred on x = 60."""

    key: str
    name: str
    age: int
    pronoun: str  # she / he
    business: str
    business_pl: str
    skin: int  # 1..5 -> --skin-N
    hair: str  # afro, locs, braid, straight, curly, bun, bald, hijab, turban, wrap, short
    hair_tone: str = "dark"  # dark, mid, grey, light
    prop: str = ""
    face_w: float = 21  # half-width at the cheek
    jaw_w: float = 14  # half-width at the jaw corner
    chin: float = 78  # y of the neck top (lower face length)
    shoulders: float = 50  # half-width at the bottom of the frame
    nose: str = "small"  # small, broad, long, button
    lips: str = "line"  # line, full
    brows: str = "thin"  # thin, thick, grey
    beard: str = ""  # "", full, long, short, stubble, moustache
    glasses: bool = False
    hearing_aid: bool = False
    wrinkles: int = 0  # 0 none, 1 laugh lines, 2 + crow's feet, 3 + forehead
    freckles: bool = False
    earring: bool = False
    wheelchair: bool = False
    features: tuple[str, ...] = field(default=())

    def silhouette(self) -> list[Pt]:
        """Shoulders, neck and head as one open line, left bottom to right bottom."""
        fw, jw, nw, sh = self.face_w, self.jaw_w, 8.5, self.shoulders
        left: list[Pt] = [
            (60 - sh, 140), (60 - sh + 8, 112), (60 - nw - 10, 99), (60 - nw, 93),
            (60 - nw + 0.5, self.chin), (60 - jw, self.chin - 7), (60 - fw, 54),
            (60 - fw + 0.5, 36), (60 - fw * 0.55, 21), (60, 17),
        ]
        return left + mirror(left[:-1])[::-1]

    def head(self) -> list[Pt]:
        """Closed skin area: neck base, neck, face, crown (points of the silhouette)."""
        return self.silhouette()[3:-3]

    def describe(self, lang: str = "en") -> str:
        biz = self.business_pl if lang == "pl" else self.business
        return f"{self.name}, {biz}"


CAST: tuple[Person, ...] = (
    Person("amara", "Amara", 34, "she", "bike repair", "naprawa rowerów", 5, "afro",
           prop="wrench", face_w=21, jaw_w=14, nose="broad", lips="full", earring=True),
    Person("tomasz", "Tomasz", 61, "he", "bakery", "piekarnia", 1, "bald", "grey",
           prop="loaf", face_w=22, jaw_w=17, chin=79, shoulders=54, nose="long",
           brows="grey", beard="full", wrinkles=3),
    Person("leila", "Leila", 27, "she", "florist", "kwiaciarnia", 3, "hijab",
           prop="tulip", face_w=19, jaw_w=12, shoulders=47, nose="small"),
    Person("harjit", "Harjit", 68, "he", "tailor", "krawiectwo", 4, "turban", "grey",
           prop="spool", face_w=21, jaw_w=16, shoulders=52, nose="long", brows="grey",
           beard="long", glasses=True, wrinkles=2),
    Person("mei", "Mei", 74, "she", "ceramics", "ceramika", 2, "bun", "grey",
           prop="mug", face_w=20, jaw_w=14, chin=76, shoulders=46, nose="button",
           brows="grey", hearing_aid=True, wrinkles=3),
    Person("diego", "Diego", 23, "he", "barber", "fryzjer męski", 3, "curly",
           prop="comb", face_w=20, jaw_w=15, shoulders=51, nose="long", beard="stubble"),
    Person("kofi", "Kofi", 42, "he", "maths tutoring", "korepetycje z matematyki", 4,
           "locs", prop="pencil", face_w=21, jaw_w=16, shoulders=53, nose="broad",
           lips="full", beard="short", wrinkles=1),
    Person("grace", "Grace", 56, "she", "food truck", "food truck", 5, "wrap",
           prop="bowl", face_w=21, jaw_w=15, shoulders=50, nose="broad", lips="full",
           wrinkles=1, glasses=True),
    Person("priya", "Priya", 46, "she", "photography", "fotografia", 3, "braid",
           prop="camera", face_w=20, jaw_w=13, shoulders=47, nose="long", earring=True,
           wheelchair=True),
    Person("ola", "Ola", 29, "she", "yarn shop", "pasmanteria", 1, "straight", "light",
           prop="yarn", face_w=19, jaw_w=13, chin=77, shoulders=46, nose="button",
           freckles=True),
)
BY_KEY = {p.key: p for p in CAST}


# -- the owner (bust) -------------------------------------------------------------------


def _hair_back(p: Person) -> list[str]:
    """Hair behind the head and shoulders."""
    fw = p.face_w
    h = f"hair-{p.hair_tone}"
    if p.hair == "afro":
        pts = []
        for i in range(15):
            a = math.pi * (0.92 + i * 1.16 / 14)  # from lower left round the top to lower right
            r = 35 if i % 2 else 32
            pts.append((60 + r * 1.05 * math.cos(a), 44 + r * 0.92 * math.sin(a)))
        pts += [(60 + fw + 4, 66), (60 - fw - 4, 66)]
        return [f'<path class="{h}" d="{curve(pts, closed=True, t=1.15)}"/>']
    if p.hair == "straight":
        left = [(60 - fw + 1, 30), (60 - fw - 5, 52), (60 - fw - 6, 86), (60 - fw - 3, 104),
                (60 - fw + 6, 104), (60 - fw + 4, 60)]
        return [f'<path class="{h} hair-line" d="{curve(left, closed=True)}"/>',
                f'<path class="{h} hair-line" d="{curve(mirror(left), closed=True)}"/>']
    if p.hair == "locs":
        out = []
        for x0, x1, y1 in ((38, 33, 104), (43, 39, 110), (78, 82, 110), (83, 88, 104),
                           (48, 46, 96), (73, 75, 96)):
            d = f"M{x0} 40Q{(x0 + x1) / 2 - 3:.0f} {(40 + y1) / 2:.0f} {x1} {y1}"
            out += [f'<path class="loc-edge" d="{d}"/>', f'<path class="loc {h}" d="{d}"/>']
        return out
    if p.hair == "bun":
        return [f'<circle class="{h}" cx="60" cy="13" r="9"/>',
                '<path class="c-face" d="M53 12q7 -5 14 0M54 16q6 -3 12 0"/>']
    if p.hair == "wrap":
        return []
    return []


def _hair_front(p: Person) -> list[str]:
    """Hair or head covering over the crown, with its hairline."""
    fw = p.face_w
    h = f"hair-{p.hair_tone}"
    L, R = 60 - fw, 60 + fw
    if p.hair == "afro":
        line: list[Pt] = [(L - 1, 46), (L + 1, 31), (60, 26), (R - 1, 31), (R + 1, 46)]
        cap = [*line, (R + 4, 30), (60, 12), (L - 4, 30)]
        return [f'<path class="{h}" d="{curve(cap, closed=True)}"/>',
                f'<path class="c-line" d="{curve(line)}"/>',
                '<path class="c-texture" d="M44 20q3 -3 6 0M66 18q3 -3 6 0M55 13q3 -3 6 0'
                'M33 34q3 -3 6 0M82 34q3 -3 6 0M30 52q3 -3 6 0M85 52q3 -3 6 0"/>']
    if p.hair == "curly":
        top = [(60 + 25 * math.cos(math.pi * (1 + i / 10)) * (1 if i % 2 else 1.1),
                31 + 19 * math.sin(math.pi * (1 + i / 10)) * (1 if i % 2 else 1.12))
               for i in range(11)]
        line = [(R + 1, 42), (R - 3, 33), (66, 30), (56, 31), (46, 29), (L + 3, 34),
                          (L - 1, 42)]
        return [f'<path class="{h} hair-line" d="{curve(top + line, closed=True, t=1.2)}"/>',
                '<path class="c-texture" d="M47 21a3 3 0 1 1 5 2M57 17a3 3 0 1 1 5 2'
                'M68 19a3 3 0 1 1 5 2M43 30a3 3 0 1 1 5 1M73 27a3 3 0 1 1 5 2"/>']
    if p.hair == "straight":
        cap = [(L - 1, 50), (L, 30), (48, 18), (62, 15), (74, 18), (R, 30), (R + 1, 50),
               (R - 3, 38), (70, 28), (54, 27), (44, 32), (L + 3, 40)]
        return [f'<path class="{h} hair-line" d="{curve(cap, closed=True)}"/>',
                '<path class="c-texture" d="M56 17q-6 6 -10 14M68 19q4 6 6 13"/>']
    if p.hair == "locs":
        cap = [(L - 1, 44), (L, 28), (50, 17), (60, 15), (70, 17), (R, 28), (R + 1, 44),
               (R - 3, 34), (60, 28), (L + 3, 34)]
        return [f'<path class="{h} hair-line" d="{curve(cap, closed=True)}"/>',
                '<path class="c-texture" d="M50 18v10M60 16v11M70 18v10M44 23v9M76 23v9"/>']
    if p.hair == "braid":
        cap = [(L - 1, 46), (L, 28), (50, 17), (60, 15), (70, 17), (R, 28), (R + 1, 46),
               (R - 3, 34), (66, 24), (60, 20), (54, 24), (L + 3, 34)]
        braid = [f'<path class="{h} hair-line" d="{curve(cap, closed=True)}"/>',
                 '<path class="c-texture" d="M60 16v6M52 21q-6 4 -8 13M68 21q6 4 8 13"/>']
        y = 58
        for i in range(7):  # the braid falls from behind the ear over the right shoulder
            x = R + 2 + i * 0.9
            braid.append(f'<ellipse class="{h} hair-line" cx="{_n(x)}" cy="{y + i * 7}" '
                         f'rx="4" ry="5"/>')
        braid.append(f'<path class="c-line" d="M{_n(R + 4)} {y + 46}l5 2"/>'
                     f'<path class="c-texture" d="M{_n(R + 6)} {y + 50}q-2 6 1 9M{_n(R + 8)} '
                     f'{y + 50}q2 5 0 9"/>')
        return braid
    if p.hair == "bun":
        cap = [(L - 1, 44), (L, 28), (50, 18), (60, 16), (70, 18), (R, 28), (R + 1, 44),
               (R - 2, 32), (60, 24), (L + 2, 32)]
        return [f'<path class="{h} hair-line" d="{curve(cap, closed=True)}"/>',
                '<path class="c-texture" d="M46 30q6 -9 14 -12M74 30q-6 -9 -14 -12M53 26q3 -6 7 -8'
                'M67 26q-3 -6 -7 -8"/>']
    if p.hair == "bald":
        side = [(L - 0.5, 34), (L - 1, 46), (L + 0.5, 58), (L + 4, 52), (L + 3.5, 40)]
        return [f'<path class="{h} hair-line" d="{curve(side, closed=True)}"/>',
                f'<path class="{h} hair-line" d="{curve(mirror(side), closed=True)}"/>',
                '<path class="c-shine" d="M50 24q6 -4 12 -3"/>']
    if p.hair == "hijab":
        outer = [(60, 10), (R + 9, 26), (R + 10, 60), (R + 8, 88), (R + 22, 104), (60, 112),
                 (L - 22, 104), (L - 8, 88), (L - 10, 60), (L - 9, 26)]
        face = [(60, 24), (R - 3, 36), (R - 1, 58), (R - 6, 74), (60, 82), (L + 6, 74),
                (L + 1, 58), (L + 3, 36)]
        d = curve(outer, closed=True) + curve(face, closed=True)
        return [f'<path class="cloth" fill-rule="evenodd" d="{d}"/>',
                f'<path class="c-texture" d="M{R + 4} 60q4 20 -6 34M{L + 14} 96q12 8 30 6"/>']
    if p.hair == "turban":
        t = [(L - 1, 44), (L - 3, 26), (46, 9), (60, 4), (74, 9), (R + 3, 26), (R + 1, 44),
             (R - 2, 34), (60, 30), (L + 2, 34)]
        return [f'<path class="cloth hair-line" d="{curve(t, closed=True)}"/>',
                '<path class="c-texture" d="M60 30l-12 -20M60 30l12 -20M42 34q6 -14 16 -24'
                'M78 34q-6 -14 -16 -24"/>']
    if p.hair == "wrap":
        w = [(L - 2, 44), (L - 5, 22), (46, 2), (62, -6), (80, 2), (R + 5, 22), (R + 2, 44),
             (R - 2, 32), (60, 28), (L + 2, 32)]
        return [f'<path class="cloth hair-line" d="{curve(w, closed=True)}"/>',
                '<path class="c-texture" d="M42 30q12 -22 34 -26M48 32q14 -16 32 -18'
                'M56 -2q8 6 4 16"/>']
    return []


def _ears(p: Person) -> list[str]:
    if p.hair in ("hijab",):
        return []
    fw = p.face_w
    ear = (f"M{_n(60 - fw + 0.5)} 47c-6 -3 -8 4 -6 8c1 3 3 5 6 5"
           f"M{_n(60 + fw - 0.5)} 47c6 -3 8 4 6 8c-1 3 -3 5 -6 5")
    out = [f'<path class="skin-{p.skin} c-line" d="{ear}"/>']
    if p.hearing_aid:
        out.append(f'<path class="device" d="M{_n(60 + fw + 4)} 42q5 2 3 10l-2 3"/>'
                   f'<circle class="device" cx="{_n(60 + fw + 3)}" cy="56" r="1.6"/>')
    if p.earring:
        out.append(f'<circle class="c-line" cx="{_n(60 - fw - 3)}" cy="62" r="2"/>'
                   f'<circle class="c-line" cx="{_n(60 + fw + 3)}" cy="62" r="2"/>')
    return out


def _face(p: Person) -> list[str]:
    d = []
    # brows
    brow = "M47 41q5 -3 9 -1M64 40q4 -2 9 1"
    bc = {"thick": "brow-thick", "grey": "brow-grey"}.get(p.brows, "c-face")
    out = [f'<path class="{bc}" d="{brow}"/>']
    # eyes: the same friendly closed smile for everybody
    d.append("M48 49q3.5 -3 7 0M65 49q3.5 -3 7 0")
    # nose
    d.append({
        "small": "M60 52c-2 5 -3 8 1 9",
        "broad": "M58 51v5M56 59q-3 3 1 4q3 1 6 0q4 -1 1 -4",
        "long": "M58 45c1 6 0 10 -3 14c1 3 5 3 7 1",
        "button": "M57 59q3 3 6 0",
    }[p.nose])
    # mouth
    d.append("M53 67q7 6 14 0")
    if p.lips == "full":
        d.append("M54 67q3 -2 6 -1q3 -1 6 1M55 69q5 5 10 0")
    if p.wrinkles >= 1:
        d.append("M53 61q-4 4 -2 9M67 61q4 4 2 9")
    if p.wrinkles >= 2:
        d.append("M44 47l-3 -1M44 50l-3 1M76 47l3 -1M76 50l3 1")
    if p.wrinkles >= 3:
        d.append("M50 33q10 -3 20 0")
    out.append(f'<path class="c-face" d="{"".join(d)}"/>')
    if p.freckles:
        out.append('<path class="c-dots" d="M49 56h.1M52 58h.1M47 59h.1M68 58h.1M71 56h.1'
                   'M73 59h.1"/>')
    return out


def _beard(p: Person) -> list[str]:
    h = f"hair-{p.hair_tone}"
    jw, fw, c = p.jaw_w, p.face_w, p.chin
    if p.beard in ("full", "long"):
        low = c + (18 if p.beard == "long" else 6)
        pts = [(60 - fw + 1, 56), (60 - jw - 1, c - 6), (60 - 8, low), (60, low + 2),
               (60 + 8, low), (60 + jw + 1, c - 6), (60 + fw - 1, 56), (60 + fw - 5, 62),
               (67, 63), (60, 62), (53, 63), (60 - fw + 5, 62)]
        out = [f'<path class="{h} hair-line" d="{curve(pts, closed=True)}"/>']
        if p.beard == "long":
            out.append(f'<path class="c-texture" d="M56 {c}q-1 8 2 14M64 {c}q1 8 -2 14"/>')
        out.append('<path class="skin-lip" d="M53 67q7 6 14 0q-7 2 -14 0"/>')
        return out
    if p.beard == "short":
        pts = [(60 - fw + 2, 58), (60 - jw, c - 5), (60, c + 1), (60 + jw, c - 5),
               (60 + fw - 2, 58), (66, 66), (60, 64), (54, 66)]
        return [f'<path class="{h} hair-line" d="{curve(pts, closed=True)}"/>',
                '<path class="skin-lip" d="M53 67q7 6 14 0q-7 2 -14 0"/>']
    if p.beard == "stubble":
        return ['<path class="c-dots" d="M47 66h.1M50 70h.1M54 73h.1M60 74h.1M66 73h.1'
                'M70 70h.1M73 66h.1M57 64h.1M63 64h.1"/>']
    return []


def _glasses(p: Person) -> list[str]:
    if not p.glasses:
        return []
    return ['<path class="c-line glass" d="M45 48a6.5 6 0 1 0 13 0a6.5 6 0 1 0 -13 0'
            'M62 48a6.5 6 0 1 0 13 0a6.5 6 0 1 0 -13 0M58 47q2 -2 4 0'
            f'M45 47l{_n(-p.face_w + 15.5)} -2M75 47l{_n(p.face_w - 15.5)} -2"/>']


PROPS = {
    # one warm accent each, held in front of the chest (lower right of the bust frame)
    "wrench": 'M82 134l3 3 17 -19-3 -3zM99 115a8 8 0 0 1 5 -12l-1 6 4 3 5 -3a8 8 0 0 1 -13 6',
    "loaf": 'M78 128c0 -12 30 -14 32 -2c1 6 -6 9 -16 9s-16 -1 -16 -7zM86 122l3 4M94 120l3 4'
            'M102 120l2 4',
    "tulip": 'M94 138V116M94 128q-6 -2 -9 -8M88 104q1 10 6 12q5 -2 6 -12l-3 3-3 -5-3 5z',
    "spool": 'M84 112h20M84 134h20M87 112v22M101 112v22M87 118l14 3M87 124l14 3M87 130l14 3'
             'M108 104l-6 22',
    "mug": 'M82 114h20v18q0 4 -4 4h-12q-4 0 -4 -4zM102 118q8 0 8 6t-8 6M88 108q-2 -4 1 -6'
           'M95 108q-2 -4 1 -6',
    "comb": 'M80 118h28v6h-28zM83 124v8M87 124v8M91 124v8M95 124v8M99 124v8M103 124v8',
    "pencil": 'M82 136l22 -26l5 4l-22 26l-7 2zM101 113l5 4',
    "bowl": 'M78 120h32q-2 16 -16 16q-14 0 -16 -16zM88 114q-3 -4 0 -8q3 -4 0 -8'
            'M98 114q-3 -4 0 -8q3 -4 0 -8',
    "camera": 'M78 116h30v18h-30zM86 116l3 -5h8l3 5M93 125m-5 0a5 5 0 1 0 10 0a5 5 0 1 0 -10 0'
              'M103 120h2',
    "yarn": 'M94 124m-12 0a12 12 0 1 0 24 0a12 12 0 1 0 -24 0M84 118q10 4 20 -2M83 126q12 4 22 -4'
            'M88 135q8 -10 16 -20M106 128q4 6 8 8',
}


Box = tuple[float, float, float, float]  # x0, y0, x1, y1


def person_box(p: Person) -> Box:
    """Bounding box of a drawn person in its own frame (bust 120 × 140, seated 160 × 240):
    shoulders, hair or head covering, and the prop. Scenes place people so these never
    intersect each other or anything else."""
    if p.wheelchair:
        return (4.0, -8.0, 152.0, 238.0)
    reach = p.face_w + 24  # hijab drape, afro, braid
    return (min(60 - p.shoulders, 60 - reach), -8.0, max(60 + p.shoulders, 60 + reach, 116),
            140.0)


def place(box: Box, x: float, y: float, s: float) -> Box:
    return (x + box[0] * s, y + box[1] * s, x + box[2] * s, y + box[3] * s)


def overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def bust(p: Person, x: float = 0, y: float = 0, s: float = 1.0, accent: bool = True) -> str:
    """The owner as a warm continuous line, 120 × 140 at scale 1 (no background shape)."""
    sil = p.silhouette()
    parts = [f'<g transform="translate({_n(x)} {_n(y)}) scale({s})">']
    cloth = curve(sil[:4]) + curve(sil[-4:]).replace("M", "L", 1) + "Z"
    parts.append(f'<path class="c-cloth" d="{cloth}"/>')
    parts += _hair_back(p)
    parts.append(f'<path class="skin-{p.skin}" d="{curve(p.head(), closed=False)}Z"/>')
    parts += _ears(p)
    parts.append(f'<path class="c" d="{curve(sil)}"/>')
    parts += _face(p)
    parts += _beard(p)
    parts += _hair_front(p)
    parts += _glasses(p)
    if accent and p.prop:
        parts.append(f'<path class="c-accent" d="{PROPS[p.prop]}"/>')
    parts.append("</g>")
    return "".join(parts)


def seated(p: Person, x: float = 0, y: float = 0, s: float = 1.0, accent: bool = True) -> str:
    """Full-figure variant: the owner in a wheelchair, 160 × 240 at scale 1."""
    legs = ('<path class="c-cloth" d="M21 138V164q0 6 6 6H112V206H138q5 0 5 -5t-7 -5H128V164'
            'q0 -12 -12 -12H115V138z"/>'
            '<path class="c" d="M21 140V164q0 6 6 6H112V206H138q5 0 5 -5t-7 -5H128V164'
            'q0 -12 -12 -12H115V140M62 152q-6 0 -10 4"/>')
    chair = ('<path class="c" d="M18 92H6M18 92V172H114M106 172L114 218H140"/>'
             '<circle class="c" cx="52" cy="194" r="34"/>'
             '<circle class="c-line" cx="52" cy="194" r="28"/>'
             '<path class="c-line" d="M52 166V222M24 194H80M52 194m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 '
             '-6 0"/><path class="c" d="M114 218v4"/><circle class="c" cx="114" cy="229" '
             'r="7"/>')
    return (f'<g transform="translate({_n(x)} {_n(y)}) scale({s})">'
            f'{legs}{chair}{bust(p, 8, 0, 1, accent)}</g>')


def figure(p: Person, x: float, y: float, s: float) -> tuple[str, Box]:
    """A person drawn at (x, y, s) and the box they occupy in the scene."""
    draw = seated(p, x, y, s) if p.wheelchair else bust(p, x, y, s)
    return draw, place(person_box(p), x, y, s)


# -- the record, the trace and scenes ---------------------------------------------------


def record(x: float, y: float, w: float = 180, h: float = 150, *, signed: bool = True,
           mark: str = "", score: int | None = None) -> str:
    """The ad record: dashed card with the ad's image block, headline and text lines, the
    signature field (or the ``!`` field while unsigned), optional score and corner mark."""
    out = [f'<g class="rec" transform="translate({_n(x)} {_n(y)})">',
           f'<rect class="r-card" width="{_n(w)}" height="{_n(h)}"/>',
           # the ad image: a picture block with a sun and a hill
           '<rect class="r-field" x="10" y="12" width="64" height="56"/>'
           '<path class="r-pic" d="M14 62l16 -18 12 12 8 -8 20 14M58 26m-5 0a5 5 0 1 0 10 0'
           'a5 5 0 1 0 -10 0"/>',
           # headline and text lines
           f'<rect class="r-value" x="84" y="14" width="{_n(w - 98)}" height="9"/>',
           f'<rect class="r-label" x="84" y="32" width="{_n((w - 98) * .9)}" height="5"/>'
           f'<rect class="r-label" x="84" y="43" width="{_n((w - 98) * .75)}" height="5"/>'
           f'<rect class="r-label" x="84" y="54" width="{_n((w - 98) * .5)}" height="5"/>',
           f'<rect class="r-label" x="10" y="80" width="{_n(w - 20)}" height="5"/>'
           f'<rect class="r-label" x="10" y="91" width="{_n((w - 20) * .7)}" height="5"/>']
    sy = h - 32
    if signed:  # the signature field, filled: the record is now correct
        out.append(f'<rect class="r-signed" x="10" y="{_n(sy)}" width="{_n(w - 56)}" '
                   f'height="18"/><path class="r-sig" d="M16 {_n(sy + 12)}c6 -10 10 6 16 -2'
                   f's6 -6 10 2 12 -4"/>')
    else:  # unsigned: the ! field
        out.append(f'<rect class="warn-field" x="10" y="{_n(sy)}" width="{_n(w - 56)}" '
                   f'height="18"/><path class="warn-mark" d="M{_n(w / 2 - 18)} {_n(sy + 4)}'
                   f'v6M{_n(w / 2 - 18)} {_n(sy + 14)}v.5"/>')
    if score is not None:
        out.append(f'<text class="r-text" x="{_n(w - 38)}" y="{_n(sy + 13)}">{score}</text>')
    if mark:
        out.append(f'<g transform="translate({_n(w - 22)} -10)">'
                   '<path class="warn-field" d="M12 0L24 21H0Z"/>'
                   f'<text class="warn-text" x="12" y="17">{escape(mark)}</text></g>')
    out.append("</g>")
    return "".join(out)


def trace(d: str, nodes: list[Pt], *, signed: bool, lit: int | None = None) -> str:
    """Dotted trace with one diamond per agent; the last node is the signature."""
    lit = len(nodes) if lit is None else lit
    out = [f'<path class="trace" pathLength="100" d="{d}"/>']
    for i, (nx, ny) in enumerate(nodes):
        last = i == len(nodes) - 1
        cls = "node sig" if last else "node"
        if (last and signed) or (not last and i < lit):
            cls += " on"
        r = 7 if last else 5.5
        out.append(f'<path class="{cls} n{i + 1}" d="M{_n(nx)} {_n(ny - r)}l{r} {r}'
                   f'-{r} {r}-{r} -{r}z"/>')
    return "".join(out)


def svg(body: str, view: str, label: str = "", cls: str = "") -> Markup:
    a11y = (f'role="img" aria-label="{escape(label)}"' if label else 'aria-hidden="true"')
    return Markup(  # noqa: S704 - built from fixed geometry; labels are escaped
        f'<svg class="il {cls}" viewBox="{view}" xmlns="http://www.w3.org/2000/svg" '
        f'focusable="false" {a11y}>{body}</svg>')


SCENES = ("signed", "unsigned", "blocked", "verify", "private", "budget", "edit", "reset",
          "lost", "solo")
GAP = 16  # space between people standing side by side


@dataclass(frozen=True)
class Layout:
    scale: float
    figures: list[tuple[Person, float, float]]
    boxes: list[Box]
    start: Pt
    nodes: list[Pt]
    rec: Pt
    width: float
    height: int


def layout(keys: Sequence[str], kind: str = "signed") -> Layout:
    """Where everything goes: people side by side (boxes never touch), then the trace and
    the record to their right. Returned for the renderer and for the overlap tests."""
    people = [BY_KEY[k] for k in keys]
    single = len(people) == 1
    s = 1.45 if single and not people[0].wheelchair else 1.0
    base = 250  # every figure stands on the same floor line
    figures: list[tuple[Person, float, float]] = []
    boxes: list[Box] = []
    cursor = 4.0
    for p in people:
        bx0, _, bx1, by1 = person_box(p)
        x = cursor - bx0 * s
        y = base - by1 * s if p.wheelchair or not single else 44
        if p.wheelchair:
            y = 12  # the seated figure fills the frame: 12 + 238 = the floor line
        figures.append((p, x, y))
        boxes.append(place(person_box(p), x, y, s))
        cursor = boxes[-1][2] + GAP
    right = max(b[2] for b in boxes)
    sx = right + 8
    sy = 150.0
    nodes: list[Pt] = [(sx + 22, sy - 26), (sx + 56, 90), (sx + 94, 84), (sx + 126, 104),
                       (sx + 158, 126)]
    rec_x = sx + 186
    width = rec_x + 194 if kind != "solo" else right + 4
    return Layout(s, figures, boxes, (sx, sy), nodes, (rec_x, 58.0), width, 250)


def scene(key: str, kind: str = "signed", label: str = "", cls: str = "") -> Markup:
    """A two-views scene for ``key`` (one cast member). Digits and signs only, no words.

    - signed: owner → producer, copywriter, art director, reviewers → signature → record;
    - unsigned: the same, signature node empty, the record shows the ``!`` field;
    - blocked: reviewers' node crossed (×), record not signed;
    - verify: the record and a chain of receipts, one broken link (×);
    - private: a personal-data field stays inside the studio (the trace forks, one branch ×);
    - budget: a daily cap gauge under the record;
    - edit: a pencil on a field of the record before the signature;
    - reset: the record with a circular arrow (hourly reset);
    - lost: 404, the record with a big ``?``;
    - solo: the owner alone (gallery card).
    """
    if kind == "solo":
        p = BY_KEY[key]
        box = person_box(p)
        draw = seated(p, -box[0] + 2, 10) if p.wheelchair else bust(p, -box[0] + 2, 10)
        return svg(draw, f"0 0 {_n(box[2] - box[0] + 4)} {_n(box[3] + 14)}", label, cls)
    return group([key], kind, label, cls)


def group(keys: Sequence[str], kind: str = "signed", label: str = "", cls: str = "") -> Markup:
    """Several owners standing side by side, then the trace and the record."""
    lay = layout(keys, kind)
    s = lay.scale
    body = [figure(p, x, y, s)[0] for p, x, y in lay.figures]
    sx, sy = lay.start
    nodes = lay.nodes
    rec_x, rec_y = lay.rec
    d = curve([(sx, sy), *nodes, (rec_x - 8, 132)])
    signed = kind in ("signed", "verify", "budget", "reset", "private")
    lit = 4
    # crop the empty band above the tallest figure and the record's corner mark
    top = max(0, int(min(min(b[1] for b in lay.boxes), 44) - 4))
    view = f"0 {top} {_n(lay.width)} {lay.height - top}"
    if kind == "lost":
        body.append(trace(d, nodes[:2], signed=False, lit=1))
        body.append(f'<g transform="translate({_n(rec_x)} {rec_y})">'
                    '<rect class="r-card" width="180" height="150"/>'
                    '<rect class="warn-field" x="40" y="24" width="100" height="96"/>'
                    '<text class="warn-big" x="90" y="98">?</text></g>')
        return svg("".join(body), view, label, cls)
    if kind == "blocked":
        lit = 3
        signed = False
    body.append(trace(d, nodes, signed=signed, lit=lit))
    mark = {"unsigned": "!", "blocked": "×", "edit": "!"}.get(kind, "")
    score = {"budget": 8}.get(kind)
    body.append(record(rec_x, rec_y, signed=signed, mark=mark, score=score))
    rx = rec_x
    if kind == "blocked":
        bx, by = nodes[3]
        body.append(f'<path class="stop" d="M{_n(bx - 5)} {_n(by - 5)}l10 10m0 -10l-10 10"/>')
    elif kind == "verify":
        for i in range(4):
            cx = rx + 6 + i * 44
            cls_c = "chain bad" if i == 2 else "chain"
            body.append(f'<rect class="{cls_c}" x="{_n(cx)}" y="222" width="32" height="20"/>')
            if i:
                body.append(f'<path class="trace-solid" d="M{_n(cx - 12)} 232h12"/>')
        body.append(f'<text class="warn-text" x="{_n(rx + 110)}" y="237">×</text>')
    elif kind == "private":
        body.append(f'<path class="trace" d="M{_n(sx + 22)} {sy - 14}q30 60 90 70"/>'
                    f'<rect class="warn-field" x="{_n(sx + 104)}" y="{sy + 46}" width="40" '
                    f'height="18"/><path class="stop" d="M{_n(sx + 119)} {sy + 50}l10 10m0 -10'
                    f'l-10 10"/>')
    elif kind == "budget":
        body.append(f'<rect class="r-field" x="{_n(rx + 10)}" y="222" width="160" '
                    f'height="12"/><rect class="r-score" x="{_n(rx + 10)}" y="222" '
                    f'width="96" height="12"/><path class="stop-line" d="M{_n(rx + 170)} '
                    f'216v24"/>')
    elif kind == "edit":
        body.append(f'<path class="c-accent" d="M{_n(rx + 150)} 124l22 -26l5 4'
                    f'l-22 26l-7 2z"/><rect class="edit-field" x="{_n(rx + 82)}" '
                    f'y="88" width="70" height="12"/>')
    elif kind == "reset":
        body.append(f'<path class="trace-solid" d="M{_n(rx + 190)} 98a22 22 0 1 1 '
                    f'-8 -16"/><path class="r-score" d="M{_n(rx + 180)} 72l6 12'
                    f'l-12 0z"/><text class="r-text" x="{_n(rx + 150)}" y="234">60</text>')
    return svg("".join(body), view, label, cls)
