"""Budget advice: the learning-phase estimate kept between the account floor and the
studio's ceiling, and what the owner's own amount buys."""

from studio.budget_advice import advise


def test_leads_in_gbp_need_more_than_the_sandbox_ceiling():
    a = advise("leads", "GBP", ceiling=3, amount=3, min_daily_minor=76)
    assert a["minimum"] == 1 and a["learning"] == 43  # 50 × £6 / 7, rounded up
    assert a["suggested"] == 3 and a["fits_learning"] is False
    assert a["results_per_week"] == 3 and a["amount_fits_learning"] is False


def test_traffic_fits_and_the_currency_scales_the_estimate():
    gbp = advise("traffic", "GBP", ceiling=50)
    pln = advise("traffic", "PLN", ceiling=50)
    assert gbp["learning"] == 3 and gbp["suggested"] == 3 and gbp["fits_learning"]
    assert pln["learning"] == 15 and pln["cost_per_result"] == 2.0


def test_the_account_floor_wins_over_a_tiny_estimate():
    a = advise("awareness", "GBP", ceiling=20, min_daily_minor=500)
    assert a["minimum"] == 5 and a["suggested"] == 5


def test_an_unknown_goal_still_gives_an_estimate():
    a = advise("something", "GBP", ceiling=10)
    assert a["estimate"] is True and a["suggested"] >= 1


def test_an_unknown_currency_gives_no_advice_and_says_why():
    """Review fix E13: no GBP scale is assumed for a currency the table does not know."""
    for code in ("XYZ", "BRL", "pln!"):
        a = advise("traffic", code, ceiling=10, amount=5)
        assert a["estimate"] is False and a["reason"] == "unknown_currency"
        assert "suggested" not in a and "learning" not in a


def test_minor_units_follow_the_currency_offset():
    """Review fix E13: JPY and HUF have no minor units on Meta (offset 1)."""
    from studio.budget_advice import with_estimate

    jpy = advise("awareness", "JPY", ceiling=50_000, min_daily_minor=500, amount=4000)
    assert jpy["minimum"] == 500  # ¥500, not ¥5
    curve = [{"spend_minor": 1000, "reach": 1000, "results": 10},
             {"spend_minor": 10_000, "reach": 5000, "results": 50}]
    out = with_estimate(jpy, {"curve": curve})
    assert out["meta"]["amount_reach"] == _between(4000, curve)
    gbp = advise("awareness", "GBP", ceiling=20, min_daily_minor=500)
    assert gbp["minimum"] == 5


def _between(spend, curve):
    a, b = curve
    return int(a["reach"] + (b["reach"] - a["reach"]) * (spend - a["spend_minor"])
               / (b["spend_minor"] - a["spend_minor"]))


def test_metas_curve_gives_reach_and_results_for_both_amounts():
    from studio.budget_advice import with_estimate
    curve = [{"spend_minor": 100, "reach": 1000, "results": 10},
             {"spend_minor": 500, "reach": 3000, "results": 40}]
    a = with_estimate(advise("traffic", "GBP", ceiling=3, amount=2),
                      {"audience_lower": 10, "audience_upper": 20, "curve": curve})
    assert a["meta"]["suggested_reach"] == 2000 and a["meta"]["suggested_results"] == 25
    assert a["meta"]["amount_reach"] == 1500 and a["meta"]["amount_results"] == 17
    past_the_curve = with_estimate(advise("sales", "GBP", ceiling=20), {"curve": curve})
    assert past_the_curve["suggested"] == 20 and past_the_curve["meta"]["suggested_reach"] == 3000
    assert with_estimate(a, {"curve": []}) == a


def test_the_draft_carries_metas_estimate_on_the_mock_adapter(tmp_path):
    from fastapi.testclient import TestClient

    from tests.test_brand_scan import _app
    from tests.test_brief_flow import drafted
    from tests.test_ui_studio import login
    app, _ = _app(tmp_path)
    with TestClient(app, base_url="https://testserver") as c:
        login(c)
        d = drafted(c)
    meta = d["budget_advice"]["meta"]
    assert meta["audience_lower"] == 1_200_000 and meta["suggested_reach"] > 0
    assert any(call[0] == "delivery_estimate" for call in app.state.adapter.calls)
