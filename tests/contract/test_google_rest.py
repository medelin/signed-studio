"""Google Ads REST adapter (API v23) against hand-written response shapes (respx), the
way the Google Ads REST reference documents them. NOT recordings (no Google credential is
used by the tests): they assert the contract that matters regardless — the OAuth refresh
flow, the headers (``login-customer-id`` always, ``developer-token`` only when set), PAUSED
in every creation operation, ``validateOnly`` for the dry run, the retry matrix, no secret
in a URL or an error, and no request at all without the approval."""

import json

import httpx
import pytest
import respx

from studio.agents import google_buyer
from studio.google.adapter import AdapterError, NotApproved
from studio.google.rest import RestAdapter
from tests.approve import approve
from tests.google import RUN, make_env, payload


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


CID = "1234567890"
MANAGER = "9876543210"
SECRET = "client-secret-not-real"
REFRESH = "1//refresh-token-not-real"
ACCESS = "ya29.access-token-not-real"
MUTATE = f"https://googleads.googleapis.com/v23/customers/{CID}/googleAds:mutate"
SEARCH = f"https://googleads.googleapis.com/v23/customers/{CID}/googleAds:search"
TOKEN = "https://oauth2.googleapis.com/token"

CREATED = {"mutateOperationResponses": [
    {"campaignBudgetResult": {"resourceName": f"customers/{CID}/campaignBudgets/111"}},
    {"campaignResult": {"resourceName": f"customers/{CID}/campaigns/222"}},
    {"campaignCriterionResult": {"resourceName": f"customers/{CID}/campaignCriteria/222~2826"}},
    {"adGroupResult": {"resourceName": f"customers/{CID}/adGroups/333"}},
    {"adGroupCriterionResult": {"resourceName": f"customers/{CID}/adGroupCriteria/333~1"}},
    {"adGroupAdResult": {"resourceName": f"customers/{CID}/adGroupAds/333~444"}},
]}


def adapter(**kw):
    return RestAdapter("client-id", SECRET, REFRESH, CID, login_customer_id=MANAGER,
                       sleep=lambda s: None, **kw)


def ok_token(router):
    return router.post(TOKEN).respond(200, json={"access_token": ACCESS, "expires_in": 3599,
                                                 "token_type": "Bearer"})


def test_refresh_token_flow_and_headers_without_a_developer_token(env):
    conn, ledger, _, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    with respx.mock(assert_all_called=True) as router:
        tok = ok_token(router)
        mut = router.post(MUTATE).respond(200, json=CREATED)
        out = adapter(conn=conn).create_search_campaign(p, _claimed(conn, v), validate_only=False)
    form = dict(x.split("=") for x in tok.calls[0].request.content.decode().split("&"))
    assert form["grant_type"] == "refresh_token" and form["client_id"] == "client-id"
    h = mut.calls[0].request.headers
    assert h["authorization"] == f"Bearer {ACCESS}" and h["login-customer-id"] == MANAGER
    assert "developer-token" not in h
    assert out["campaign"] == f"customers/{CID}/campaigns/222"
    assert ACCESS not in str(mut.calls[0].request.url) and REFRESH not in str(
        mut.calls[0].request.url)


def test_the_developer_token_is_sent_only_when_set(env):
    conn, ledger, _, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    with respx.mock() as router:
        ok_token(router)
        mut = router.post(MUTATE).respond(200, json=CREATED)
        adapter(developer_token="dev-token-not-real", conn=conn).create_search_campaign(
            p, _claimed(conn, v))
    assert mut.calls[0].request.headers["developer-token"] == "dev-token-not-real"


def _claimed(conn, v):
    from studio import executor

    return executor.authorize(conn, v, v.action, json.loads(
        conn.execute("SELECT payload FROM pending_payloads WHERE approval_id = ?",
                     (v.pending_id,)).fetchone()[0]))


