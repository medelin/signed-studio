"""PII detection and tier routing (D2-2)."""

import pytest

from studio.pii import (
    CONTRIBUTOR_MODEL,
    STANDARD_MODEL,
    choose_tier,
    contains_pii,
    find_phone,
    long_digit_run,
    redact,
    redact_digit_runs,
)

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
    # not a PESEL, but 44 + 9 digits reads as a UK number without "+": detected (fail closed)
    ("PESEL 44051401358 has a bad checksum", "phone"),
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
        elif long_digit_run(text):  # e.g. a NIP with a bad checksum: still 10 digits
            assert (tier, reason, model) == ("standard", "long digit run", STANDARD_MODEL)
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


# Day 2 fix round (I5): a phone number next to another number is still a phone number.
ADJACENT = [
    ("Zadzwoń 600 123 456 89 PLN za kubek", "600 123 456"),
    ("tel. 600 123 456 2026", "600 123 456"),
    ("rok 2026 600 123 456", "600 123 456"),
    ("stacjonarny 22 123 45 67 89 zł", "22 123 45 67"),
    ("+48 600 123 456 2026", "+48 600 123 456"),
    ("600-123-456 15", "600-123-456"),
]


@pytest.mark.parametrize(("text", "phone"), ADJACENT)
def test_phone_next_to_another_number_is_found(text, phone):
    m = contains_pii(text)
    assert m is not None and m.kind == "phone", text
    assert choose_tier(text, "contributor")[0] == "standard"
    assert find_phone(text) == phone


@pytest.mark.parametrize("text", ["89 PLN, 350 ml, 2026", "Order 1234 5678 9012 3456",
                                  "12 345 678", "350 ml 89 PLN 2026"])
def test_numbers_that_are_not_phone_groupings_stay_clean(text):
    assert contains_pii(text) is None


# Controller ruling (fail closed): every contiguous group sub-sequence is classified, and
# any run of 9+ digits keeps the text off the Contributor tier whatever it is.
SUBSEQUENCES = [
    ("PESEL 44051401359 1500 PLN", "pesel"),
    ("NIP 5260250274 2026", "nip"),
    ("rok 2026 526 025 02 74", "nip"),
    ("UK 07700 900123 45 GBP", "phone"),
    ("cena 12 07700 900123", "phone"),
    ("kontakt +48600123456", "phone"),
    ("kontakt +48600123456 2026", "phone"),
    ("kontakt 00 48 600 123 456", "phone"),
    ("kontakt 0048 600 123 456 15", "phone"),
    ("kontakt 48 600 123 456", "phone"),
    ("tel 600/123/456", "phone"),
]


@pytest.mark.parametrize(("text", "kind"), SUBSEQUENCES)
def test_every_group_subsequence_is_classified(text, kind):
    m = contains_pii(text)
    assert m is not None and m.kind == kind, (text, m)
    assert choose_tier(text, "contributor")[0] == "standard"


@pytest.mark.parametrize("text", ["Order 1234 5678 9012 3456", "ref 123.456.789.0",
                                  "kod 9876-5432-1", "(123) 456 7890 11"])
def test_any_long_digit_run_forces_standard(text):
    tier, reason, model = choose_tier(text, "contributor")
    assert tier == "standard" and model == STANDARD_MODEL
    assert reason in ("long digit run",) or reason.startswith("pii detected")


@pytest.mark.parametrize("text", ["89 PLN, 350 ml", "Promocja do 2026-10-31", "12 345 678 zł",
                                  "1 500 PLN od 150 zł", "rok 2026, 24/7"])
def test_short_numbers_stay_on_contributor(text):
    assert choose_tier(text, "contributor")[0] == "contributor", text


# Controller ruling: Unicode digits and separators, and a 9-digits-in-20-characters window.
UNICODE_RUNS = [
    "tel. 600‑123‑456",                    # non-breaking hyphen
    "tel. 600−123−456",                    # minus sign
    "tel. 600 123 456",                    # narrow no-break space
    "tel. 600 123 456",                    # no-break space
    "tel. 600 123 456",                   # thin space
    "tel. 600—123—456",                    # em dash
    "tel. ６００１２３４５６",  # fullwidth digits
    "tel. ٦٠٠١٢٣٤٥٦",  # Arabic-Indic digits
    "tel. 6_0_0 1_2_3 4_5_6",                        # any non-digit filler
]


@pytest.mark.parametrize("text", UNICODE_RUNS)
def test_unicode_digit_runs_are_routed_to_standard(text):
    tier, reason, model = choose_tier(text, "contributor")
    assert (tier, model) == ("standard", STANDARD_MODEL), text


@pytest.mark.parametrize("text", UNICODE_RUNS[:8])
def test_unicode_phone_forms_are_detected(text):
    m = contains_pii(text)
    assert m is not None and m.kind == "phone", text


def test_window_is_nine_digits_in_twenty_characters():
    assert long_digit_run("1a2b3c4d5e6f7g8h9")  # 9 digits in 17 characters
    assert not long_digit_run("1 2 3 4 5 6 7 8 abcdefghijklmn 9")  # 9th digit too far
    assert not long_digit_run("rok 2026, 89 PLN")


def test_redaction_uses_the_same_window():
    from studio.receipts import redact

    for text in UNICODE_RUNS[:8]:
        assert redact(text) != text and "[phone]" in redact(text), text
    digest = "3f9a" + "1234567890" + "b" * 50
    assert redact(digest) == digest  # a hex digest is one token with letters: kept
    assert redact("run_3fa9c2b1d4e5f607") == "run_3fa9c2b1d4e5f607"


# Controller ruling: invisible characters and padding cannot stretch the window.
@pytest.mark.parametrize("sep", ["\u200b", "\u2060", "\u00ad", "\u0301", " " * 30,
                                 " .,;-/ " * 5, "\u200b\u0301 \u00ad"])
