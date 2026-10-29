"""The six onboarding drawings for ``/start``: the same "two views" language as
``studio.ui.cast`` (the owner as a warm line, the ad as a dashed record, a dotted trace with
agent diamonds), one idea per step.

Same rules as the cast (tested in ``tests/test_onboarding.py``): colours only through
classes (``illustrations.css`` and ``onboarding.css``), no ``style``/``id``/``<defs>``/images,
no words in drawings (digits and signs only), ``role="img"`` + label. The motion lives in
``onboarding.css``: every animated part is a class ``ob-*``, and the drawing's resting state
(no animation, ``prefers-reduced-motion``) is its final frame, so it reads on its own.
"""

from __future__ import annotations

from collections.abc import Callable

from markupsafe import Markup

from studio.ui import cast

VIEW = "0 0 400 240"
PERSON_AT = (4.0, 96.0)  # a 120 × 140 bust standing on the bottom edge


def _owner(key: str) -> str:
    return cast.bust(cast.BY_KEY[key], *PERSON_AT)


def _diamond(x: float, y: float, cls: str, r: float = 6.5) -> str:
    return f'<path class="node on {cls}" d="M{x} {y - r}l{r} {r}-{r} {r}-{r} -{r}z"/>'


def _tick(x: float, y: float, cls: str = "r-sig") -> str:
    return f'<path class="{cls}" d="M{x} {y}l5 5l10 -10"/>'


def _card(x: float, y: float, w: float, h: float, extra: str = "", build: bool = False) -> str:
    """A small ad record: picture block, headline, two text lines, a button. ``build`` marks
    the parts that appear one by one (step 3 only)."""
    pw, ph = w - 24, h * .42
    pic, head, lines = (" ob-pic", " ob-head", " ob-lines") if build else ("", "", "")
    return (f'<g transform="translate({x} {y})"><rect class="r-card" width="{w}" height="{h}"/>'
            f'<rect class="r-field{pic}" x="12" y="12" width="{pw}" height="{ph:.0f}"/>'
            f'<path class="r-pic{pic}" d="M18 {12 + ph - 6:.0f}l18 -18 12 12 10 -8 '
            f'{pw - 46:.0f} 14"/>'
            f'<rect class="r-value{head}" x="12" y="{ph + 22:.0f}" width="{w * .62:.0f}" '
            f'height="9"/>'
            f'<rect class="r-label{lines}" x="12" y="{ph + 38:.0f}" width="{pw:.0f}" '
            f'height="5"/><rect class="r-label{lines}" x="12" y="{ph + 49:.0f}" '
            f'width="{pw * .7:.0f}" height="5"/>'
            f'<rect class="r-score{lines}" x="12" y="{h - 22:.0f}" width="44" height="12"/>'
            f'{extra}</g>')


def brief() -> str:
    """1 · the owner speaks; three words flow into a brief card."""
    arcs = "".join(f'<path class="trace-solid ob-arc ob-a{i}" d="M114 {150 - r}a{r} {r} 0 0 1 '
                   f'0 {2 * r}"/>' for i, r in enumerate((6, 12, 18), 1))
    words = "".join(
        f'<rect class="r-label ob-word ob-w{i}" x="248" y="{y}" width="{w}" height="7"/>'
        for i, (y, w) in enumerate(((112, 124), (130, 100), (148, 72)), 1))
    return (_owner("amara") + arcs
            + '<rect class="r-card" x="232" y="40" width="160" height="170"/>'
            + '<rect class="r-value" x="248" y="58" width="84" height="10"/>'
            + '<rect class="r-field" x="248" y="76" width="24" height="26"/>'
            + '<rect class="c-line" x="256" y="80" width="8" height="12" rx="4"/>'  # mic
            + '<path class="c-line" d="M253 89q7 9 14 0M260 94v4"/>'
            + words
            + '<path class="trace ob-flow" pathLength="100" d="M146 146q40 -34 82 -26"/>')


def facts() -> str:
    """2 · two confirmed facts; the open [FACT] question is answered and becomes a tick."""
    rows = []
    for i, y in enumerate((78, 112)):
        rows.append(f'<g class="ob-row ob-r{i + 1}"><rect class="r-signed" x="200" y="{y}" '
                    f'width="40" height="22"/>{_tick(212, y + 11)}'
                    f'<rect class="r-label" x="252" y="{y + 8}" width="{124 - i * 30}" '
                    f'height="6"/></g>')
    return (_owner("harjit")
            + '<rect class="r-card" x="184" y="34" width="208" height="182"/>'
            + '<rect class="r-value" x="200" y="50" width="96" height="10"/>'
            + "".join(rows)
            + '<rect class="warn-field" x="200" y="150" width="40" height="22"/>'
            + '<text class="warn-text" x="220" y="166">?</text>'
            + '<path class="trace-solid ob-arrow" d="M246 161h18M258 155l6 6-6 6"/>'
            + '<g class="ob-answer"><rect class="r-signed" x="270" y="150" width="40" '
              'height="22"/>' + _tick(282, 161) + '</g>'
            + '<rect class="r-label ob-answer" x="320" y="158" width="56" height="6"/>'
            + '<path class="trace ob-flow" pathLength="100" d="M124 160q30 0 56 -10"/>')