def test_the_mutate_body_is_the_signed_spec_all_paused_and_atomic(env):
    conn, ledger, _, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    with respx.mock() as router:
        ok_token(router)
        mut = router.post(MUTATE).respond(200, json={})
        adapter(conn=conn).create_search_campaign(p, _claimed(conn, v), validate_only=True)
    body = json.loads(mut.calls[0].request.content)
    assert body["validateOnly"] is True and body["partialFailure"] is False
    ops = body["mutateOperations"]
    by = {next(iter(o)): next(iter(o.values()))["create"] for o in ops}
    assert by["campaignOperation"]["status"] == "PAUSED"
    assert by["campaignOperation"]["advertisingChannelType"] == "SEARCH"
    assert by["campaignOperation"]["campaignBudget"] == f"customers/{CID}/campaignBudgets/-1"
    assert by["campaignBudgetOperation"]["amountMicros"] == "6000000"
    assert by["adGroupOperation"]["status"] == "PAUSED"
    assert by["adGroupAdOperation"]["status"] == "PAUSED"
    rsa = by["adGroupAdOperation"]["ad"]["responsiveSearchAd"]
    assert [h["text"] for h in rsa["headlines"]] == p["google"]["ad"]["headlines"]
    assert by["campaignCriterionOperation"]["location"] == {
        "geoTargetConstant": "geoTargetConstants/2826"}
    assert not any(o.get("status") == "ENABLED" for o in by.values()
                   if isinstance(o, dict) and "adGroup" not in o)


def test_a_dry_run_creates_nothing_and_the_real_call_follows(env):
    conn, ledger, _, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    with respx.mock() as router:
        ok_token(router)
        router.post(SEARCH).respond(200, json={"results": [{"customer": {
            "currencyCode": "GBP", "timeZone": "Europe/London"}}]})
        mut = router.post(MUTATE).mock(side_effect=[httpx.Response(200, json={}),
                                                    httpx.Response(200, json=CREATED)])
        out = google_buyer.create(conn, ledger, adapter(conn=conn), v, p)
    assert out["status"] == "PAUSED"
    assert [json.loads(c.request.content)["validateOnly"] for c in mut.calls] == [True, False]


def test_no_request_at_all_without_the_approval(env):
    conn, ledger, _, _ = env
    with respx.mock(assert_all_called=False) as router:
        tok, mut = router.post(TOKEN), router.post(MUTATE)
        a = adapter(conn=conn)
        with pytest.raises(NotApproved):
            a.create_search_campaign(payload(), object())
        p = payload()
        v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
        with pytest.raises(NotApproved):  # a different payload than the signed one
            a.create_search_campaign({**p, "link": "https://evil.example/"}, v)
    assert tok.call_count == 0 and mut.call_count == 0


def test_account_currency_comes_from_the_customer_resource_and_can_be_overridden():
    row = {"results": [{"customer": {"currencyCode": "GBP", "timeZone": "Europe/London"}}]}
    with respx.mock() as router:
        ok_token(router)
        search = router.post(SEARCH).respond(200, json=row)
        a = adapter()
        assert a.account_info() == {"currency": "GBP", "time_zone": "Europe/London"}
        assert a.account_info()["currency"] == "GBP"
    assert search.call_count == 1  # read once
    assert "customer.currency_code" in json.loads(search.calls[0].request.content)["query"]
    with respx.mock() as router:
        ok_token(router)
        router.post(SEARCH).respond(200, json=row)
        assert adapter(account_currency="EUR").account_info()["currency"] == "EUR"


def test_insights_are_a_gaql_search_by_day():
    rows = {"results": [{"segments": {"date": "2026-10-27"}, "metrics": {
        "impressions": "412", "clicks": "21", "costMicros": "7840000",
        "conversions": 1.0, "ctr": 0.051}}]}
    name = f"customers/{CID}/campaigns/222"
    with respx.mock() as router:
        ok_token(router)
        search = router.post(SEARCH).respond(200, json=rows)
        out = adapter().insights(name)
    q = json.loads(search.calls[0].request.content)["query"]
    for field in ("segments.date", "metrics.impressions", "metrics.clicks",
                  "metrics.cost_micros", "metrics.conversions", "metrics.ctr"):
        assert field in q
    assert name in q and "LAST_7_DAYS" in q
    assert out == [{"date": "2026-10-27", "impressions": 412, "clicks": 21, "cost": 7.84,
                    "conversions": 1.0, "ctr": 5.1}]
    with pytest.raises(AdapterError):
        adapter().insights("customers/1234567890/campaigns/1' OR '1'='1")


