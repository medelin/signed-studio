"""Plans, pricing page, Terms, the payment consent before Stripe Checkout and the ad-spend
fee: prices are read from Stripe once per plan and cached, fall back to
STUDIO_PRICE_DISPLAY or to no amount, and checkout refuses without the plan, the buyer type
and the consents that buyer must give."""

import json
import secrets
import time
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import billing, pricing, routes_billing
from studio.app import create_app
from studio.billing import BillingConfig, StripeApi
from studio.meta import measurement
from studio.meta.mock import MockAdapter
from studio.ui import help_texts, i18n, legal_texts
from tests.test_billing import TEST, signed
from tests.test_social_login import SOCIAL
from tests.test_ui_studio import PASSWORD, csrf, settings

OPTS = {"eur": {"unit_amount": 2100}, "pln": {"unit_amount": 9500}}


def _price(pid, amount, count=1, once=False):
    body = {"id": pid, "object": "price", "unit_amount": amount, "currency": "gbp",
            "currency_options": {"gbp": {"unit_amount": amount}, **OPTS}}
    if once:
        return {**body, "type": "one_time", "recurring": None}
    return {**body, "type": "recurring",
            "recurring": {"interval": "month", "interval_count": count}}


PRICES = {"price_123": _price("price_123", 1800), "price_3m": _price("price_3m", 5400, 3),
          "price_6m": _price("price_6m", 9000, 6), "price_12m": _price("price_12m", 18000, 12),
          "price_once": _price("price_once", 1800, once=True)}
PLANS = {"STRIPE_PRICE_3M": "price_3m", "STRIPE_PRICE_6M": "price_6m",
         "STRIPE_PRICE_12M": "price_12m", "STRIPE_PRICE_ONCE": "price_once"}
FEE = {"STRIPE_SPEND_PRICE_ID": "price_spend"}
CFG = BillingConfig.from_env({**TEST, **PLANS})


class FakeStripe:
    """A MockTransport for Stripe: prices on GET, a Checkout Session or a meter event on
    POST."""

    def __init__(self, prices=None, status=200):
        self.prices, self.status, self.seen = dict(prices or PRICES), status, []

    def __call__(self, request):
        self.seen.append(request)
        if request.method == "GET":
            pid = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(self.status, json=self.prices.get(pid, {}))
        if request.url.path.endswith("/billing/meter_events"):
            return httpx.Response(200, json={"object": "billing.meter_event"})
        return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe.com/c/1"})

    def api(self, cfg=CFG):
        return StripeApi(cfg, transport=httpx.MockTransport(self))

    @property
    def gets(self):
        return [r for r in self.seen if r.method == "GET"]

    @property
    def posts(self):
        return [r for r in self.seen if r.method == "POST"]


def _app(tmp_path, fake=None, **env):
    app = create_app(settings(tmp_path, **env))
    if fake is not None:
        app.state.stripe = fake.api(app.state.settings.billing)
    return app


def _billing_app(tmp_path, fake, **env):
    app = _app(tmp_path, fake, **{**TEST, **PLANS, **env})
    app.state.accounts.create("ola", PASSWORD, email="ola@example.com")
    return app


# -- prices ----------------------------------------------------------------------------------

@pytest.mark.parametrize("amount,cur,lang,want", [
    (900, "gbp", "en", "£9"), (999, "gbp", "en", "£9.99"), (4500, "pln", "en", "45 zł"),
    (900, "gbp", "pl", "9 £"), (999, "eur", "pl", "9,99 €"),
    (1000, "jpy", "en", "1,000 JPY"), (150000, "usd", "en", "$1,500")])
def test_money(amount, cur, lang, want):
    assert pricing.money(amount, cur, lang) == want


def test_a_price_is_shown_only_for_the_plan_it_matches():
    assert pricing.parse(PRICES["price_123"]) == pricing.Price(
        1800, "gbp", 1, True, {"eur": 2100, "pln": 9500})
    assert pricing.parse(PRICES["price_3m"], "3m").months == 3
    assert pricing.parse(PRICES["price_3m"]) is None  # 3-monthly is not "a month"
    assert pricing.parse(PRICES["price_123"], "12m") is None
    assert pricing.parse(PRICES["price_once"], "once").recurring is False
    assert pricing.parse(PRICES["price_once"]) is None
    assert pricing.parse({**PRICES["price_123"], "unit_amount": None}) is None


