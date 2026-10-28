"""Brief v1 (``studio.brief``): strict schema, canonical hash, field errors, [FACT]
resolution, the grounding nets and the producer's draft call."""

import json

import pytest
from pydantic import ValidationError

from studio import brief as bv
from studio.agents import copywriter, reviewer
from studio.agents.schemas import CopyVariant
from studio.muse.structured import strict_schema
from studio.receipts import canonical, sha256_hex


def sample(**over) -> bv.BriefV1:
    data = {
        "advertiser": {"kind": "person", "display_name": "Clay Workshop",
                       "countries": ["PL"], "landing_url": "https://clay.example/"},
        "confirmed_facts": ["2-hour workshop, 180 PLN per person", "Saturdays 10:00 and 14:00",
                            "at most 8 people"],
        "offer": {"product": "wheel-throwing workshop for beginners",
                  "offer": "November dates", "valid_until": "2026-11-30",
                  "reasons_to_believe": [{"text": "small groups", "facts": [2]}]},
        "objective": {"goal": "leads", "kpi": "sign-ups from the form"},
        "audience": {"locations": ["Kraków + 20 km"], "age_min": 25, "age_max": 55,
                     "languages": ["pl"],
                     "insight": {"who": "office workers", "belief": "I am not crafty",
                                 "barrier": "fear of failing", "trigger": "a first bowl"}},
        "proposition": "Your first bowl, made by your own hands, in two hours.",
        "tone_brand": {"tone": ["warm"], "colours": ["clay"], "donts": ["no hype"]},
        "mandatories": {"disclaimers": [], "banned_claims": ["best in town"], "dates": []},
        "budget": {"kind": "daily", "amount": 40, "currency": "PLN", "start": "2026-11-03",
                   "end": "2026-11-30"},
        "placements": {"automatic": True, "deliverables": ["feed_1x1", "story_9x16"]},
        "assets": [{"kind": "product_photo", "note": "bowls on a shelf"}],
        "open_questions": [],
    }
    for key, value in over.items():
        data[key] = value
    return bv.BriefV1.model_validate(data)


def test_schema_is_strict_and_closed():
    good = sample().model_dump()
    with pytest.raises(ValidationError):
        bv.BriefV1.model_validate({**good, "nip": "123"})  # no national ids, no extras
    with pytest.raises(ValidationError):
        bv.BriefV1.model_validate({**good, "objective": {"goal": "reach", "kpi": "x"}})
    bad = json.loads(json.dumps(good))
    bad["audience"]["age_min"] = "25"  # strict: no string → int coercion
    with pytest.raises(ValidationError):
        bv.BriefV1.model_validate(bad)
    schema = strict_schema(bv.BriefV1)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(bv.BriefV1.model_fields)
    goal = schema["properties"]["objective"]["properties"]["goal"]
    assert goal["enum"] == list(bv.GOALS)


def test_hash_is_canonical_json_with_the_schema_id():
    b = sample()
    assert bv.brief_sha256(b) == sha256_hex(canonical({"schema": "brief/v1",
                                                       **b.model_dump(mode="json")}))
    edited = b.model_copy(update={"confirmed_facts": [*b.confirmed_facts[:-1],
                                                      "at most 9 people"]})
    assert bv.brief_sha256(edited) != bv.brief_sha256(b)
    # key order does not matter
    again = bv.BriefV1.model_validate(dict(reversed(list(b.model_dump().items()))))
    assert bv.brief_sha256(again) == bv.brief_sha256(b)


def test_a_complete_brief_has_no_problems():
    assert bv.problems(sample(), ceiling_daily=50) == {}


@pytest.mark.parametrize("change,field,code", [
    (lambda d: d["advertiser"].update(landing_url="http://x.example/"),
     "advertiser.landing_url", "bad_url"),
    (lambda d: d["advertiser"].update(countries=["Poland"]), "advertiser.countries",
     "bad_country"),
    (lambda d: d["advertiser"].update(display_name=" "), "advertiser.display_name",
     "required"),
    (lambda d: d["offer"]["reasons_to_believe"][0].update(facts=[]),
     "offer.reasons_to_believe.0", "no_fact_ref"),
    (lambda d: d["offer"]["reasons_to_believe"][0].update(facts=[7]),
     "offer.reasons_to_believe.0", "bad_fact_ref"),
    (lambda d: d.update(proposition="One. Two sentences."), "proposition", "one_sentence"),
    (lambda d: d["audience"].update(age_min=60, age_max=30), "audience.age", "age_range"),
    (lambda d: d["budget"].update(amount=0), "budget.amount", "amount"),
    (lambda d: d["budget"].update(amount=80), "budget.amount", "over_ceiling"),
    (lambda d: d["budget"].update(currency="EUR"), "budget.currency", "account_currency"),
    (lambda d: d["budget"].update(end="2026-10-01"), "budget.end", "date_order"),
    (lambda d: d["budget"].update(kind="lifetime", end=""), "budget.end", "required"),
    (lambda d: d["placements"].update(deliverables=[]), "placements.deliverables",
     "required"),
    (lambda d: d.update(confirmed_facts=["price [FACT]"]), "confirmed_facts", "placeholder"),
    (lambda d: d.update(open_questions=[{"question": "Is clay included?",
                                         "claim": "all included"}]),
     "open_questions", "open_question"),
])
def test_field_errors_name_the_field(change, field, code):
    data = sample().model_dump()
    change(data)
    assert bv.problems(bv.BriefV1.model_validate(data), ceiling_daily=50).get(field) == code


