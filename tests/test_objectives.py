"""The objective table (``studio.meta.objectives``): one test per row, plus the gate both
adapters run before any Meta call."""

import pytest

from studio.meta import objectives as ob

PAGE = "600000000000001"
IDS = {"pixel_id": "700000000000001", "application_id": "800000000000001",
       "object_store_url": "https://play.google.com/store/apps/details?id=demo.app",
       "event_id": "900000000000001", "product_catalog_id": "910000000000001",
       "product_set_id": "920000000000001",
       "privacy_policy_url": "https://example.com/privacy"}

# (goal, sub) → (objective, optimization_goal, destination_type, promoted_object keys,
#                creative, cta, result)
EXPECTED = {
    ("awareness", "reach"): ("OUTCOME_AWARENESS", "REACH", "", {"page_id"}, "link", "",
                             "reach"),
    ("awareness", "ad_recall_lift"): ("OUTCOME_AWARENESS", "AD_RECALL_LIFT", "", {"page_id"},
                                      "link", "", "estimated_ad_recallers"),
    ("traffic", "link_clicks"): ("OUTCOME_TRAFFIC", "LINK_CLICKS", "WEBSITE", set(), "link",
                                 "", "link_click"),
    ("traffic", "landing_page_views"): ("OUTCOME_TRAFFIC", "LANDING_PAGE_VIEWS", "WEBSITE",
                                        set(), "link", "", "landing_page_view"),
    ("engagement", "post_engagement"): ("OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT", "ON_POST",
                                        set(), "link", "", "post_engagement"),
    ("engagement", "messenger"): ("OUTCOME_ENGAGEMENT", "CONVERSATIONS", "MESSENGER",
                                  {"page_id"}, "message", "MESSAGE_PAGE",
                                  "onsite_conversion.messaging_conversation_started_7d"),
    ("engagement", "instagram_direct"): ("OUTCOME_ENGAGEMENT", "CONVERSATIONS",
                                         "INSTAGRAM_DIRECT", {"page_id"}, "message",
                                         "INSTAGRAM_MESSAGE",
                                         "onsite_conversion.messaging_conversation_started_7d"),
    ("engagement", "whatsapp"): ("OUTCOME_ENGAGEMENT", "CONVERSATIONS", "WHATSAPP",
                                 {"page_id"}, "message", "WHATSAPP_MESSAGE",
                                 "onsite_conversion.messaging_conversation_started_7d"),
    ("engagement", "event_responses"): ("OUTCOME_ENGAGEMENT", "EVENT_RESPONSES", "ON_EVENT",
                                        set(), "event", "", "rsvp"),
    ("leads", "instant_form"): ("OUTCOME_LEADS", "LEAD_GENERATION", "ON_AD", {"page_id"},
                                "lead_form", "SIGN_UP", "lead"),
    ("leads", "website"): ("OUTCOME_LEADS", "OFFSITE_CONVERSIONS", "WEBSITE",
                           {"pixel_id", "custom_event_type"}, "link", "",
                           "offsite_conversion.fb_pixel_lead"),
    ("app_promotion", "app_installs"): ("OUTCOME_APP_PROMOTION", "APP_INSTALLS", "",
                                        {"application_id", "object_store_url"}, "app",
                                        "INSTALL_MOBILE_APP", "mobile_app_install"),
    ("sales", "website"): ("OUTCOME_SALES", "OFFSITE_CONVERSIONS", "WEBSITE",
                           {"pixel_id", "custom_event_type"}, "link", "",
                           "offsite_conversion.fb_pixel_purchase"),
    ("sales", "catalog"): ("OUTCOME_SALES", "OFFSITE_CONVERSIONS", "WEBSITE",
                           {"pixel_id", "custom_event_type", "product_set_id"}, "catalog", "",
                           "offsite_conversion.fb_pixel_purchase"),
}


def test_the_table_has_six_objectives_and_every_row_is_tested():
    assert set(ob.TABLE) == set(EXPECTED)
    assert {r.objective for r in ob.TABLE.values()} == {
        "OUTCOME_AWARENESS", "OUTCOME_TRAFFIC", "OUTCOME_ENGAGEMENT", "OUTCOME_LEADS",
        "OUTCOME_APP_PROMOTION", "OUTCOME_SALES"}
    assert ob.GOALS == tuple(ob.SUBS)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_row(key):
    goal, sub = key
    objective, opt, dest, promoted, creative, cta, result = EXPECTED[key]
    spec = ob.delivery(goal, sub, page_id=PAGE, promotion=IDS)
    assert (spec["objective"], spec["optimization_goal"], spec["destination_type"]) == (
        objective, opt, dest)
    assert set(spec["promoted_object"]) == promoted
    assert all(spec["promoted_object"].values())
    assert (spec["creative"], spec["cta"], spec["result"]) == (creative, cta, result)
    assert spec["billing_event"] == "IMPRESSIONS"
    if "page_id" in promoted:
        assert spec["promoted_object"]["page_id"] == PAGE
    # what the adapters accept is exactly what the table makes
    assert ob.check(spec, PAGE) == spec