def test_prices_are_read_once_per_plan_and_cached():
    fake, now = FakeStripe(), [1000.0]
    cache = pricing.PriceCache(fake.api, clock=lambda: now[0])
    assert cache.plans() == ["monthly", "3m", "6m", "12m", "once"]
    assert cache.amount("en") == "£18" and cache.amount("pl", "3m") == "54 £"
    assert cache.others("en") == ["€21", "95 zł"]
    assert cache.per_month("en", "6m") == "£15" and cache.per_month("en", "monthly") == ""
    assert [cache.free_months(p) for p in ("3m", "6m", "12m", "once")] == [0, 1, 2, 0]
    gets = len(fake.gets)
    assert gets == 5 and fake.gets[0].url.path == "/v1/prices/price_123"
    assert fake.gets[0].url.params.get("expand[]") == "currency_options"
    now[0] += pricing.TTL_S - 1
    cache.amount("en", "3m")
    assert len(fake.gets) == gets
    now[0] += 2
    cache.amount("en", "3m")
    assert len(fake.gets) == gets + 1


def test_stripe_errors_fall_back_and_are_retried_later():
    fake, now = FakeStripe(status=500), [1000.0]
    cache = pricing.PriceCache(fake.api, "£18 / month", clock=lambda: now[0])
    assert cache.amount("en") == "£18" and cache.others("en") == []
    assert cache.amount("en", "3m") == ""  # the fallback is for the monthly plan only
    cache.amount("en")
    assert len(fake.gets) == 2  # not hammered while Stripe is down
    fake.status = 200
    now[0] += pricing.RETRY_S + 1
    assert cache.others("en") == ["€21", "95 zł"]
    assert pricing.PriceCache(lambda: None).amount("en") == ""  # billing off, no fallback


def test_an_unreachable_stripe_falls_back():
    def down(request):
        raise httpx.ConnectError("down")
    api = StripeApi(CFG, transport=httpx.MockTransport(down))
    assert pricing.PriceCache(lambda: api, "9 zł na miesiąc").amount("pl") == "9 zł"


def test_fetch_false_never_calls_stripe():
    fake = FakeStripe()
    cache = pricing.PriceCache(fake.api, "£9")
    assert cache.amount("en", fetch=False) == "£9" and fake.seen == []


def test_config_of_plans_and_fee():
    assert CFG.plans == {"monthly": "price_123", "3m": "price_3m", "6m": "price_6m",
                         "12m": "price_12m", "once": "price_once"}
    assert BillingConfig.from_env(TEST).plans == {"monthly": "price_123"}
    assert not CFG.spend_fee  # no metered price: no fee anywhere
    fee = BillingConfig.from_env({**TEST, **FEE})
    assert fee.spend_fee and fee.fee_pct == Decimal(5) and fee.meter_event == "muse_ad_spend"
    assert not BillingConfig.from_env({**TEST, **FEE, "STUDIO_SPEND_FEE_PCT": "0"}).spend_fee
    assert BillingConfig.from_env({**TEST, **FEE,
                                   "STUDIO_SPEND_FEE_PCT": "2.5"}).fee_pct_text == "2.5"
    for bad in ({"STUDIO_SPEND_FEE_PCT": "x"}, {"STUDIO_SPEND_FEE_PCT": "80"},
                {"STRIPE_SPEND_PRICE_ID": "nope"}, {"STRIPE_PRICE_3M": "nope"}):
        with pytest.raises(ValueError):
            BillingConfig.from_env({**TEST, **bad})


# -- /pricing and /terms ---------------------------------------------------------------------

@pytest.mark.parametrize("lang", ["en", "pl"])
def test_pricing_without_billing_shows_no_amount(tmp_path, lang):
    with TestClient(_app(tmp_path), base_url="https://testserver") as c:
        r = c.get(f"/pricing?lang={lang}")
    assert r.status_code == 200
    cat = i18n.CATALOGS[lang]
    assert cat["pricing.title"] in r.text and cat["pricing.no_amount"] in r.text
    main = r.text.split("<main", 1)[1].split("</main>")[0]
    assert "£" not in main and "%" not in main  # no amount, no fee
    assert cat["pricing.promise.meta"] in r.text and "Dwarven Stronghold Ltd" in r.text
    assert 'href="/login?next=/account"' in r.text  # a visitor signs up first


def test_pricing_uses_the_display_fallback(tmp_path):
    with TestClient(_app(tmp_path, STUDIO_PRICE_DISPLAY="£9 / month",
                         STUDIO_FAIR_USE_RUNS="40"), base_url="https://testserver") as c:
        r = c.get("/pricing?lang=en")
    assert "£9 a month" in r.text and "up to 40 campaign runs" in r.text


