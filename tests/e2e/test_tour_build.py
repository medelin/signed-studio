"""The committed tour file is what ``python -m studio.tour`` makes from the recorded set:
rebuilt offline here, everything that is not random (ids, times, keys, signatures) equal."""

import json

from studio import tour


def test_rebuilt_tour_matches_the_committed_file():
    fresh = tour.build(tour.DEFAULT_SET)
    kept = json.loads(tour.tour_path().read_text())
    for key in ("schema", "set", "recorded", "lang", "plan", "copy", "images", "review",
                "producer_summary", "angle_index"):
        assert fresh[key] == kept[key], key
    assert fresh["brief"]["text"] == kept["brief"]["text"]
    assert fresh["brief"]["confirmed"] == kept["brief"]["confirmed"]
    assert fresh["approval"]["fields"]["brief_sha256"] == kept["approval"]["fields"]["brief_sha256"]
    assert fresh["approval"]["fields"]["spec_sha256"] == kept["approval"]["fields"]["spec_sha256"]

    def shape(receipts):
        return [(r["agent"], r["kind"], r["model"], r["tokens_in"], r["tokens_out"],
                 r["cost_usd"]) for r in receipts]

    assert shape(fresh["receipts"]) == shape(kept["receipts"])
