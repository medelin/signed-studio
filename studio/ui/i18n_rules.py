"""Meta's rules explained (``studio.agents.rules``), English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key. ``js.rule_<id>`` is the one plain sentence of a rule, taken from the rules
table itself (one source for code and screen).
"""

from __future__ import annotations

from studio.agents import rules

EN_RULES: dict[str, str] = {
    "js.source_link": "policy",
    "js.source_muse_grounding": "only what you confirmed",
    "js.source_muse_prompt": "image prompt",
    "js.source_muse_unmapped": "no known rule; kept at the reviewer's level",
    # A2: what the copywriter left out, on the approval screen
    "studio.ap.left_out": "Left out to keep the ad within Meta's rules",
    "js.left_out_asked": "You asked: “{asked}”",
    "js.left_out_instead": "The ad says instead: “{instead}”",
    "js.left_out_rule": "Rule {rule}: {sentence}",
    "js.left_out_notes": "Copywriter's notes",
    "js.left_out_overrule": "Disagree? Edit the brief and run again — Muse never removes "
                            "what you asked for without showing it here.",
    "js.left_out_edit": "Edit the brief",
    **{f"js.rule_{r.id}": r.en for r in rules.RULES.values()},
    # A7/B7: Meta disapproved an ad (inbox)
    "dp.title": "Meta disapproved an ad",
    "dp.lead": "Muse read Meta's review of your live ads. Below: Meta's reason in plain "
               "words, and one compliant variant from the same confirmed facts, re-checked "
               "by both reviewers. Nothing changes on Meta until you sign.",
    "dp.ad": "An ad of campaign {campaign}",
    "dp.policy": "Meta's policy",
    "dp.meta_said": "Meta's own words:",
    "dp.proposal": "Proposed replacement (reviewers: {verdict}):",
    "dp.propose": "Propose a compliant variant",
    "dp.sign": "Sign: new creative and new ad, PAUSED",
    "dp.safety": "Signing creates a new creative and a new ad in the same ad set, PAUSED. "
                 "The disapproved ad is left as it is (Meta does not deliver it; you can "
                 "still ask Meta for another review).",
    "enum.dp_status.open": "disapproved", "enum.dp_status.blocked": "variant blocked",
    "enum.dp_status.requested": "awaiting your signature",
    "enum.dp_status.replaced": "replaced (new ad PAUSED)", "enum.dp_status.failed": "failed",
    "js.dp_proposing": "Writing a compliant variant and re-checking it…",
    "js.dp_sign_label": "replacement ad",
    "js.dp_blocked": "The reviewers blocked the new variant too; nothing was proposed. Edit "
                     "the brief or try again.",
    "js.dp_failed": "Could not propose a variant ({error})",
    "err.no_assets": "The approved image is not reachable on this server.",
    "err.not_open": "A replacement for this ad was already requested.",
    "err.not_proposed": "This replacement was not proposed for this disapproval.",
    "err.image_changed": "The approved image changed on disk since it was signed.",
}

PL_RULES: dict[str, str] = {
    "js.source_link": "zasada",
    "js.source_muse_grounding": "tylko to, co potwierdziłeś",
    "js.source_muse_prompt": "prompt obrazu",
    "js.source_muse_unmapped": "brak znanej zasady; poziom recenzenta",
    "studio.ap.left_out": "Pominięte, by reklama mieściła się w zasadach Meta",
    "js.left_out_asked": "Prosiłeś: „{asked}”",
    "js.left_out_instead": "Reklama mówi zamiast tego: „{instead}”",
    "js.left_out_rule": "Zasada {rule}: {sentence}",
    "js.left_out_notes": "Uwagi copywritera",
    "js.left_out_overrule": "Nie zgadzasz się? Zmień brief i uruchom ponownie — Muse nigdy "
                            "nie usuwa tego, o co prosiłeś, bez pokazania tego tutaj.",
    "js.left_out_edit": "Zmień brief",
    **{f"js.rule_{r.id}": r.pl for r in rules.RULES.values()},
    "dp.title": "Meta odrzuciła reklamę",
    "dp.lead": "Muse odczytała ocenę Meta Twoich aktywnych reklam. Poniżej: powód Meta "
               "prostymi słowami i jeden zgodny wariant z tych samych potwierdzonych faktów, "
               "sprawdzony ponownie przez obu recenzentów. Nic się nie zmienia w Meta, "
               "dopóki nie podpiszesz.",
    "dp.ad": "Reklama kampanii {campaign}",
    "dp.policy": "Zasada Meta",
    "dp.meta_said": "Słowa Meta:",
    "dp.proposal": "Proponowany zamiennik (recenzenci: {verdict}):",
    "dp.propose": "Zaproponuj zgodny wariant",
    "dp.sign": "Podpisz: nowa kreacja i nowa reklama, WSTRZYMANA",
    "dp.safety": "Podpis tworzy nową kreację i nową reklamę w tym samym zestawie reklam, "
                 "WSTRZYMANĄ. Odrzucona reklama zostaje bez zmian (Meta jej nie wyświetla; "
                 "nadal możesz poprosić Meta o ponowną ocenę).",
    "enum.dp_status.open": "odrzucona", "enum.dp_status.blocked": "wariant zablokowany",
    "enum.dp_status.requested": "czeka na Twój podpis",
    "enum.dp_status.replaced": "zastąpiona (nowa reklama WSTRZYMANA)",
    "enum.dp_status.failed": "błąd",
    "js.dp_proposing": "Piszę zgodny wariant i sprawdzam go ponownie…",
    "js.dp_sign_label": "reklama zastępcza",
    "js.dp_blocked": "Recenzenci zablokowali też nowy wariant; nic nie zaproponowano. Zmień "
                     "brief albo spróbuj ponownie.",
    "js.dp_failed": "Nie udało się zaproponować wariantu ({error})",
    "err.no_assets": "Zatwierdzony obraz jest niedostępny na tym serwerze.",
    "err.not_open": "Zamiennik tej reklamy został już zaproponowany.",
    "err.not_proposed": "Ten zamiennik nie został zaproponowany dla tego odrzucenia.",
    "err.image_changed": "Zatwierdzony obraz zmienił się na dysku od podpisu.",
}