def test_pricing_shows_every_plan_from_stripe_and_is_public(tmp_path):
    fake = FakeStripe()
    app = _app(tmp_path, fake, STUDIO_REQUIRE_LOGIN="1", **TEST, **PLANS)
    with TestClient(app, base_url="https://testserver") as c:
        en = c.get("/pricing?lang=en", follow_redirects=False)
        pl = c.get("/pricing?lang=pl").text
    assert en.status_code == 200
    for text in ("£18 a month", "£54 every 3 months", "£90 every 6 months",
                 "£180 every 12 months", "That is £15 a month.", "1 month free",
                 "2 months free", "£18 once, 30 days of access", "€21", "One month, paid once"):
        assert text in en.text, text
    assert "18 £ miesięcznie" in pl and "2 miesiące gratis" in pl
    assert "%" not in en.text.split("<main", 1)[1].split("</main>")[0]  # no fee configured
    assert len(fake.gets) == 5


def test_pricing_states_the_fee_when_it_is_charged(tmp_path):
    app = _app(tmp_path, FakeStripe(), **TEST, **FEE)
    with TestClient(app, base_url="https://testserver") as c:
        page = c.get("/pricing?lang=en").text
        terms = c.get("/terms?lang=en").text
    assert ("plus 5% of the ad spend of campaigns you sign through the studio, billed "
            "monthly in arrears; the ad spend itself is paid to Meta") in page
    assert "the 5% above is the only charge on your ad spend" in page
    assert "we charge 5% of the ad spend of the campaigns you sign" in terms
    assert "We take no commission" not in terms


def test_signed_in_cta_goes_to_billing(tmp_path):
    app = _billing_app(tmp_path, FakeStripe())
    with TestClient(app, base_url="https://testserver") as c:
        c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})
        assert 'href="/account#billing"' in c.get("/pricing").text


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_terms_fill_the_seller_and_leave_out_missing_lines(tmp_path, lang):
    with TestClient(_app(tmp_path, STUDIO_REQUIRE_LOGIN="1"),
                    base_url="https://testserver") as c:
        bare = c.get(f"/terms?lang={lang}", follow_redirects=False)
    with TestClient(_app(tmp_path, STUDIO_SELLER_NUMBER="12345678",
                         STUDIO_SELLER_ADDRESS="1 Example Street, London"),
                    base_url="https://testserver") as c:
        full = c.get(f"/terms?lang={lang}").text
    title, _ = legal_texts.page("terms", lang)
    assert bare.status_code == 200 and title in bare.text
    assert "Dwarven Stronghold Ltd" in bare.text and legal_texts.CONTACT in bare.text
    assert "12345678" not in bare.text and "{" not in bare.text.split("<main", 1)[1]
    assert "%" not in bare.text.split("<main", 1)[1].split("</main>")[0]  # no fee
    assert "12345678" in full and "1 Example Street, London" in full
    for anchor in ("withdrawal", "plans", "one-off", "buyers"):
        assert f'id="{anchor}"' in full
    assert '<a href="/privacy">' in full and legal_texts.UPDATED in full


def test_terms_anchors_match_the_sections():
    for name, anchors in legal_texts.ANCHORS.items():
        for lang in ("en", "pl"):
            assert len(legal_texts.PAGES[name][lang][1]) == len(anchors)


def test_footer_links_and_faq(tmp_path):
    with TestClient(_app(tmp_path), base_url="https://testserver") as c:
        page = c.get("/privacy?lang=pl").text
        faq = c.get("/help?lang=en").text
    assert 'href="/pricing"' in page and 'href="/terms"' in page
    assert i18n.PL["foot.terms"] in page
    assert any(e.anchor == "pricing" for e in help_texts.ENTRIES)
    assert 'id="pricing"' in faq and "How much does it cost?" in faq


def test_login_sign_up_box_shows_the_cached_price(tmp_path):
    app = _app(tmp_path, FakeStripe(), **{**TEST, **SOCIAL, **FEE})
    with TestClient(app, base_url="https://testserver") as c:
        html = c.get("/login?lang=en").text
    assert ('£18/month + 5% of ad spend after the trial — see <a href="/pricing">Pricing'
            "</a>") in html
    with TestClient(_app(tmp_path / "off", **SOCIAL), base_url="https://testserver") as c:
        assert "after the trial" not in c.get("/login?lang=en").text


