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


def test_an_unknown_goal_or_currency_still_gives_an_estimate():
    a = advise("something", "XYZ", ceiling=10)
    assert a["estimate"] is True and a["suggested"] >= 1
