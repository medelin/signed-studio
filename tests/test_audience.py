"""Audiences: Advantage+ by default with the owner's hints as suggestions, hints resolved
to Meta ids by code (never guessed), exclusions, the owner's own custom audiences and a
lookalike built from the owner's source audience only."""

import json

import httpx
import pytest

from studio import brief as bv
from studio import db
from studio.agents import media_buyer
from studio.meta import audience
from studio.meta.mock import MockAdapter
from tests.approve import approve
from tests.attack.test_paused_only import PNG, RUN, payload
from tests.contract.test_graph import adapter
from tests.test_brief import sample


def search(kind, q):
    table = {("adinterest", "ceramics"): [{"id": "6003107902433", "name": "Pottery"},
                                          {"id": "6003000000001", "name": "Ceramics"}],
             ("adinterest", "coffee"): [{"id": "6003012345678", "name": "Coffee"}],
             ("adlocale", "pol"): [{"key": 15, "name": "polski"}]}
    return table.get((kind, q.casefold()), [])


def test_resolve_prefers_the_exact_name_and_never_guesses():
    r = audience.resolve(search, ["Ceramics", "coffee", "zzz-unknown"], ["coffee"], ["pl"])
    assert r.interests == [{"hint": "Ceramics", "id": "6003000000001", "name": "Ceramics"},
                           {"hint": "coffee", "id": "6003012345678", "name": "Coffee"}]
    assert r.excluded_interests[0]["id"] == "6003012345678"
    assert r.locales == [{"hint": "pl", "key": 15, "name": "polski"}]
    assert r.unresolved == ["zzz-unknown"] and r.searches == 5
    offline = audience.resolve(None, ["coffee"])
    assert offline.interests == [] and offline.unresolved == ["coffee"]


def test_advantage_audience_is_the_default_with_suggestions():
    r = audience.resolve(search, ["coffee"], languages=["pl"])
    t = audience.targeting(countries=["PL"], age_min=30, age_max=50, genders=["female"],
                           resolved=r, excluded_countries=["DE"],
                           custom_audiences=["2380000000000001"],
                           excluded_custom_audiences=["2380000000000002"])
    assert t["targeting_automation"] == {"advantage_audience": 1}
    assert (t["age_min"], t["age_max"], t["age_range"]) == (25, 65, [30, 50])
    assert t["genders"] == [2] and t["locales"] == [15]
    assert t["flexible_spec"] == [{"interests": [{"id": "6003012345678", "name": "Coffee"}]}]
    assert t["excluded_geo_locations"] == {"countries": ["DE"]}
    assert t["custom_audiences"] == [{"id": "2380000000000001"}]
    assert t["excluded_custom_audiences"] == [{"id": "2380000000000002"}]
    young = audience.targeting(countries=["PL"], age_min=13, age_max=20)
    assert (young["age_min"], young["age_range"]) == (18, [18, 20])
    assert "genders" not in audience.targeting(countries=["PL"], age_min=20, age_max=30,
                                               genders=["male", "female"])


def test_advantage_off_makes_the_brief_hard_targeting():
    t = audience.targeting(countries=["PL"], age_min=30, age_max=50, advantage=False)
    assert t["targeting_automation"] == {"advantage_audience": 0}
    assert (t["age_min"], t["age_max"]) == (30, 50) and "age_range" not in t


def test_lookalike_comes_only_from_the_owners_audience():
    spec = audience.lookalike_spec("2380000000000003", 2, "PL", "x")
    assert spec["subtype"] == "LOOKALIKE"
    assert spec["lookalike_spec"] == {"ratio": 0.02, "country": "PL"}
    for bad in (("abc", 1), ("238", 0), ("238", 11)):
        with pytest.raises(ValueError):
            audience.lookalike_spec(bad[0], bad[1], "PL", "x")


def test_brief_audience_fields_are_checked_and_grounded():
    au = sample().audience.model_dump()
    b = sample(audience={**au, "excluded_countries": ["PL"], "custom_audience_ids": ["x1"],
                         "lookalike_percent": 0})
    p = bv.problems(b)
    assert p["audience.excluded_countries"] == "excluded_sold"
    assert p["audience.custom_audience_ids"] == "bad_id"
    assert p["audience.lookalike_percent"] == "lookalike_percent"
    drafted = sample(audience={**au, "custom_audience_ids": ["2380000000000001"],
                               "lookalike_source_id": "2380000000000009"})
    grounded = bv.ground_audience(drafted.audience, "use audience 2380000000000001")
    assert grounded.custom_audience_ids == ["2380000000000001"]
    assert grounded.lookalike_source_id == ""  # never written by the owner


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    (tmp_path / "assets" / RUN).mkdir(parents=True)
    (tmp_path / "assets" / RUN / "feed_1x1.png").write_bytes(PNG)
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, tmp_path


def test_lookalike_is_made_before_the_ad_set_and_joins_its_audiences(env):
    conn, ledger, data = env
    p = payload(targeting=audience.targeting(countries=["PL"], age_min=25, age_max=55),
                lookalike=audience.lookalike_spec("2380000000000003", 1, "PL", "lal"))
    v = approve(conn, ledger, "campaign.create", "cmp_lal1", p, RUN)
    mock = MockAdapter()
    media_buyer.create(conn, ledger, mock, v, p, data)
    assert [c[0] for c in mock.calls][:4] == ["upload_image", "create_campaign_paused",
                                              "create_lookalike", "create_adset_paused"]
    lal = next(k for k, o in mock.objects.items() if o.get("type") == "custom_audience")
    adset = next(o for o in mock.objects.values() if o.get("type") == "adset")
    assert {"id": lal} in adset["targeting"]["custom_audiences"]
    assert adset["targeting"]["targeting_automation"] == {"advantage_audience": 1}


def test_graph_search_and_lookalike_requests(env):
    conn, ledger, _ = env
    sent = []

    def handler(request):
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "6003012345678",
                                                       "name": "Coffee"}]})
        return httpx.Response(200, json={"id": "2380000000000777"})

    from tests.test_meta_mock import payload as mpayload

    g = adapter(handler)
    assert g.search_targeting("adinterest", "coffee")[0]["id"] == "6003012345678"
    assert sent[0].url.path == "/v25.0/search"
    assert dict(sent[0].url.params) == {"type": "adinterest", "q": "coffee", "limit": "5"}
    p = mpayload(lookalike=audience.lookalike_spec("2380000000000003", 3, "PL", "lal"))
    v = approve(conn, ledger, "campaign.create", "cmp_lal2", p, "run_m1")
    assert g.create_lookalike(p, v) == "2380000000000777"
    form = dict(httpx.QueryParams(sent[1].content.decode()))
    assert sent[1].url.path == "/v25.0/act_100000000000001/customaudiences"
    assert form["subtype"] == "LOOKALIKE" and form["origin_audience_id"] == "2380000000000003"
    assert json.loads(form["lookalike_spec"]) == {"ratio": 0.03, "country": "PL"}