# -- the consent texts -----------------------------------------------------------------------

def test_consent_sentence_per_plan_and_fee():
    fee = BillingConfig.from_env({**TEST, **FEE})
    s = routes_billing.consent_sentence
    assert s("en", "monthly", "£18", CFG) == (
        "I agree to a recurring payment of £18 per month, charged in advance until I "
        "cancel, and to the Terms.")
    assert s("en", "3m", "£54", CFG) == (
        "I agree to a payment of £54 every 3 months, charged in advance and renewing for "
        "the same period until I cancel, and to the Terms.")
    assert s("en", "once", "£18", CFG) == (
        "One payment of £18 for 30 days of access; it does not renew. I agree to the Terms.")
    assert "every 12 months" in s("en", "12m", "", CFG)  # no amount: still the period
    with_fee = s("en", "monthly", "£18", fee)
    assert ("plus 5% of the ad spend of campaigns I sign through the studio, billed monthly "
            "in arrears (the ad spend itself is paid to Meta)") in with_fee
    assert "%" not in s("en", "once", "£18", fee)  # the one-off month has no ad-spend fee
    assert "5%" in s("pl", "6m", "90 £", fee)


# -- checkout --------------------------------------------------------------------------------

def _signed_in(c):
    c.post("/login", data={"username": "ola", "password": PASSWORD, "csrf": csrf(c)})


def _checkout(c, **fields):
    return c.post("/account/billing/checkout?lang=en", data={"csrf": csrf(c), **fields},
                  follow_redirects=False)


def _consents(app):
    """The billing.consent receipts, each with its full, privately stored record as
    ``details``; the public receipt itself never names the account, buyer or amount."""
    from types import SimpleNamespace
    out = []
    bodies = {}
    for row in app.state.conn.execute("SELECT sha256, body FROM billing_consents"):
        bodies[row[0]] = json.loads(row[1])
    for x in app.state.ledger.receipts(billing.RUN_ID):
        if x.details.get("tool") != "billing.consent":
            continue
        assert not {"account_id", "buyer", "amount_minor", "price_id"} & set(x.details)
        body = bodies[x.details["consent_sha256"]]
        from studio.receipts import canonical, sha256_hex
        assert sha256_hex(canonical(body)) == x.details["consent_sha256"]  # salted record
        assert len(body["nonce"]) == 32
        assert x.details["plan"] == body["plan"]
        out.append(SimpleNamespace(agent=x.agent, kind=x.kind, details=body))
    return out


def test_account_billing_offers_the_plans_buyers_and_consents(tmp_path):
    app = _billing_app(tmp_path, FakeStripe())
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        page = c.get("/account?lang=en").text
    assert 'id="bp-monthly" name="plan" value="monthly" checked' in page
    for plan in ("3m", "6m", "12m", "once"):
        assert f'name="plan" value="{plan}"' in page
    assert "£54 every 3 months" in page and "(£15 a month)" in page
    assert 'name="buyer" value="person" required' in page
    assert 'name="recurring_consent" value="1" required' in page
    assert '<div data-buyer-only="person">' in page
    assert 'name="immediate_start" value="1" aria-describedby="bc-start-hint"' in page
    assert ('<span data-plan-text="3m" hidden>I agree to a payment of £54 every 3 months'
            in page)
    assert '<script src="/static/billing.js" defer></script>' in page


def test_buyer_type_is_prefilled_from_the_profile(tmp_path):
    app = _billing_app(tmp_path, FakeStripe())
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        c.post("/account/profile", data={"csrf": csrf(c), "advertiser_type": "company",
                                         "legal_name": "Ola Ltd", "country": "UK",
                                         "tax_id": "GB123456789"})
        page = c.get("/account?lang=en").text
    assert 'value="company" checked' in page and 'value="GB123456789"' in page


@pytest.mark.parametrize("fields,code", [
    ({"buyer": "person"}, "both"),
    ({"buyer": "person", "recurring_consent": "1"}, "immediate_start"),
    ({"buyer": "person", "immediate_start": "1"}, "recurring_consent"),
    ({"buyer": "company"}, "recurring_consent"),
    ({"buyer": "person", "recurring_consent": "on", "immediate_start": "1"},
     "recurring_consent"),
    ({"recurring_consent": "1", "immediate_start": "1"}, "buyer"),
    ({"buyer": "alien", "recurring_consent": "1", "immediate_start": "1"}, "buyer"),
    ({"plan": "bogus", "buyer": "person", "recurring_consent": "1",
      "immediate_start": "1"}, "plan")])