def test_defaults_and_fixed_values():
    assert ob.row("traffic").sub == "link_clicks"
    assert ob.row("leads").sub == "instant_form"
    sales = ob.delivery("sales", page_id=PAGE, promotion={"pixel_id": "1"})
    assert sales["promoted_object"] == {"pixel_id": "1", "custom_event_type": "PURCHASE"}
    assert sales["attribution_spec"] == [{"event_type": "CLICK_THROUGH", "window_days": 7},
                                         {"event_type": "VIEW_THROUGH", "window_days": 1}]
    assert sales["dynamic_creative"] is True
    lead = ob.delivery("leads", "website", page_id=PAGE,
                       promotion={"pixel_id": "1", "custom_event_type": "CONTACT"})
    assert lead["promoted_object"]["custom_event_type"] == "CONTACT"
    form = ob.delivery("leads", page_id=PAGE, promotion={
        "privacy_policy_url": "https://example.com/p"})
    assert form["lead_fields"] == ["FULL_NAME", "EMAIL"] and "attribution_spec" not in form
    catalog = ob.delivery("sales", "catalog", page_id=PAGE, promotion=IDS)
    assert catalog["campaign_promoted_object"] == {"product_catalog_id": "910000000000001"}
    traffic = ob.delivery(page_id=PAGE)
    assert traffic["objective"] == "OUTCOME_TRAFFIC" and not traffic["dynamic_creative"]


@pytest.mark.parametrize(("goal", "sub", "field"), [
    ("leads", "instant_form", "privacy_policy_url"), ("leads", "website", "pixel_id"),
    ("sales", "website", "pixel_id"), ("sales", "catalog", "product_set_id"),
    ("sales", "catalog", "product_catalog_id"), ("app_promotion", "", "application_id"),
    ("app_promotion", "", "object_store_url"), ("engagement", "event_responses", "event_id")])
def test_missing_ids_are_errors_never_guesses(goal, sub, field):
    ids = {k: v for k, v in IDS.items() if k != field}
    assert ob.id_problems(goal, sub, ids) == {f"promotion.{field}": "required_for_objective"}
    with pytest.raises(ob.DeliveryError):
        ob.delivery(goal, sub, page_id=PAGE, promotion=ids)


def test_malformed_ids_and_choices():
    assert ob.id_problems("sales", "", {"pixel_id": "px-1"}) == {
        "promotion.pixel_id": "bad_id"}
    assert ob.id_problems("app_promotion", "", {**IDS, "object_store_url":
                                                "https://evil.example/app"}) == {
        "promotion.object_store_url": "bad_store_url"}
    assert ob.id_problems("sales", "", {**IDS, "custom_event_type": "LEAD"}) == {
        "promotion.custom_event_type": "bad_event"}
    assert ob.id_problems("traffic", "messenger", {}) == {"objective.sub": "bad_sub"}
    assert ob.id_problems("leads", "", {**IDS, "lead_fields": ["EMAIL", "SSN"]}) == {
        "promotion.lead_fields": "bad_lead_fields"}


@pytest.mark.parametrize("edit", [
    lambda s: s.update(objective="OUTCOME_TRAFFIC"),
    lambda s: s.update(optimization_goal="REACH"),
    lambda s: s.update(destination_type="MESSENGER"),
    lambda s: s["promoted_object"].update(custom_event_type="ADD_TO_CART_X"),
    lambda s: s["promoted_object"].pop("pixel_id"),
    lambda s: s["promoted_object"].update(application_id="1"),
    lambda s: s.update(billing_event="LINK_CLICKS"),
    lambda s: s.pop("attribution_spec"),
    lambda s: s.update(sub="catalog"),
])
def test_check_refuses_any_hand_edited_mapping(edit):
    spec = ob.delivery("sales", page_id=PAGE, promotion=IDS)
    edit(spec)
    with pytest.raises(ob.DeliveryError):
        ob.check(spec, PAGE)


def test_check_refuses_another_page_and_a_missing_block():
    spec = ob.delivery("leads", page_id=PAGE, promotion=IDS)
    with pytest.raises(ob.DeliveryError):
        ob.check(spec, "600000000000999")
    with pytest.raises(ob.DeliveryError):
        ob.check(None)
    assert ob.label(spec) == "OUTCOME_LEADS · LEAD_GENERATION · ON_AD"
