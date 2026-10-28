"""Google Search channel: the spec (limits, geo, keywords), the mock adapter, the buyer."""

import json

import pytest

from studio import db
from studio.agents import google_buyer
from studio.agents.media_buyer import BuyError
from studio.approvals import Keys
from studio.brief import Channels
from studio.google import adapter as ga
from studio.google import store
from studio.google.adapter import NotApproved, PausedOnly, SpecError
from studio.receipts import verify
from studio.settings import ConfigError, Settings
from tests.approve import approve
from tests.google import RUN, make_env, payload, spec


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)



def test_the_spec_obeys_the_responsive_search_ad_limits():
    s = spec()
    ad = s["ad"]
    assert 3 <= len(ad["headlines"]) <= 15 and 2 <= len(ad["descriptions"]) <= 4
    assert all(len(h) <= 30 for h in ad["headlines"])
    assert all(len(d) <= 90 for d in ad["descriptions"])
    assert s["campaign"]["status"] == s["ad_group"]["status"] == ad["status"] == "PAUSED"
    assert s["campaign"]["daily_budget_micros"] == 6_000_000
    assert s["geo"] == {"countries": ["GB"], "criteria": ["2826"]}
    assert [k["text"] for k in s["keywords"]][:2] == ["handmade mug", "handmade mug London"]


def test_text_that_does_not_fit_is_dropped_never_truncated():
    long_h = "H" * 31
    s = spec(variants=[{"headline": long_h, "description": "d" * 91, "primary_text": ""},
                       {"headline": "Slow mornings start here",
                        "description": "Handmade stoneware mugs, thrown in small batches.",
                        "primary_text": "Each mug is shaped by hand and fired twice."}])
    assert long_h not in s["ad"]["headlines"] and "d" * 91 not in s["ad"]["descriptions"]
    assert not any(h.startswith("HHHH") for h in s["ad"]["headlines"])


def test_a_brief_with_too_little_text_makes_no_spec():
    with pytest.raises(SpecError) as exc:
        spec(variants=[], product="x", display_name="y", offer="", proposition="z" * 100)
    assert exc.value.code in ("google_headlines", "google_descriptions")


def test_an_unknown_country_is_refused_not_guessed():
    with pytest.raises(SpecError) as exc:
        spec(countries=["XX"])
    assert exc.value.code == "google_geo"


@pytest.mark.parametrize("edit", [
    lambda s: s["campaign"].update(status="ENABLED"),
    lambda s: s["ad_group"].update(status="ENABLED"),
    lambda s: s["ad"].update(status="ENABLED"),
], ids=["campaign", "ad_group", "ad"])
def test_anything_but_paused_is_refused(edit):
    s = spec()
    edit(s)
    with pytest.raises(PausedOnly):
        ga.check_spec(s)


@pytest.mark.parametrize("edit,code", [
    (lambda s: s["ad"]["headlines"].append("H" * 31), "google_headlines"),
    (lambda s: s["ad"]["descriptions"].append("d" * 91), "google_descriptions"),
    (lambda s: s["ad"].update(headlines=["a", "b"]), "google_headlines"),
    (lambda s: s["ad"]["headlines"].__setitem__(0, "Buy now!"), "google_headlines"),
    (lambda s: s["ad"].update(final_url="http://example.com"), "google_url"),
    (lambda s: s["geo"].update(criteria=["2616"]), "google_geo"),
    (lambda s: s["keywords"].append({"text": "a{b}", "match_type": "PHRASE"}),
     "google_keywords"),
    (lambda s: s["campaign"].update(daily_budget_micros=0), "google_spec"),
    (lambda s: s.update(extra=1), "google_spec"),
], ids=["h31", "d91", "few-h", "bang", "http", "geo", "kw", "budget", "extra"])
def test_a_spec_outside_the_rules_is_refused(edit, code):
    s = spec()
    edit(s)
    with pytest.raises(SpecError) as exc:
        ga.check_spec(s)
    assert exc.value.code == code