def test_checkout_without_what_the_buyer_must_give_is_refused(tmp_path, fields, code):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake)
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        r = _checkout(c, **fields)
    assert r.status_code == 400
    key = {"buyer": "billing.buyer.missing", "plan": "billing.plan.invalid"}.get(
        code, "billing.consent.missing_" + code)
    assert i18n.EN[key] in r.text and 'aria-invalid="true"' in r.text
    assert fake.posts == [] and _consents(app) == []


def test_an_unset_plan_is_refused(tmp_path):
    fake = FakeStripe()
    app = _app(tmp_path, fake, **TEST)  # only the monthly price is configured
    app.state.accounts.create("ola", PASSWORD, email="ola@example.com")
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        assert 'value="3m"' not in c.get("/account").text
        r = _checkout(c, plan="3m", buyer="person", recurring_consent="1", immediate_start="1")
    assert r.status_code == 400 and fake.posts == []


def test_monthly_checkout_records_the_consent_and_tells_stripe(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake)
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        c.get("/account?lang=en")
        r = _checkout(c, buyer="person", recurring_consent="1", immediate_start="1")
    assert r.status_code == 303 and r.headers["location"].startswith(
        "https://checkout.stripe.com/")
    (consent,) = _consents(app)
    d = consent.details
    assert consent.agent == "owner" and consent.kind == "tool_call"
    assert str(d["terms_updated"]) == legal_texts.UPDATED.replace("-", "")
    assert d["terms_sha256"] == legal_texts.digest("terms") and d["price_id"] == "price_123"
    assert d["plan"] == "monthly" and d["buyer"] == "person" and d["interval_count"] == 1
    assert d["recurring_consent"] is True and d["immediate_start"] is True
    assert "ola@example.com" not in str(d)
    form = parse_qs(fake.posts[0].content.decode())
    assert form["mode"] == ["subscription"] and form["line_items[0][price]"] == ["price_123"]
    assert "line_items[1][price]" not in form  # no fee configured
    assert form["subscription_data[description]"] == [
        "Muse Studio Lite — monthly subscription"]
    assert form["custom_text[submit][message]"] == [
        "I agree to a recurring payment of £18 per month, charged in advance until I "
        "cancel, and to the Terms."]
    assert form["subscription_data[metadata][account_id]"] == [d["account_id"]]
    assert form["metadata[plan]"] == ["monthly"] and "tax_id_collection[enabled]" not in form


@pytest.mark.parametrize("plan,price,months", [("3m", "price_3m", 3), ("6m", "price_6m", 6),
                                               ("12m", "price_12m", 12)])
def test_a_term_plan_checks_out_its_own_price(tmp_path, plan, price, months):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake)
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        c.get("/account?lang=en")
        assert _checkout(c, plan=plan, buyer="person", recurring_consent="1",
                         immediate_start="1").status_code == 303
    form = parse_qs(fake.posts[0].content.decode())
    assert form["line_items[0][price]"] == [price] and form["mode"] == ["subscription"]
    assert f"every {months} months, charged in advance and renewing for the same period" in \
        form["custom_text[submit][message]"][0]
    assert _consents(app)[0].details["interval_count"] == months


def test_the_fee_adds_the_metered_item_without_quantity(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake, **FEE)
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        page = c.get("/account?lang=en").text
        _checkout(c, buyer="person", recurring_consent="1", immediate_start="1")
    assert "plus 5% of the ad spend of campaigns I sign through the studio" in page
    form = parse_qs(fake.posts[0].content.decode())
    assert form["line_items[1][price]"] == ["price_spend"]
    assert "line_items[1][quantity]" not in form and form["line_items[0][quantity]"] == ["1"]
    assert "plus 5% of the ad spend" in form["custom_text[submit][message]"][0]
    assert _consents(app)[0].details["spend_fee_pct"] == "5"


def test_a_company_needs_no_withdrawal_box_and_gets_tax_id_collection(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake)
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        r = _checkout(c, buyer="company", tax_id="GB123456789", recurring_consent="1")
    assert r.status_code == 303
    form = parse_qs(fake.posts[0].content.decode())
    assert form["tax_id_collection[enabled]"] == ["true"]
    assert form["billing_address_collection"] == ["required"]
    assert form["metadata[buyer]"] == ["company"]
    assert form["metadata[buyer_tax_id]"] == ["GB123456789"]
    d = _consents(app)[0].details
    assert d["buyer"] == "company" and d["immediate_start"] is False


