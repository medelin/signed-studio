"""Attack suite (day 2 fix round, I3): copy with an unfilled ``[FACT]`` cannot be signed.

The copywriter writes ``[FACT]`` where the brand sheet lacks a fact. A model (or an
injected brief) that pushes such a variant to approval anyway is refused in code, and the
copy's ``risks`` travel inside the payload the human signs.
"""

import json
import re

import pytest

from studio import db
from studio.agents import producer
from studio.agents.dispatcher import Dispatcher
from studio.agents.schemas import CopyPack, CreativeImage, CreativeSet, Plan
from studio.muse.client import MuseClient, ToolCall
from studio.settings import Budget
from tests.cassettes import FAKE_KEY, ROOT

PHOTO = (ROOT / "fixtures" / "assets" / "product-demo.png").read_bytes()
PLAN = {"audience": "a", "angles": [{"name": "n", "hook": "h", "persona": "p"}] * 3,
        "budget_pln_daily": 30, "placements": ["feed_1x1"], "tasks": ["t"]}
CLEAN = {"primary_text": "Hand-thrown, 350 ml.", "headline": "Warm mug", "description": "89 PLN",
         "cta": "SHOP_NOW"}


@pytest.fixture
def ctx(tmp_path):
    conn = db.connect(tmp_path / "studio.db")
    db.init(conn)
    ledger = db.open_ledger(conn, tmp_path / "ledger.key")
    client = MuseClient(FAKE_KEY, ledger=ledger, budget=Budget(1.0))
    return producer.Context(client=client, open_client=None, conn=conn, photo_png=PHOTO,
                            concept="mug", data_dir=tmp_path / "var")


def _run(variant, risks):
    run = producer.RunResult(run_id="atk-i3", status="running")
    run.plan = Plan.model_validate(PLAN)
    run.copy = CopyPack.model_validate({"variants": [variant, CLEAN, CLEAN], "risks": risks})
    img = CreativeImage(placement="feed_1x1", size="1024x1024", path="x.png", sha256="0" * 64,
                        width=1, height=1, response_id="")
    run.creatives = CreativeSet(cutout_path="c.png", cutout_sha256="0" * 64,
                                foreground_pixels=1, images=[img], prompts=[],
                                response_ids=[])
    run.review = {"verdict": "OK", "second_opinion": "unavailable", "disagreements": [],
                  "formats": [{"placement": "feed_1x1", "sha256": "0" * 64, "verdict": "OK"}],
                  "variant_index": 0, "binding": producer.review_binding(run, 0)}
    return run


def _approve(ctx, run):
    d = Dispatcher(producer._handlers(ctx, run, "brief"), ledger=ctx.client.ledger,
                   run_id=run.run_id)
    return d.execute_or_refuse(ToolCall("c1", "request_approval", '{"summary": "all OK"}'))


@pytest.mark.parametrize("field", ["primary_text", "headline", "description"])
@pytest.mark.parametrize("token", ["[FACT]", "[fact]", "[ FACT ]"])
def test_variant_with_unfilled_fact_is_refused(ctx, field, token):
    variant = {**CLEAN, field: f"Glazed in {token} colours"}
    out = _approve(ctx, _run(variant, ["variant 1: unfilled [FACT] placeholder"]))
    assert out.status == "error" and "[FACT]" in out.output["error"]
    assert ctx.conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_risks_are_part_of_the_signed_payload(ctx):
    risks = ["variant 2: unfilled [FACT] placeholder, needs a real fact before use"]
    out = _approve(ctx, _run(CLEAN, risks))
    assert out.status == "ok"
    (payload,) = ctx.conn.execute("SELECT payload FROM pending_payloads").fetchone()
    assert json.loads(payload)["risks"] == risks


# Follow-up security review: look-alike placeholders must not slip past the gate.
EVASIONS = [
    "[\uff26\uff21\uff23\uff34]",  # fullwidth letters
    "\uff3bFACT\uff3d",             # fullwidth brackets
    "[F\u200bACT]",                 # zero-width space
    "[FA\u00adCT]",                 # soft hyphen (Cf)
    "[\u2060FACT\u2060]",           # word joiner
    "[F\u0410\u0421\u0422]",        # Cyrillic A, C, T
    "[\u0192act]",                  # Latin small f with hook
    "[\u03dc\u0391\u03f9\u03a4]",   # Greek digamma, alpha, lunate sigma, tau
    "[F\u3164ACT]",                 # Hangul filler
    "[\u115fFACT]",                 # Hangul choseong filler
    "FACT\u200b",                   # no brackets, zero-width tail
    "\u0424\u0410\u0421\u0422",      # all-Cyrillic, no brackets
]