def test_padding_between_digits_cannot_stretch_the_window(sep):
    text = "tel " + sep.join("600123456")
    assert long_digit_run(text), repr(sep)
    assert choose_tier(text, "contributor")[0] == "standard"


@pytest.mark.parametrize("text", ["Kubek za 89 PLN, oferta do końca 2026 roku.",
                                  "Od 150 zł dostawa gratis, 350 ml, rocznik 2026."])
def test_a_sentence_with_a_price_and_a_year_stays_on_contributor(text):
    assert choose_tier(text, "contributor")[0] == "contributor"


# Review fix (31 Oct): digit runs glued to letters, and Unicode e-mail addresses.
GLUED = [("tel600123456", "phone"), ("PESEL44051401359", "pesel"),
         ("nip5260250274 dzięki", "nip"), ("kontakt:tel+48600123456!", "phone"),
         ("zadzwoń_600123456", "phone")]


@pytest.mark.parametrize(("text", "kind"), GLUED)
def test_a_number_glued_to_letters_is_found_and_masked(text, kind):
    from studio.receipts import redact as receipt_redact

    m = contains_pii(text)
    assert m is not None and m.kind == kind, (text, m)
    masked = redact(text)
    assert f"[{kind}]" in masked and not any(ch.isdigit() for ch in masked), masked
    assert not any(ch.isdigit() for ch in receipt_redact(text)), receipt_redact(text)
    assert choose_tier(text, "contributor")[0] == "standard"


def test_glued_hex_ids_and_short_numbers_stay():
    from studio.receipts import redact as receipt_redact

    for text in ("3f9a1234567890bbb", "run_3fa9c2b1d4e5f607", "an_ab600123456cdef0",
                 "kubek350ml za 89zł", "model X200"):
        assert contains_pii(text) is None, text
        assert receipt_redact(text) == text, text
    assert contains_pii("id 550e8400-e29b-41d4-a716-446655440000") is None


@pytest.mark.parametrize("text", ["napisz: kontakt@sklep-żabka.pl", "zofia.łęcka@poczta.pl",
                                  "info@例え.jp"])
def test_unicode_email_addresses_are_found_and_masked(text):
    from studio.receipts import redact as receipt_redact

    m = contains_pii(text)
    assert m is not None and m.kind == "email", text
    assert "@" not in redact(text) and "@" not in receipt_redact(text)


# Security review of the fix: hex letters, separators, long runs, decomposed e-mails.
@pytest.mark.parametrize("text", ["abc600123456", "a44051401359", "a44051401458",
                                  "tel600-123-456", "tel600 123 456", "tel(600)123456",
                                  "tel600123456789012345", "fax+48600123456x"])
def test_glued_runs_with_hex_letters_separators_or_any_length_are_masked(text):
    from studio.receipts import redact as receipt_redact

    for masked in (redact(text), receipt_redact(text)):
        assert sum(ch.isdigit() for ch in masked) < 9, (text, masked)
    assert choose_tier(text, "contributor")[0] == "standard"


@pytest.mark.parametrize(("text", "kind"), [("abc600123456", "phone"),
                                            ("a44051401359", "pesel"),
                                            ("tel600-123-456", "phone"),
                                            ("tel600 123 456 dzięki", "phone")])
def test_glued_runs_are_classified(text, kind):
    m = contains_pii(text)
    assert m is not None and m.kind == kind, (text, m)


@pytest.mark.parametrize("text", ["kontakt@sklep-z\u0307abka.pl", "zofia.l\u0301ecka@poczta.pl",
                                  "kontakt@sklep\u200b.pl", "pisz: jan@firma", "a@b"])
def test_decomposed_or_bare_addresses_count_as_email(text):
    from studio.receipts import redact as receipt_redact

    m = contains_pii(text)
    assert m is not None and m.kind == "email", text
    assert "@" not in redact(text) and "@" not in receipt_redact(text), text


@pytest.mark.parametrize("text", ["jan.kowalski@example.com", "x@.example.com",
                                  "jan@-b.pl", "x@a..pl", "A_B@Sub.Domain.co.uk",
                                  "100%+tag@mail-1.example.org"])
def test_new_email_pattern_covers_every_address_the_old_one_caught(text):
    from studio.receipts import _EMAIL as OLD
    from studio.receipts import redact as receipt_redact

    assert OLD.search(text), text
    out = receipt_redact(f"pisz: {text} teraz")
    assert "@" not in out and not OLD.search(out), out


@pytest.mark.parametrize("text", ["abcdef-600-123-456-abcdef", "abcdef600-123-456abcdef0000",
                                  "deadbeef 600.123.456cafe0000000000"])
def test_hex_letters_with_separators_are_not_an_id(text):
    from studio.receipts import redact as receipt_redact

    m = contains_pii(text)
    assert m is not None and m.kind == "phone", text
    for masked in (redact(text), receipt_redact(text)):
        assert "600" not in masked, (text, masked)


def test_real_uuids_and_digests_stay_intact():
    import hashlib
    import uuid

    from studio.receipts import redact as receipt_redact

    digest = "3f9a" + "600123456" + "b" * 51
    ids = [digest, "550e8400-e29b-41d4-a716-446655440000",
           "123e4567-e89b-12d3-a456-426614174000", "n_" + "1" * 32,
           hashlib.sha256(b"x").hexdigest(), uuid.UUID(int=600123456).hex]
    for value in ids:
        assert contains_pii(value) is None, value
        if "-" not in value:  # a UUID's last group stands alone: the long-run rule masks it
            assert redact_digit_runs(value) == value, value
    assert receipt_redact(digest) == digest