def test_the_one_off_month_is_a_payment_without_renewal(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake, **FEE)
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        c.get("/account?lang=en")
        assert _checkout(c, plan="once", buyer="person", recurring_consent="1",
                         immediate_start="1").status_code == 303
    form = parse_qs(fake.posts[0].content.decode())
    assert form["mode"] == ["payment"] and form["line_items[0][price]"] == ["price_once"]
    assert not [k for k in form if k.startswith("subscription_data")]
    assert "line_items[1][price]" not in form  # the metered fee rides on subscriptions only
    assert form["invoice_creation[enabled]"] == ["true"]
    assert form["customer_creation"] == ["always"]
    assert form["custom_text[submit][message]"][0].startswith(
        "One payment of £18 for 30 days of access; it does not renew")
    d = _consents(app)[0].details
    assert d["plan"] == "once" and d["interval_count"] == 0 and d["price_id"] == "price_once"


# -- webhooks: one-off access and the renewal reminder --------------------------------------

def _hook(c, kind, obj, eid, created=None):
    body = json.dumps({"id": eid, "object": "event", "type": kind, "livemode": False,
                       "created": created or int(time.time()),
                       "data": {"object": obj}}).encode()
    return c.post("/webhooks/stripe", content=body,
                  headers={"Stripe-Signature": signed(body),
                           "Content-Type": "application/json"})


def test_a_paid_one_off_session_stores_the_access_end(tmp_path):
    app = _billing_app(tmp_path, FakeStripe())
    aid =app.state.conn.execute("SELECT id FROM accounts WHERE username = 'ola'"
                                 ).fetchone()[0]
    paid = int(time.time())
    with TestClient(app, base_url="https://testserver") as c:
        r = _hook(c, "checkout.session.completed", {
            "id": "cs_1", "mode": "payment", "payment_status": "paid", "customer": "cus_9",
            "client_reference_id": aid, "metadata": {"account_id": aid, "plan": "once"}},
            "evt_once", created=paid)
        assert r.status_code == 200
        _signed_in(c)
        page = c.get("/account?lang=en").text
    row = billing.of(app.state.conn, aid)
    assert row.access_until == paid + 30 * 86400 and row.customer_id == "cus_9"
    assert row.status == "" and app.state.accounts.get(aid).tier == "full"  # unchanged
    assert "paid once, access until" in page


def test_unpaid_one_off_session_grants_nothing(tmp_path):
    app = _billing_app(tmp_path, FakeStripe())
    aid = app.state.conn.execute("SELECT id FROM accounts WHERE username = 'ola'"
                                 ).fetchone()[0]
    with TestClient(app, base_url="https://testserver") as c:
        _hook(c, "checkout.session.completed", {
            "id": "cs_1", "mode": "payment", "payment_status": "unpaid", "customer": "cus_9",
            "metadata": {"account_id": aid}}, "evt_x")
    row = billing.of(app.state.conn, aid)
    assert row is None or row.access_until == 0


def _upcoming(aid, price, count, when=1_800_000_000):
    return {"object": "invoice", "customer": "cus_1", "subscription": "sub_1",
            "next_payment_attempt": when,
            "subscription_details": {"metadata": {"account_id": aid}},
            "lines": {"data": [{"price": {"id": price, "recurring": {
                "interval": "month", "interval_count": count}}}]}}


def test_renewal_reminder_only_for_long_plans_and_once_per_renewal(tmp_path):
    app = _billing_app(tmp_path, FakeStripe(), STUDIO_MAIL_TRANSPORT="mock",
                       STUDIO_MAIL_FROM="Muse <muse@example.org>")
    aid = app.state.conn.execute("SELECT id FROM accounts WHERE username = 'ola'"
                                 ).fetchone()[0]
    with TestClient(app, base_url="https://testserver") as c:
        _hook(c, "invoice.upcoming", _upcoming(aid, "price_123", 1), "e1")
        _hook(c, "invoice.upcoming", _upcoming(aid, "price_3m", 3), "e2")
        _hook(c, "invoice.upcoming", _upcoming(aid, "price_3m", 3), "e3")  # same renewal
        newer = _upcoming(aid, "price_12m", 12, when=1_900_000_000)
        newer["lines"]["data"][0]["price"] = "price_12m"  # an id only: months from config
        _hook(c, "invoice.upcoming", newer, "e4")
        app.state.notifier.flush()
    subjects = [m.subject for m in app.state.notifier.outbox]
    assert subjects.count("Your Muse Studio Lite plan renews soon") == 2


