"""Test helpers for the Google Search channel: a valid spec/payload and a database."""

from studio import db
from studio.google.adapter import MOCK_CUSTOMER, build_spec
from studio.google.mock import MockAdapter

RUN = "run_gads1"


def spec(**over):
    kw = {"item_id": "gcmp_aaaaaa111111", "customer_id": MOCK_CUSTOMER, "currency": "GBP",
          "display_name": "Slow Ceramics", "product": "handmade mug",
          "offer": "Free delivery over 40 GBP", "proposition": "A mug that slows the morning.",
          "countries": ["GB"], "locations": ["London", "Bristol"],
          "link": "https://example.com/mug",
          "variants": [{"headline": "Slow mornings start here", "description":
                        "Handmade stoneware mugs, thrown in small batches.",
                        "primary_text": "A mug for slow mornings."},
                       {"headline": "Hand-thrown stoneware", "description":
                        "Each mug is shaped by hand and fired twice.", "primary_text": ""}],
          "daily_budget": 6}
    kw.update(over)
    return build_spec(**kw)


def payload(**over):
    p = {"action": "google.create", "run_id": RUN, "google": spec(),
         "link": "https://example.com/mug"}
    p.update(over)
    return p


def make_env(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    ledger.append(RUN, agent="producer", kind="model_call")
    return conn, ledger, MockAdapter(), tmp_path
