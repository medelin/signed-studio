"""Finding levels by source: the one table that decides how much a review finding weighs.

Principle (docs/scenarios.md): **Muse explains Meta's rules, it does not invent new ones.**
Every finding carries a ``source`` (the Meta policy page or format specification it
stands on, a Google Ads policy for copy that runs on Google, or ``muse:grounding`` for the
anti-invention rules) and a level (the ``severity`` field):

- ``block`` only where the platform would reject the ad or flag the account;
- ``warn`` where it would degrade delivery or display;
- ``tip`` for best practice (including Meta's own *recommended* lengths); a tip never
  makes the verdict WARN or BLOCK;
- ``info`` is a note from a code check (no issue).

The models tag a level and a source too, but code has the final say: ``apply`` caps every
finding at the level this table allows for the rule on the channels the copy will run on,
and sets the source from the table. ``policy_net``, ``grounding_net``, the format check
and both model reviews go through it (``reviewer.normalize``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

TIP, WARN, BLOCK, INFO = "tip", "warn", "block", "info"
LEVEL_RANK = {INFO: 0, TIP: 1, WARN: 2, BLOCK: 3}
# what a level does to the verdict: tips and notes never raise it
VERDICT_OF = {INFO: "OK", TIP: "OK", WARN: "WARN", BLOCK: "BLOCK"}
META, GOOGLE = "meta", "google"
GROUNDING = "muse:grounding"  # P10 and S1: the owner's facts and product, not invented ones
RETRIEVED = "2026-10-29"

# Meta Advertising Standards (Transparency Center), retrieved 2026-10-29
META_PERSONAL_ATTRIBUTES = ("https://transparency.meta.com/policies/ad-standards/"
                            "objectionable-content/privacy-violations-personal-attributes/")
META_HEALTH = ("https://transparency.meta.com/policies/ad-standards/"
               "restricted-goods-services/health-wellness")
META_BUSINESS_PRACTICES = ("https://transparency.meta.com/policies/ad-standards/"
                           "fraud-scams/unacceptable-business-practices/")
META_STANDARDS = "https://transparency.meta.com/policies/ad-standards/"
META_NON_EXISTENT = ("https://transparency.meta.com/policies/ad-standards/"
                     "deceptive-content/non-existent-functionality/")
META_FEED_SPEC = "https://www.facebook.com/business/ads-guide/update/image/facebook-feed"
# Google Ads policy "Government documents and services"
GOOGLE_OFFICIAL = "https://support.google.com/adspolicy/answer/13156083"


@dataclass(frozen=True)
class Rule:
    """``caps``: the highest level the rule may reach on each channel. ``source``: where
    the rule comes from. ``en`` / ``pl``: the rule in one plain sentence for the owner."""

    id: str
    caps: dict[str, str]
    source: str
    en: str
    pl: str


RULES: dict[str, Rule] = {r.id: r for r in (
    Rule("P1", {META: BLOCK, GOOGLE: BLOCK}, META_PERSONAL_ATTRIBUTES,
         "Meta rejects ads that say or imply what a person is (their health, weight, age, "
         "finances, beliefs): speak about what the product does, not about the reader.",
         "Meta odrzuca reklamy, które mówią lub sugerują, jaki ktoś jest (zdrowie, waga, "
         "wiek, finanse, przekonania): mów o tym, co daje produkt, a nie o odbiorcy."),
    Rule("P2", {META: BLOCK, GOOGLE: BLOCK}, META_HEALTH,
         "Meta rejects before/after comparisons and images of an 'ideal' body.",
         "Meta odrzuca porównania przed/po i obrazy „idealnego” ciała."),
    Rule("P3", {META: BLOCK, GOOGLE: BLOCK}, META_BUSINESS_PRACTICES,
         "Meta rejects guaranteed results and exaggerated or unverifiable claims.",
         "Meta odrzuca gwarantowane efekty oraz przesadzone lub niesprawdzalne obietnice."),
    Rule("P4", {META: BLOCK, GOOGLE: BLOCK}, META_BUSINESS_PRACTICES,
         "Meta rejects prices and offers that do not match what the advertiser really sells.",
         "Meta odrzuca ceny i oferty niezgodne z tym, co reklamodawca naprawdę sprzedaje."),
    Rule("P5", {META: TIP, GOOGLE: TIP}, META_FEED_SPEC,
         "Meta shows your Page name on every ad, so the brand name in the text is optional; "
         "naming it helps recognition.",
         "Meta pokazuje nazwę Twojej strony przy każdej reklamie, więc nazwa marki w tekście "
         "nie jest wymagana; pomaga jednak ją rozpoznać."),
    Rule("P6", {META: BLOCK, GOOGLE: BLOCK}, META_NON_EXISTENT,
         "Meta rejects images with buttons or controls that do nothing (a fake play "
         "button); keep text in the image to the headline.",
         "Meta odrzuca obrazy z przyciskami, które nic nie robią (fałszywy przycisk "
         "odtwarzania); tekst na obrazie ogranicz do nagłówka."),
    Rule("P7", {META: BLOCK, GOOGLE: BLOCK}, META_STANDARDS,
         "Meta rejects prohibited or shocking content (weapons, drugs, adult content).",
         "Meta odrzuca treści zakazane lub szokujące (broń, narkotyki, treści dla dorosłych)."),
    Rule("P8", {META: BLOCK, GOOGLE: BLOCK}, META_HEALTH,
         "Meta rejects or restricts medical, financial and similar regulated claims.",
         "Meta odrzuca lub ogranicza twierdzenia medyczne, finansowe i inne regulowane."),
    # P9 is a Google Ads policy, not a Meta rule: a block only when the copy also runs on
    # Google Search; on Meta it stays visible as a tip with its real (Google) source.
    Rule("P9", {META: TIP, GOOGLE: BLOCK}, GOOGLE_OFFICIAL,
         "Google Ads rejects ads that offer official services (registering a company) or "
         "promise tax or legal outcomes; Meta has no such rule, so on Meta this is a tip.",
         "Google Ads odrzuca reklamy oferujące usługi urzędowe (rejestracja spółki) lub "
         "obiecujące skutki podatkowe i prawne; Meta nie ma takiej zasady, więc tu to "
         "wskazówka."),
    Rule("P10", {META: BLOCK, GOOGLE: BLOCK}, GROUNDING,
         "The ad may only state what you confirmed in the brief; Muse never adds a claim "
         "you did not make.",
         "Reklama mówi tylko to, co potwierdziłeś w briefie; Muse nie dopisuje twierdzeń, "
         "których nie było."),
    # F1: a hard limit is refused by the platform (block); a recommended length is the
    # platform's own advice (tip, with the truncation point and the spec URL).
    Rule("F1", {META: BLOCK, GOOGLE: BLOCK}, META_FEED_SPEC,
         "Each placement has its own text and image limits from Meta's ads guide.",
         "Każde miejsce wyświetlania ma własne limity tekstu i obrazu z przewodnika Meta."),
    Rule("S1", {META: BLOCK, GOOGLE: BLOCK}, GROUNDING,
         "The image must show your real product, not one the image model invented.",
         "Obraz musi pokazywać Twój prawdziwy produkt, a nie wymyślony przez model."),
    Rule("S2", {META: TIP, GOOGLE: TIP}, "muse:prompt",
         "The image prompt asked for no people; a person in the image is worth a look.",
         "Prompt obrazu prosił o brak osób; osoba na obrazie warta jest sprawdzenia."),
)}
# a finding whose rule the model did not name: shown, never blocking (no source to cite)
UNMAPPED = Rule("UNMAPPED", {META: WARN, GOOGLE: WARN}, "",
                "A reviewer note without a named rule.", "Uwaga recenzenta bez nazwanej zasady.")
_URL = re.compile(r"https://[^\s,;)\"']+")


def rule(rule_id: str) -> Rule:
    return RULES.get(rule_id, UNMAPPED)


def cap(rule_id: str, channels: Iterable[str] = (META,)) -> str:
    """The highest level ``rule_id`` may reach for copy running on ``channels``."""
    r = rule(rule_id)
    return max((r.caps.get(c, TIP) for c in channels or (META,)), key=LEVEL_RANK.__getitem__)


def level(rule_id: str, severity: str, channels: Iterable[str] = (META,)) -> str:
    """The model's or the check's level, capped by the table. F1 below a hard limit is
    always a tip (Meta's recommendation). ``info`` stays a note."""
    if severity == INFO:
        return INFO
    if rule_id == "F1" and severity != BLOCK:
        return TIP
    top = cap(rule_id, channels)
    sev = severity if severity in LEVEL_RANK else WARN
    return sev if LEVEL_RANK[sev] <= LEVEL_RANK[top] else top


def source(rule_id: str, evidence: str = "", channels: Iterable[str] = (META,)) -> str:
    """Where the finding comes from: F1 names its own spec URL in the evidence; every
    other rule its row of the table."""
    if rule_id == "F1":
        m = _URL.search(evidence)
        if m:
            return m.group().rstrip(".")
    return rule(rule_id).source


def verdict(levels: Iterable[str]) -> str:
    worst = "OK"
    for lv in levels:
        v = VERDICT_OF.get(lv, "WARN")
        if ("OK", "WARN", "BLOCK").index(v) > ("OK", "WARN", "BLOCK").index(worst):
            worst = v
    return worst


def explain(rule_id: str, lang: str = "en") -> str:
    r = rule(rule_id)
    return r.pl if lang == "pl" else r.en


def table() -> list[dict[str, str]]:
    """The table as rows (docs/policy.md and tests)."""
    return [{"rule": r.id, "meta": r.caps[META], "google": r.caps[GOOGLE],
             "source": r.source} for r in RULES.values()]