# -- the ad-spend fee usage report -----------------------------------------------------------

def _seed_spend(app):
    """Two accounts: ola (Stripe customer, active subscription) signed c1 and c2; bob (no
    customer) signed c3. Each signature: approval → key → session → account."""
    conn = app.state.conn
    store = app.state.accounts
    ola = conn.execute("SELECT id FROM accounts WHERE username = 'ola'").fetchone()[0]
    bob = store.create("bob", PASSWORD, email="bob@example.com").id
    now = time.time()
    with conn.lock:
        for n, (aid, cids) in enumerate(((ola, ("c1", "c2")), (bob, ("c3",)))):
            conn.execute("INSERT INTO account_sessions (sid_sha256, account_id, created_ts, "
                         "last_seen_ts, expires_ts) VALUES (?, ?, ?, ?, ?)",
                         (f"s{n}", aid, now, now, int(now) + 3600))
            conn.execute("INSERT INTO keys (approver, public_key, session_sha256, "
                         "created_ts, expires_ts) VALUES (?, ?, ?, ?, ?)",
                         (f"ed25519:k{n}", bytes(32), f"s{n}", int(now), int(now) + 3600))
            for cid in cids:
                conn.execute("INSERT INTO campaigns (id, name, objective, "
                             "daily_budget_minor, adapter, created_ts, currency) VALUES "
                             "(?, 'x', 'traffic', 1000, 'mock', ?, 'GBP')", (cid, now))
                conn.execute("INSERT INTO approvals (id, run_id, action, item_id, "
                             "content_sha256, chain_head, nonce, ts, status, approver, "
                             "decided_ts) VALUES (?, 'r', 'campaign.create', ?, 'h', 'g', "
                             "?, ?, 'approved', ?, ?)",
                             (f"ap_{cid}", cid, f"n_{cid}", int(now), f"ed25519:k{n}",
                              int(now)))
        snaps = [("c1", {"2026-10-29": 10.0}, now - 100),
                 ("c1", {"2026-10-29": 12.34, "2026-10-28": 5.0}, now - 10),
                 ("c2", {"2026-10-29": 0.66}, now - 10),
                 ("c3", {"2026-10-29": 50.0}, now - 10)]
        for i, (cid, days, ts) in enumerate(snaps):
            conn.execute("INSERT INTO insights_snapshots (id, campaign_id, body, created_ts) "
                         "VALUES (?, ?, ?, ?)",
                         (f"sn{i}", cid, json.dumps({"spend_by_day": days}), ts))
        conn.execute("INSERT INTO billing (account_id, customer_id, status, updated_ts) "
                     "VALUES (?, 'cus_1', 'active', ?)", (ola, now))
    return ola, bob


def test_report_spend_sends_one_fee_event_per_account_and_day(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake, **FEE)
    ola, bob = _seed_spend(app)
    api = fake.api(app.state.settings.billing)
    out = billing.report_spend(app.state.conn, api, "2026-10-29", ledger=app.state.ledger)
    assert out == {"accounts": 2, "sent": 1, "skipped": 1}
    (event,) = [r for r in fake.posts if r.url.path == "/v1/billing/meter_events"]
    form = parse_qs(event.content.decode())
    # latest snapshot per campaign (12.34, not 10 + 12.34) + 0.66 = 13.00 → fee 5 % = 65
    assert form == {"event_name": ["muse_ad_spend"], "identifier": [f"{ola}:2026-10-29"],
                    "payload[stripe_customer_id]": ["cus_1"], "payload[value]": ["65"]}
    assert event.headers["idempotency-key"] == f"{ola}:2026-10-29"
    usage = [r.details for r in app.state.ledger.receipts()
             if r.details.get("tool") == "billing.usage"]
    # the public receipts carry no account and no amounts, only the record's hash
    assert all(not {"account_id", "spend_minor", "fee_minor", "currency"} & set(u)
               for u in usage)
    nonce = app.state.conn.execute("SELECT nonce FROM spend_usage WHERE account_id = ?",
                                   (ola,)).fetchone()[0]
    sent = {"nonce": nonce, "account_id": ola, "day": "2026-10-29", "campaigns": 2,
            "spend_minor": 1300, "currency": "GBP", "fee_pct": "5", "fee_minor": 65}
    by_hash = {u["usage_sha256"]: u for u in usage}
    assert by_hash[billing.usage_sha256(sent)]["status"] == "sent"
    assert by_hash[billing.usage_sha256(sent)]["day"] == 20261029
    assert sorted(u["status"] for u in usage) == ["no_customer", "sent"]
    assert app.state.conn.execute("SELECT spend_minor, fee_minor, currency FROM spend_usage "
                                  "WHERE account_id = ?", (ola,)).fetchone() == (1300, 65, "GBP")
    again = billing.report_spend(app.state.conn, api, "2026-10-29", ledger=app.state.ledger)
    assert again["sent"] == 0  # a rerun sends nothing new
    assert len([r for r in fake.posts if r.url.path == "/v1/billing/meter_events"]) == 1
    assert billing.report_spend(app.state.conn, api, "2026-10-27",
                                ledger=app.state.ledger)["accounts"] == 0