@pytest.mark.parametrize("token", EVASIONS)
def test_look_alike_placeholders_are_caught(ctx, token):
    variant = {**CLEAN, "headline": f"Glazed in {token}"}
    out = _approve(ctx, _run(variant, []))
    assert out.status == "error"
    error = out.output["error"]
    assert "[FACT]" in error or re.fullmatch(r"unsupported character U\+[0-9A-F]{4,6} in "
                                             r"headline", error), error
    assert ctx.conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


@pytest.mark.parametrize(("text", "code"), [
    ("Kubek\u200b", "U+200B"), ("Ｓale", "U+FF33"), ("Kubek \u0430", "U+0430"),
    ("mug\u3164", "U+1160"), ("caf\u00e9", "U+00E9"), ("kubek\u200d", "U+200D"),
    ("a\ufe0f", "U+FE0F"), ("<b>", "U+003C"), ("[FACT]", "U+005B"),
])
def test_allowlist_names_the_first_unsupported_character(text, code):
    from studio.agents import copywriter

    v = CopyPack.model_validate({"variants": [{**CLEAN, "headline": text}] * 3,
                                 "risks": []}).variants[0]
    assert copywriter.unsupported_character(v) == f"unsupported character {code} in headline"


def test_polish_copy_with_emoji_is_signable(ctx):
    variant = {"primary_text": "Ręcznie toczony kubek – 350 ml; zmywarka? Tak! ☕\ufe0f\n"
                               "Darmowa dostawa od 150\u00a0zł (PLN). Żółć, ćma, źdźbło…",
               "headline": "Zaczynaj dzień spokojnie 👩\u200d💻",
               "description": "89 zł / szt. — 100% kamionka, #handmade @studio & 2×",
               "cta": "SHOP_NOW"}
    variant["description"] = variant["description"].replace("×", "x")
    out = _approve(ctx, _run(variant, []))
    assert out.status == "ok", out.output


def test_ordinary_copy_is_not_a_placeholder(ctx):
    variant = {**CLEAN, "primary_text": "In fact, it holds 350 ml. Facts first."}
    assert _approve(ctx, _run(variant, [])).status == "ok"


def test_model_reported_unfilled_facts_refuse_approval(ctx):
    run = _run(CLEAN, [])
    run.copy = run.copy.model_copy(update={"unfilled_facts": 1})
    out = _approve(ctx, run)
    assert out.status == "error" and "unfilled" in out.output["error"]
    assert ctx.conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_unfilled_facts_is_a_required_field_of_the_copy_schema():
    from studio.agents.schemas import CopyPack
    from studio.muse.structured import strict_schema

    schema = strict_schema(CopyPack)
    assert "unfilled_facts" in schema["required"]
    assert schema["properties"]["unfilled_facts"]["type"] == "integer"


def test_code_count_overrides_a_model_that_reports_zero():
    from studio.agents import copywriter

    pack = CopyPack.model_validate({"variants": [{**CLEAN, "headline": EVASIONS[0]},
                                                 CLEAN, CLEAN], "risks": [],
                                    "unfilled_facts": 0})
    assert copywriter.flag_unfilled_facts(pack).unfilled_facts == 1


# Review follow-up: dingbat brackets inside the emoji blocks must not wrap a placeholder.
@pytest.mark.parametrize("token", ["❨FACT❩", "❮fact❯", "❲FACT❳",
                                   "❛FACT❜", "❴ FACT ❵"])
def test_ornamental_brackets_cannot_wrap_a_placeholder(ctx, token):
    out = _approve(ctx, _run({**CLEAN, "headline": f"Glazed {token}"}, []))
    assert out.status == "error", token
    assert ctx.conn.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


@pytest.mark.parametrize("emoji", ["\U0001f389", "☕", "✅", "❤️",
                                   "\U0001f44d\U0001f3fd", "✨"])
def test_real_emoji_are_still_allowed(ctx, emoji):
    assert _approve(ctx, _run({**CLEAN, "headline": f"Nowe kubki {emoji}"}, [])).status == "ok"


def test_every_bracket_the_allowlist_accepts_is_caught_by_the_net():
    import sys
    import unicodedata

    from studio.agents import copywriter

    opens = [chr(c) for c in range(sys.maxunicode + 1)
             if unicodedata.category(chr(c)) in ("Ps", "Pi")]
    closes = [chr(c) for c in range(sys.maxunicode + 1)
              if unicodedata.category(chr(c)) in ("Pe", "Pf")]
    tokens = [f"{o}FACT)" for o in opens] + [f"(FACT{c}" for c in closes] + ["<FACT>"]
    for token in tokens:
        ch = token[0] if token[0] != "(" else token[-1]
        v = CopyPack.model_validate({"variants": [{**CLEAN, "headline": token}] * 3,
                                     "risks": []}).variants[0]
        assert copywriter.unsupported_character(v) or copywriter.has_unfilled_fact(v), \
            f"U+{ord(ch):04X}"
