"""Draw the synthetic "Ads Manager" chart used as the analyst's screenshot input.

    .venv/bin/python scripts/make_insights_chart.py

It is a plain bar chart of the synthetic insights (fixtures/meta/insights.SYNTHETIC.json),
labelled SYNTHETIC: not a screenshot of Meta's product, not real ad data.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    data = json.loads((ROOT / "fixtures/meta/insights.SYNTHETIC.json").read_text())["data"]
    w, h = 900, 520
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.text((30, 20), "SYNTHETIC demo insights - Autumn mugs (demo) - CTR % and spend PLN per day",
           fill="black")
    top = max(float(x["ctr"]) for x in data)
    for i, x in enumerate(data):
        ctr, spend = float(x["ctr"]), float(x["spend"])
        x0 = 60 + i * 115
        bar = int(360 * ctr / top)
        d.rectangle([x0, 440 - bar, x0 + 70, 440], fill=(10, 102, 194))
        d.text((x0, 445), x["date_start"][5:], fill="black")
        d.text((x0, 425 - bar), f"CTR {ctr:.2f}", fill="black")
        d.text((x0, 465), f"{spend:.2f} PLN", fill=(90, 90, 90))
        d.text((x0, 485), f"{x['clicks']} clicks", fill=(90, 90, 90))
    out = ROOT / "fixtures/images/insights-chart.SYNTHETIC.png"
    im.save(out, optimize=True)
    print(out, out.stat().st_size)


if __name__ == "__main__":
    main()