def test_transient_errors_are_retried_and_fatal_ones_are_not(env):
    conn, ledger, _, _ = env
    sleeps = []
    a = RestAdapter("c", SECRET, REFRESH, CID, sleep=sleeps.append)
    with respx.mock() as router:
        ok_token(router)
        route = router.post(SEARCH).mock(side_effect=[
            httpx.Response(503, json={"error": {"status": "UNAVAILABLE"}}),
            httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
            httpx.Response(200, json={"results": []})])
        assert a.insights(f"customers/{CID}/campaigns/1") == []
    assert route.call_count == 3 and sleeps == [1, 2]
    fatal = {"error": {"code": 403, "status": "PERMISSION_DENIED", "message":
                       f"secret detail {ACCESS}", "details": [{"errors": [{
                           "errorCode": {"authorizationError": "USER_PERMISSION_DENIED"},
                           "message": REFRESH}]}]}}
    with respx.mock(assert_all_called=False) as router:
        ok_token(router)
        route = router.post(SEARCH).respond(403, json=fatal)
        with pytest.raises(AdapterError) as exc:
            a.insights(f"customers/{CID}/campaigns/1")
    assert route.call_count == 1 and exc.value.code == "google_error"
    text = str(exc.value)
    assert "403" in text and "PERMISSION_DENIED" in text and "authorizationError" in text
    assert ACCESS not in text and REFRESH not in text and "secret detail" not in text


def test_a_401_refreshes_the_access_token_once():
    with respx.mock() as router:
        tok = ok_token(router)
        route = router.post(SEARCH).mock(side_effect=[httpx.Response(401, json={}),
                                                      httpx.Response(200, json={"results": []})])
        assert adapter().insights(f"customers/{CID}/campaigns/1") == []
    assert tok.call_count == 2 and route.call_count == 2


def test_a_refused_refresh_token_names_no_secret():
    with respx.mock() as router:
        router.post(TOKEN).respond(400, json={"error": "invalid_grant",
                                              "error_description": f"Bad {REFRESH}"})
        with pytest.raises(AdapterError) as exc:
            adapter().account_info()
    assert exc.value.code == "oauth_error" and "invalid_grant" in str(exc.value)
    assert REFRESH not in str(exc.value) and SECRET not in repr(adapter())


def test_a_network_error_on_a_write_is_not_retried(env):
    conn, ledger, _, _ = env
    p = payload()
    v = approve(conn, ledger, "google.create", "gcmp_aaaaaa111111", p, RUN)
    with respx.mock() as router:
        ok_token(router)
        route = router.post(MUTATE).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(AdapterError) as exc:
            adapter(conn=conn).create_search_campaign(p, _claimed(conn, v))
    assert route.call_count == 1 and exc.value.code == "network"


def test_activation_is_one_mutate_that_enables_ad_group_then_campaign(env):
    conn, ledger, _, _ = env
    names = {"campaign": f"customers/{CID}/campaigns/222",
             "ad_group": f"customers/{CID}/adGroups/333",
             "ad_group_ad": f"customers/{CID}/adGroupAds/333~444"}
    p = {"action": "google.activate", "run_id": RUN, "customer_id": CID, **names}
    v = approve(conn, ledger, "google.activate", "gcmp_aaaaaa111111", p, RUN)
    with respx.mock() as router:
        ok_token(router)
        mut = router.post(MUTATE).respond(200, json={})
        adapter(conn=conn).activate(p, _claimed(conn, v))
    ops = json.loads(mut.calls[0].request.content)["mutateOperations"]
    assert [next(iter(o)) for o in ops] == ["adGroupAdOperation", "adGroupOperation",
                                            "campaignOperation"]
    assert all(next(iter(o.values()))["updateMask"] == "status" and next(
        iter(o.values()))["update"]["status"] == "ENABLED" for o in ops)


def test_from_settings_uses_the_env_names():
    from studio.settings import Settings

    s = Settings.load({"GOOGLE_ADS_ADAPTER": "rest", "GOOGLE_ADS_CLIENT_ID": "id",
                       "GOOGLE_ADS_CLIENT_SECRET": SECRET, "GOOGLE_ADS_REFRESH_TOKEN": REFRESH,
                       "GOOGLE_ADS_CUSTOMER_ID": CID, "GOOGLE_ADS_LOGIN_CUSTOMER_ID": MANAGER})
    from studio.google.adapter import make

    a = make(s)
    assert isinstance(a, RestAdapter) and a.customer_id == CID
    assert SECRET not in repr(a) and REFRESH not in repr(a)