def agents() -> str:
    """3 · plan, write, draw, cut out: four agent diamonds light up and the ad fills in."""
    nodes = ((150, 128), (182, 100), (216, 94), (246, 112))
    d = cast.curve([(124, 170), *nodes, (262, 132)])
    diamonds = "".join(_diamond(x, y, f"ob-n{i}") for i, (x, y) in enumerate(nodes, 1))
    cut = ('<path class="c-accent ob-product" d="M50 30h22v22q0 4 -4 4h-14q-4 0 -4 -4zM72 '
           '34q8 0 8 6t-8 6"/><rect class="ob-cut" x="41" y="23" width="46" height="40" '
           'rx="6"/>')
    return (_owner("mei") + f'<path class="trace ob-flow" pathLength="100" d="{d}"/>'
            + diamonds + _card(262, 36, 132, 186, cut, build=True))


def review() -> str:
    """4 · two independent reviewers: BLOCK (×), the text is rewritten, then PASS (tick)."""
    looks = ('<path class="trace" pathLength="100" d="M92 70q20 10 40 30"/>'
             '<path class="trace" pathLength="100" d="M308 70q-20 10 -40 30"/>')
    reviewers = (_diamond(84, 64, "ob-n1", 9)
                 + _diamond(316, 64, "ob-n2", 9)
                 + '<path class="c-line" d="M316 59l5 5-5 5-5 -5z"/>')  # the open one
    rewrite = ('<rect class="edit-field ob-edit" x="135" y="135" width="102" height="17"/>'
               '<path class="c-accent ob-edit" d="M242 154l18 -21l5 4l-18 21l-7 2z"/>')
    verdicts = ('<g class="ob-block"><rect class="warn-field" x="126" y="208" width="44" '
                'height="24"/><path class="stop" d="M142 214l12 12m0 -12l-12 12"/></g>'
                '<path class="trace-solid ob-arrow" d="M178 220h36M208 214l6 6-6 6"/>'
                '<g class="ob-pass"><rect class="r-signed" x="222" y="208" width="44" '
                'height="24"/>' + _tick(236, 220) + '</g>')
    return (looks + reviewers + _card(128, 56, 144, 146) + rewrite + verdicts)


def sign() -> str:
    """5 · the owner signs on a phone; the ad lands PAUSED in her own ad-account frame."""
    phone = ('<rect class="device" x="132" y="66" width="62" height="124" rx="9"/>'
             '<path class="c-line" d="M156 76h14"/>'
             '<rect class="r-label" x="142" y="90" width="42" height="5"/>'
             '<rect class="r-label" x="142" y="101" width="34" height="5"/>'
             '<rect class="r-label" x="142" y="112" width="38" height="5"/>'
             '<rect class="r-signed" x="140" y="146" width="46" height="26"/>'
             '<path class="r-sig ob-sign" pathLength="100" d="M146 164c5 -12 9 4 13 -3'
             's5 -8 8 1 6 -6 13 -4"/>')
    frame = ('<g class="ob-frame-in"><rect class="ob-frame" x="238" y="32" width="156" '
             'height="196"/><circle class="c-line" cx="252" cy="46" r="6"/>'
             '<rect class="r-label" x="264" y="43" width="64" height="6"/>'
             + _card(250, 62, 132, 130) + '</g>')
    badge = ('<g class="ob-paused"><rect class="warn-field" x="318" y="198" width="60" '
             'height="22"/><path class="warn-mark" d="M343 204v10M353 204v10"/></g>')
    return (_owner("grace") + phone
            + '<path class="trace ob-flow" pathLength="100" d="M198 128q20 -8 36 -2"/>'
            + frame + badge)


def inbox() -> str:
    """6 · comments and a lead drop into one inbox; a row of receipts lights up."""
    items = ('<path class="ob-bubble ob-d1" d="M178 102h44v24h-30l-8 8v-8h-6z"/>'
             '<path class="ob-bubble ob-d2" d="M232 96h44v24h-6v8l-8 -8h-30z"/>'
             '<g class="ob-d3"><rect class="r-field" x="286" y="98" width="40" height="30"/>'
             '<circle class="c-accent" cx="298" cy="110" r="5"/>'
             '<path class="c-line" d="M308 108h12M308 116h8M292 122h28"/></g>')
    tray = ('<path class="ob-tray" d="M168 118v38q0 10 10 10h150q10 0 10 -10v-38h-42l-10 18'
            'h-66l-10 -18z"/>')
    chain = []
    for i in range(5):
        x = 150 + i * 48
        chain.append(f'<g class="ob-rc ob-c{i + 1}"><rect class="chain" x="{x}" y="196" '
                     f'width="34" height="22"/>{_tick(x + 10, 207)}</g>')
        if i:
            chain.append(f'<path class="trace-solid" d="M{x - 14} 207h14"/>')
    return (_owner("diego") + items + tray
            + '<path class="trace ob-flow" pathLength="100" d="M164 150q-18 6 -38 4"/>'
            + "".join(chain))


STEPS: tuple[Callable[[], str], ...] = (brief, facts, agents, review, sign, inbox)


def art(n: int, label: str) -> Markup:
    """Drawing for step ``n`` (1-based), labelled for screen readers."""
    return cast.svg(STEPS[n - 1](), VIEW, label, f"ob-art ob-art-{n}")