def test_resolve_turns_answers_into_facts_and_drops_struck_claims():
    q = [{"question": "Is clay included?", "claim": "everything included"},
         {"question": "Can I take the bowl home?", "claim": "take it home"}]
    b = sample(open_questions=q)
    out, answered, struck = bv.resolve(b, [
        bv.Resolution(action="answer", text="Clay and firing are included"),
        bv.Resolution(action="strike")])
    assert out.open_questions == [] and (answered, struck) == (1, 1)
    assert out.confirmed_facts[-1] == "Clay and firing are included"
    assert "take it home" not in json.dumps(out.model_dump())
    with pytest.raises(ValueError):
        bv.resolve(b, [bv.Resolution(action="strike")])


def test_ground_draft_moves_numbers_the_owner_never_wrote_into_questions():
    owner = "Pottery workshop on Saturdays at 10:00, 180 zł per person, max 8 people."
    draft = sample(confirmed_facts=["180 zł per person", "Saturdays at 10:00 and 14:00",
                                    "max 8 people"],
                   offer={"product": "pottery", "offer": "20% off in November",
                          "valid_until": "", "reasons_to_believe": [
                              {"text": "small groups", "facts": [2]}]},
                   proposition="A bowl in 2 hours.")
    out = bv.ground_draft(draft, owner, lang="pl")
    assert out.confirmed_facts == ["180 zł per person", "max 8 people"]
    assert out.offer.reasons_to_believe[0].facts == [1]  # re-indexed with the fact
    assert out.offer.offer == "" and out.proposition == ""
    claims = [q.claim for q in out.open_questions]
    assert claims == ["Saturdays at 10:00 and 14:00", "20% off in November",
                      "A bowl in 2 hours."]
    assert out.open_questions[0].question.startswith("Proszę potwierdzić")


def test_numbers_fold_lookalike_digits():
    assert bv.ungrounded_numbers("now ５０ PLN", "price 50 PLN") == []
    assert bv.ungrounded_numbers("now 49 PLN, 50% off", "price 50 PLN") == ["49"]


def test_daily_budget_from_daily_or_lifetime():
    assert bv.daily_budget(sample()) == 40
    life = sample(budget={"kind": "lifetime", "amount": 100, "currency": "PLN",
                          "start": "2026-11-01", "end": "2026-11-03"})
    assert bv.daily_budget(life) == 34  # 100 over 3 days, rounded up


def variant(text: str, headline: str = "Your first bowl") -> CopyVariant:
    return CopyVariant(primary_text=text, headline=headline, description="Kraków",
                       cta="SIGN_UP")


def test_grounding_net_blocks_unconfirmed_numbers_and_banned_claims():
    b = sample()
    assert reviewer.grounding_net(variant("2-hour workshop, 180 PLN, Saturdays 10:00"), b) == []
    found = reviewer.grounding_net(variant("Only 150 PLN, the best in town!"), b)
    assert [f.rule for f in found] == ["P10", "P10"]
    assert all(f.severity == "block" for f in found)
    assert "150" in found[0].evidence and "best in town" in found[1].evidence
    assert reviewer.grounding_net(variant("Only 150 PLN"), None) == []  # free-text brief


def test_copywriter_gets_only_the_confirmed_facts():
    from studio.agents.schemas import Angle
    msg = copywriter._user_message(Angle(name="a", hook="h", persona="p"), "p", "brief text",
                                   facts=["180 PLN per person"])
    assert copywriter.FACTS_RULE in msg and "1. 180 PLN per person" in msg
    plain = copywriter._user_message(Angle(name="a", hook="h", persona="p"), "p", "brief")
    assert copywriter.FACTS_RULE not in plain  # free-text runs (and their replays) unchanged


def test_reviewer_message_carries_rule_p10_and_the_facts_only_with_a_brief():
    png = b"\x89PNG\r\n\x1a\n" + bytes(16)  # only sniffed by _messages
    with_brief = reviewer._messages(png, variant("x"), sample())
    text = with_brief[1]["content"][0]["text"]
    assert "Rule P10" in text and "source=brief_facts" in text and "180 PLN" in text
    without = reviewer._messages(png, variant("x"))[1]["content"][0]["text"]
    assert "P10" not in without and "brief_facts" not in without


def test_render_lists_every_section():
    text = bv.render(sample())
    for needle in ("Clay Workshop", "1. 2-hour workshop", "Objective: leads",
                   "Proposition:", "Banned claims: best in town", "Budget: daily 40 PLN",
                   "deliverables feed_1x1, story_9x16", "product_photo"):
        assert needle in text



def test_every_error_code_has_an_english_and_polish_message():
    import re
    from pathlib import Path

    from studio.ui import i18n
    root = Path(__file__).resolve().parents[1] / "studio"
    body = (root / "brief.py").read_text().split("def problems")[1].split("def daily_budget")[0]
    codes = set(re.findall(r'= "([a-z_]+)"', body)) - {"daily", "lifetime"}
    codes |= set(re.findall(r'"(required|too_long|too_many|empty_item)"', body))
    codes |= {"unresolved", "answer_required", "invalid"}  # routes_studio.confirm_and_start
    assert len(codes) > 20
    for code in codes:
        assert f"js.brief_err_{code}" in i18n.EN and f"js.brief_err_{code}" in i18n.PL, code