def test_report_spend_does_nothing_without_a_fee_and_refuses_an_unallowed_live_key(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake)
    _seed_spend(app)
    assert billing.report_spend(app.state.conn, fake.api(app.state.settings.billing),
                                "2026-10-29", ledger=app.state.ledger)["sent"] == 0
    live = BillingConfig(secret_key="sk_live_" + "a" * 24, webhook_secret="whsec_x",
                         price_id="price_123", spend_price_id="price_spend")
    with pytest.raises(billing.BillingError, match="STUDIO_BILLING_LIVE"):
        billing.report_spend(app.state.conn, fake.api(live), "2026-10-29",
                             ledger=app.state.ledger)
    with pytest.raises(billing.BillingError):
        fake.api(live).meter_event(customer_id="cus_1", value=1, identifier="x")
    assert fake.posts == []
    with pytest.raises(ValueError):
        billing.report_spend(app.state.conn, fake.api(live), "yesterday",
                             ledger=app.state.ledger)


def test_fee_rounds_half_up():
    assert billing.fee_of(1300, Decimal(5)) == 65
    assert billing.fee_of(10, Decimal(5)) == 1  # 0.5 → 1
    assert billing.fee_of(9, Decimal(5)) == 0
    assert billing.fee_of(1000, Decimal("2.5")) == 25


def test_snapshots_store_the_daily_spend():
    days = measurement.spend_by_day(MockAdapter(), "120200000000000101")
    assert days["2026-10-19"] == 27.58 and len(days) >= 7


def test_cli_report_usage_without_fee_is_a_no_op(tmp_path, capsys):
    from studio import cli

    env = tmp_path / "env"
    env.write_text("".join(f"{k}={v}\n" for k, v in {
        **TEST, "STUDIO_DATA_DIR": str(tmp_path / "var"), "JUDGE_PASSWORD": PASSWORD,
        "SESSION_SECRET": secrets.token_urlsafe(32)}.items()))
    env.chmod(0o600)
    assert cli.main(["billing", "report-usage", "--env-file", str(env)]) == 0
    assert "spend fee off" in capsys.readouterr().out


def test_a_failed_meter_event_leaves_no_record_so_the_next_run_retries(tmp_path):
    fake = FakeStripe()
    app = _billing_app(tmp_path, fake, **FEE)
    ola, _ = _seed_spend(app)
    api = fake.api(app.state.settings.billing)

    def down(**kw):
        raise billing.BillingError("stripe_unreachable")

    real, api.meter_event = api.meter_event, down
    with pytest.raises(billing.BillingError):
        billing.report_spend(app.state.conn, api, "2026-10-29", ledger=app.state.ledger)
    assert app.state.conn.execute("SELECT COUNT(*) FROM spend_usage").fetchone() == (0,)
    api.meter_event = real
    out = billing.report_spend(app.state.conn, api, "2026-10-29", ledger=app.state.ledger)
    assert out["sent"] == 1


def test_checkout_needs_an_email_address_first(tmp_path):
    """The renewal reminder and notice of changed terms go by e-mail."""
    fake = FakeStripe()
    app = _app(tmp_path, fake, **TEST)
    app.state.accounts.create("ola", PASSWORD)  # no e-mail
    with TestClient(app, base_url="https://testserver") as c:
        _signed_in(c)
        r = _checkout(c, buyer="person", recurring_consent="1", immediate_start="1")
    assert r.status_code == 400 and i18n.EN["billing.email.missing"] in r.text
    assert fake.posts == [] and _consents(app) == []
