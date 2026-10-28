"""scripts/eval.py: the numbers in docs/eval.md come from code that is itself tested."""

import importlib.util

from tests.cassettes import ROOT

spec = importlib.util.spec_from_file_location("eval_script", ROOT / "scripts" / "eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_every_tamper_class_is_rejected_and_clean_exports_pass():
    t = ev.tamper_suite(2, seed=11)
    assert len(t["rows"]) == 23
    assert all(r["rejected"] == r["n"] == 2 for r in t["rows"]), t["rows"]
    assert t["clean"] == {"n": 2, "accepted": 2}


def test_reviewer_metrics_from_the_recorded_reviews():
    m = ev.reviewer_metrics()
    assert m["n_bad"] == 20 and m["n_clean"] == 5
    assert m["p9_n"] == 5 and m["p9_final_blocked"] == 5
    assert m["clean_blocked"] == 0
    assert m["final_flagged"] >= m["model_flagged"]


def test_eval_report_states_the_headline_numbers():
    text = (ROOT / "docs" / "eval.md").read_text()
    assert "575/575 rejected" in text and "same OK-vs-flag decision" in text


def test_agreement_from_the_recorded_open_weights_reviews():
    a = ev.agreement()
    assert a["n"] == 25 and a["errors"] == 0
    assert 0 <= a["same_verdict"] <= a["same_decision"] <= 25
    assert a["either_flagged"] >= a["open_flagged"]
    assert a["cost_usd"] > 0
