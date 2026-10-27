"""PII detection and tier routing (D2-2)."""

import pytest

from studio.pii import CONTRIBUTOR_MODEL, STANDARD_MODEL, choose_tier, contains_pii

# Checksums: NIP 5260250274 (weights 6,5,7,2,3,4,5,6,7), PESEL 44051401359 / 02270803624.
CASES = [
    # (text, expected kind or None)
    ("Write me copy for a sourdough bakery.", None),
    ("Mail me at anna.nowak@example.com please", "email"),
    ("kontakt: Jan.Kowalski+ads@firma.pl", "email"),
    ("call 600 100 200 tomorrow", "phone"),
    ("tel. 600-100-200", "phone"),
    ("numer 600100200", "phone"),
    ("+48 600 100 200", "phone"),
    ("0048 600 100 200", "phone"),
    ("biuro (12) 345 67 89", "phone"),
    ("biuro 12 345 67 89", "phone"),
    ("UK mobile +44 7700 900123", "phone"),
    ("ring 07700 900123", "phone"),
    ("London +44 20 7946 0958", "phone"),
    ("office 020 7946 0958", "phone"),
    ("NIP 526-025-02-74", "nip"),
    ("NIP: 5260250274", "nip"),
    ("PESEL 44051401359", "pesel"),
    ("pesel 02270803624", "pesel"),
    ("NIP 5260250275 has a bad checksum", None),
    ("PESEL 44051401358 has a bad checksum", None),
    ("Price 89 PLN, free delivery above 150 PLN", None),
    ("Budget 30 PLN per day, 25-55 within 15 km", None),
    ("Campaign 2026-10-27 at 21:03", None),
    ("Order no. 1234567", None),
    ("1 000 000 impressions", None),
]


def test_table_has_25_cases():
    assert len(CASES) == 25


@pytest.mark.parametrize(("text", "kind"), CASES)
def test_contains_pii(text, kind):
    match = contains_pii(text)
    if kind is None:
        assert match is None, match
    else:
        assert match is not None and match.kind == kind
        assert 0 <= match.start < match.end <= len(text)


def test_contributor_is_never_selected_for_pii():
    for text, kind in CASES:
        tier, reason, model = choose_tier(text, "contributor")
        if kind:
            assert (tier, model) == ("standard", STANDARD_MODEL)
            assert kind in reason
            assert text not in reason  # the reason never quotes the PII
        else:
            assert (tier, model) == ("contributor", CONTRIBUTOR_MODEL)


def test_standard_requested_stays_standard():
    assert choose_tier("hello", "standard")[::2] == ("standard", STANDARD_MODEL)


@pytest.mark.parametrize("source", ["comment", "lead", "transcript"])
def test_customer_text_never_goes_to_contributor(source):
    tier, reason, _ = choose_tier("no pii here", "contributor", source=source)
    assert tier == "standard" and source in reason


def test_unknown_tier_refused():
    with pytest.raises(ValueError):
        choose_tier("x", "local")
