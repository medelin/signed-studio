"""The ad account's currency: read from the account (recorded read-only response), every
budget in its minor units by Meta's offsets, a brief in another currency refused with a
field error (never converted), the ceiling in the account's currency."""

import json

import httpx
import pytest

from studio import brief as bv
from studio.meta import audience
from studio.meta.currency import from_minor, offset, to_minor
from studio.meta.graph import GraphAdapter
from studio.meta.mock import MockAdapter
from tests.cassettes import load
from tests.test_brief import sample

RECORDED = load("graph_readonly")


def _recorded(name):
    return next(i for i in RECORDED["interactions"] if i["name"] == name)


def test_offsets_follow_metas_table():
    assert offset("GBP") == offset("PLN") == offset("EUR") == 100
    assert offset("JPY") == offset("HUF") == 1
    assert to_minor(6, "GBP") == 600 and to_minor(6.005, "GBP") == 601
    assert to_minor(500, "JPY") == 500 and from_minor(750, "GBP") == 7.5
    with pytest.raises(ValueError):
        offset("gbp")


def test_account_currency_is_read_once_from_the_account():
    rec = _recorded("account")
    assert RECORDED["_provenance"] == "recorded" and rec["response"]["json"]["currency"] == "GBP"
    sent = []

    def handler(request):
        sent.append(request)
        assert request.url.path == rec["request"]["path"]
        assert request.url.params["fields"] == "currency,timezone_name"
        return httpx.Response(200, json=rec["response"]["json"])

    g = GraphAdapter("EAAtest-token-not-a-secret", "act_100000000000001", "600000000000001",
                     transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert g.account_info() == {"currency": "GBP", "timezone_name": "Europe/Warsaw"}
    assert g.account_info()["currency"] == "GBP" and len(sent) == 1
    assert MockAdapter(currency="GBP").account_info()["currency"] == "GBP"


def test_a_brief_in_another_currency_is_a_field_error():
    b = sample()  # 40 PLN a day
    assert bv.problems(b, currency="PLN") == {}
    assert bv.problems(b, currency="GBP") == {"budget.currency": "account_currency"}
    gbp = sample(budget={**b.budget.model_dump(), "currency": "GBP", "amount": 6.0})
    assert bv.problems(gbp, currency="GBP") == {}
    assert bv.problems(gbp, currency="GBP", ceiling_daily=5) == {
        "budget.amount": "over_ceiling"}


def test_recorded_locale_search_needs_a_name_not_an_iso_code():
    rec = _recorded("search_locale")["response"]["json"]["data"]

    def search(kind, q):
        return rec if (kind, q) == ("adlocale", "Pol") else []

    r = audience.resolve(search, [], languages=["pl"])
    assert r.locales == [{"hint": "pl", "key": 15, "name": "polski"}]


def test_recorded_interest_search_shape():
    data = _recorded("search_interest")["response"]["json"]["data"]
    r = audience.resolve(lambda k, q: data, ["Ceramics Monthly"])
    assert r.interests[0]["name"] == "Ceramics Monthly" and r.interests[0]["id"].isdigit()
    assert json.dumps(RECORDED).count("act_100000000000001") >= 1