def test_micros_round_up():
    assert ga.micros(6) == 6_000_000 and ga.micros(0.0000011) == 2 and ga.micros(0) == 0


def test_mock_creates_everything_paused_in_one_atomic_request(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    assert v.spec_sha256 == ga.spec_sha256(p["google"])
    out = google_buyer.create(conn, ledger, mock, v, p)
    assert out["status"] == "PAUSED"
    kinds = [o["type"] for o in mock.objects.values()]
    assert kinds.count("campaignOperation") == 1 and kinds.count("adGroupAdOperation") == 1
    assert [c[0] for c in mock.calls] == ["validate", "create_search_campaign"]
    for o in mock.objects.values():
        if o["type"] in ("campaignOperation", "adGroupOperation", "adGroupAdOperation"):
            assert o["status"] == "PAUSED"
    steps = [r.details["tool"] for r in ledger.receipts(RUN) if r.kind == "tool_call"]
    assert steps == ["google_validate", "google_create"]
    assert {r.details["spec_sha256"] for r in ledger.receipts(RUN)
            if r.kind == "tool_call"} == {v.spec_sha256}
    row = store.campaign(conn, v.item_id)
    assert row["status"] == "PAUSED" and set(row["resources"]) == {
        "budget", "campaign", "ad_group", "ad_group_ad"}


def test_activation_is_its_own_approval_and_enables_the_parent_last(env, tmp_path):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    pending, ap = google_buyer.request_activation(conn, ledger, v.item_id, 50)
    assert ap["action"] == "google.activate" and pending.spec_sha256 == ""
    v2 = approve(conn, ledger, "google.activate", v.item_id, ap, RUN)
    out = google_buyer.activate(conn, ledger, mock, v2, ap, 50)
    assert out["status"] == "ENABLED"
    assert [c[0] for c in mock.calls[-3:]] == ["activate"] * 3
    assert mock.calls[-1][1] == ap["campaign"]
    assert store.campaign(conn, v.item_id)["status"] == "ENABLED"
    export = tmp_path / "e.jsonl"
    export.write_text(ledger.export_jsonl())
    assert verify(export, ledger.public_key, approver_keys=Keys.registry(conn)).ok


def test_activation_above_the_ceiling_is_refused_before_anything_is_asked(env):
    conn, ledger, mock, _ = env
    p = payload(google=spec(daily_budget=60))
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    with pytest.raises(BuyError) as exc:
        google_buyer.request_activation(conn, ledger, v.item_id, 50)
    assert exc.value.code == "over_ceiling"


def test_the_account_currency_must_be_the_specs(env):
    conn, ledger, mock, _ = env
    mock.currency = "PLN"
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    with pytest.raises(BuyError) as exc:
        google_buyer.create(conn, ledger, mock, v, p)
    assert exc.value.code == "currency_changed" and mock.calls == []


def test_the_payload_builder_rejects_a_budget_above_the_ceiling_and_a_foreign_currency():
    from studio.brief import Channels

    class B:
        channels = Channels(google_search=True, google_daily_budget=60)

        class budget:
            currency = "GBP"

    with pytest.raises(SpecError) as exc:
        google_buyer.payload_for(run_id=RUN, item_id="gcmp_x", brief=B, brief_sha256="",
                                 link="https://example.com/", variants=[],
                                 customer_id="1234567890", currency="GBP", ceiling=50)
    assert exc.value.code == "over_ceiling"
    B.channels = Channels(google_search=True, google_daily_budget=6)
    with pytest.raises(SpecError) as exc:
        google_buyer.payload_for(run_id=RUN, item_id="gcmp_x", brief=B, brief_sha256="",
                                 link="https://example.com/", variants=[],
                                 customer_id="1234567890", currency="PLN", ceiling=50)
    assert exc.value.code == "google_account_currency"


def test_insights_are_the_last_week_by_day(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    assert store.pull_all(conn, ledger, mock) == {"google_campaigns": 1}
    (snap,) = store.latest(conn)
    assert len(snap["days"]) == 7 and snap["totals"]["clicks"] == 164
    assert snap["totals"]["impressions"] == 3022 and snap["currency"] == "GBP"
    assert any(r.kind == "insights.snapshot" and r.details["channel"] == "google_search"
               for r in ledger.receipts())


def test_brief_checks_google_budget_currency_and_countries():
    from studio import brief as b
    from tests.test_brief import sample  # a valid Brief v1

    brief = sample()
    brief = brief.model_copy(update={"channels": Channels(google_search=True,
                                                          google_daily_budget=6)})
    cur = brief.budget.currency
    assert b.problems(brief, currency=cur, google_currency=cur, ceiling_daily=50) == {}
    other = "GBP" if cur != "GBP" else "EUR"
    assert b.problems(brief, currency=cur, google_currency=other
                      )["budget.currency"] == "google_account_currency"
    no_budget = brief.model_copy(update={"channels": Channels(google_search=True)})
    assert b.problems(no_budget, currency=cur, google_currency=cur
                      )["channels.google_daily_budget"] == "amount"
    over = brief.model_copy(update={"channels": Channels(google_search=True,
                                                         google_daily_budget=99)})
    assert b.problems(over, currency=cur, google_currency=cur, ceiling_daily=50
                      )["channels.google_daily_budget"] == "over_ceiling"


def test_settings_default_to_the_mock_and_rest_needs_its_credentials():
    s = Settings.load({})
    assert s.google_adapter == "mock" and not s.google_developer_token
    with pytest.raises(ConfigError, match="GOOGLE_ADS_ADAPTER"):
        Settings.load({"GOOGLE_ADS_ADAPTER": "grpc"})
    with pytest.raises(ConfigError, match="GOOGLE_ADS_CLIENT_ID"):
        Settings.load({"GOOGLE_ADS_ADAPTER": "rest"})
    with pytest.raises(ConfigError, match="customer id"):
        Settings.load({"GOOGLE_ADS_CUSTOMER_ID": "12"})
    ok = Settings.load({"GOOGLE_ADS_ADAPTER": "rest", "GOOGLE_ADS_CLIENT_ID": "a",
                        "GOOGLE_ADS_CLIENT_SECRET": "b", "GOOGLE_ADS_REFRESH_TOKEN": "c",
                        "GOOGLE_ADS_CUSTOMER_ID": "123-456-7890",
                        "GOOGLE_ADS_ACCOUNT_CURRENCY": "GBP"})
    assert ok.google_customer_id == "1234567890" and ok.google_account_currency == "GBP"


def test_secrets_are_not_in_the_settings_repr():
    s = Settings.load({"GOOGLE_ADS_CLIENT_SECRET": "sekret-value-1",
                       "GOOGLE_ADS_REFRESH_TOKEN": "refresh-value-2",
                       "GOOGLE_ADS_DEVELOPER_TOKEN": "dev-value-3"})
    text = repr(s)
    assert not any(x in text for x in ("sekret-value-1", "refresh-value-2", "dev-value-3"))


def test_activation_names_must_belong_to_the_account():
    good = {"customer_id": "1234567890", "campaign": "customers/1234567890/campaigns/1",
            "ad_group": "customers/1234567890/adGroups/2",
            "ad_group_ad": "customers/1234567890/adGroupAds/2~3"}
    assert ga.activation_names(good, "1234567890") == {
        k: good[k] for k in ("campaign", "ad_group", "ad_group_ad")}
    for bad in ({**good, "campaign": "customers/9999999999/campaigns/1"},
                {**good, "ad_group_ad": "customers/1234567890/adGroupAds/x"},
                {**good, "customer_id": "9999999999"}):
        with pytest.raises(NotApproved):
            ga.activation_names(bad, "1234567890")


def test_db_reset_clears_google_tables(env):
    conn, ledger, mock, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    google_buyer.create(conn, ledger, mock, v, p)
    db.reset_demo(conn)
    assert store.campaigns(conn) == []


def test_json_serialisable_payload():
    json.dumps(payload())
