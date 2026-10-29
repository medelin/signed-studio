"""The onboarding page (``/start``, ``/pl/start``, ``studio.routes_onboarding``), its entry
links and the first-visit banner on ``/studio``, English and Polish.

Merged into ``studio.ui.i18n`` (``EN``/``PL``), so ``tests/test_i18n.py`` checks them like
every other key. Steps are ``start.s<N>.*`` with ``h`` (title), ``p`` (at most two short
sentences), ``see`` (what you'll see) and ``art`` (the drawing's accessible name).
"""

from __future__ import annotations

STEPS = 6

EN_ONBOARDING: dict[str, str] = {
    "start.href": "/start",
    "start.link": "How it works (1 min)",
    "nav.start": "How it works",
    "meta.start": "How Muse Studio Lite works in one minute: six illustrated steps from one "
                  "brief to a PAUSED campaign in your own Meta ad account, signed by you.",
    "start.title": "How it works in one minute",
    "start.kicker": "Six steps · no account needed",
    "start.lead": "From one paragraph about your business to a campaign waiting, paused, in "
                  "your own Meta ad account. Nothing goes live without your signature.",
    "start.controls": "Animation controls",
    "start.prev": "Previous step",
    "start.next": "Next step",
    "start.play": "Play",
    "start.pause": "Pause",
    "start.dots": "Steps",
    "start.dot": "Step {n}: {title}",
    "start.step_of": "Step {n} of {total}",
    "start.see": "What you'll see:",
    "start.s1.h": "Say or type one brief",
    "start.s1.p": "Tell the studio about your business and your offer in your own words. "
                  "Speak or type: one paragraph is enough.",
    "start.s1.see": "A text box with a microphone button; your words become a short brief "
                    "card.",
    "start.s1.art": "An owner speaks; her words flow into a brief card.",
    "start.s2.h": "Answer the fact questions",
    "start.s2.p": "Anything the studio cannot know is marked [FACT] and asked of you. Only "
                  "the facts you confirm may appear in the ad, so nothing is made up.",
    "start.s2.see": "A few short questions: answer each in a sentence, or strike the claim.",
    "start.s2.art": "A brief card with two confirmed facts and one open question that turns "
                    "into a tick.",
    "start.s3.h": "Agents plan, write and draw",
    "start.s3.p": "Muse Spark plans the campaign and writes the text. Muse Image draws each "
                  "picture, and SAM 3.1 cuts your product out of your own photo.",
    "start.s3.see": "A progress line through the agents, then the images as they appear.",
    "start.s3.art": "Three agent marks light up in turn while the ad's text, picture and "
                    "product cut-out appear on the card.",
    "start.s4.h": "Two reviewers check it",
    "start.s4.p": "Two independent reviewers, one on the open-weights Muse Spark Open, check "
                  "the ad against Meta's ad rules and your product photo. If either says "
                  "BLOCK, the text is rewritten and checked again.",
    "start.s4.see": "Each reviewer's verdict (PASS, WARN or BLOCK) with its reason.",
    "start.s4.art": "Two reviewer marks look at the ad: first a cross, then a rewrite, then "
                    "a tick.",
    "start.s5.h": "You sign, it lands PAUSED",
    "start.s5.p": "You sign the exact campaign on your phone. It is then created PAUSED in "
                  "your own Meta ad account, with your landing page and your lead form.",
    "start.s5.see": "The approval screen with exactly what you sign, then the PAUSED "
                    "campaign.",
    "start.s5.art": "A signature is drawn on a phone; the ad then appears in an ad-account "
                    "frame with a pause badge.",
    "start.s6.h": "Replies come back to one inbox",
    "start.s6.p": "Leads and comments return to one inbox with a suggested reply. Every step "
                  "leaves a receipt: a signed record you can check yourself.",
    "start.s6.see": "One inbox, and a Receipts page where each step can be checked.",
    "start.s6.art": "Comments and a lead drop into one inbox tray; a row of receipts lights "
                    "up underneath.",
    "start.end.h": "See it for yourself",
    "start.end.p": "Watch one real recorded run step by step, no account needed, or sign in "
                   "and make your own.",
    "start.cta_try": "Try a recorded run",
    "start.cta_try_href": "/try",
    "start.cta_sign_in": "Sign in",
    "start.cta_studio": "Open the studio",
    "start.banner_label": "Getting started",
    "start.banner": "New here? See the whole flow in six pictures.",
    "start.banner_close": "Got it",
    "js.ob_play": "Play",
    "js.ob_pause": "Pause",
    "js.ob_step_of": "Step {n} of {total}",
}

PL_ONBOARDING: dict[str, str] = {
    "start.href": "/pl/start",
    "start.link": "Jak to działa (1 min)",
    "nav.start": "Jak to działa",
    "meta.start": "Jak działa Muse Studio Lite w minutę: sześć ilustrowanych kroków od "
                  "jednego briefu do wstrzymanej (PAUSED) kampanii na twoim koncie reklamowym "
                  "Meta, podpisanej przez ciebie.",
    "start.title": "Jak to działa — w minutę",
    "start.kicker": "Sześć kroków · bez konta",
    "start.lead": "Od jednego akapitu o twojej firmie do kampanii, która czeka wstrzymana na "
                  "twoim własnym koncie reklamowym Meta. Nic nie rusza bez twojego podpisu.",
    "start.controls": "Sterowanie animacją",
    "start.prev": "Poprzedni krok",
    "start.next": "Następny krok",
    "start.play": "Odtwórz",
    "start.pause": "Pauza",
    "start.dots": "Kroki",
    "start.dot": "Krok {n}: {title}",
    "start.step_of": "Krok {n} z {total}",
    "start.see": "Co zobaczysz:",
    "start.s1.h": "Powiedz lub wpisz jeden brief",
    "start.s1.p": "Opisz studiu swoją firmę i ofertę własnymi słowami. Mów albo pisz: "
                  "wystarczy jeden akapit.",
    "start.s1.see": "Pole tekstowe z przyciskiem mikrofonu; twoje słowa stają się krótką "
                    "kartą briefu.",
    "start.s1.art": "Właścicielka mówi; jej słowa spływają do karty briefu.",
    "start.s2.h": "Odpowiedz na pytania o fakty",
    "start.s2.p": "Wszystko, czego studio nie może wiedzieć, oznacza jako [FACT] i pyta o to "
                  "ciebie. W reklamie mogą się znaleźć tylko potwierdzone przez ciebie fakty, "
                  "więc nic nie jest zmyślone.",
    "start.s2.see": "Kilka krótkich pytań: odpowiedz zdaniem albo skreśl twierdzenie.",
    "start.s2.art": "Karta briefu z dwoma potwierdzonymi faktami i jednym otwartym pytaniem, "
                    "które zmienia się w znacznik.",
    "start.s3.h": "Agenci planują, piszą i rysują",
    "start.s3.p": "Muse Spark planuje kampanię i pisze teksty. Muse Image rysuje każdą "
                  "grafikę, a SAM 3.1 wycina twój produkt z twojego własnego zdjęcia.",
    "start.s3.see": "Linia postępu przez kolejnych agentów, a potem pojawiające się grafiki.",
    "start.s3.art": "Trzy znaczniki agentów zapalają się po kolei, a na karcie pojawiają się "
                    "tekst, grafika i wycięty produkt.",
    "start.s4.h": "Sprawdza to dwóch recenzentów",
    "start.s4.p": "Dwóch niezależnych recenzentów, w tym jeden na otwartym modelu Muse Spark "
                  "Open, porównuje reklamę z zasadami reklam Meta i zdjęciem produktu. Jeśli "
                  "któryś powie BLOCK, tekst jest przepisywany i sprawdzany od nowa.",
    "start.s4.see": "Werdykt każdego recenzenta (PASS, WARN albo BLOCK) z uzasadnieniem.",
    "start.s4.art": "Dwa znaczniki recenzentów patrzą na reklamę: najpierw krzyżyk, potem "
                    "poprawka, potem znacznik.",
    "start.s5.h": "Podpisujesz, kampania czeka wstrzymana",
    "start.s5.p": "Podpisujesz na telefonie dokładnie tę kampanię. Powstaje wtedy jako PAUSED "
                  "na twoim koncie reklamowym Meta, z twoją stroną docelową i twoim "
                  "formularzem.",
    "start.s5.see": "Ekran zatwierdzenia z dokładnie tym, co podpisujesz, a potem wstrzymana "
                    "kampania.",
    "start.s5.art": "Na telefonie rysuje się podpis; reklama pojawia się w ramce konta "
                    "reklamowego ze znakiem pauzy.",
    "start.s6.h": "Odpowiedzi wracają do jednej skrzynki",
    "start.s6.p": "Leady i komentarze wracają do jednej skrzynki z propozycją odpowiedzi. "
                  "Każdy krok zostawia pokwitowanie: podpisany zapis, który sprawdzisz sam.",
    "start.s6.see": "Jedna skrzynka i strona Pokwitowania, na której sprawdzisz każdy krok.",
    "start.s6.art": "Komentarze i lead wpadają do jednej skrzynki; pod nią zapala się rząd "
                    "pokwitowań.",
    "start.end.h": "Zobacz sam",
    "start.end.p": "Obejrzyj krok po kroku jeden prawdziwy, nagrany przebieg, bez konta, "
                   "albo zaloguj się i zrób własny.",
    "start.cta_try": "Zobacz nagrany przebieg",
    "start.cta_try_href": "/pl/try",
    "start.cta_sign_in": "Zaloguj się",
    "start.cta_studio": "Otwórz studio",
    "start.banner_label": "Pierwsze kroki",
    "start.banner": "Pierwszy raz tutaj? Zobacz cały przebieg w sześciu obrazkach.",
    "start.banner_close": "Rozumiem",
    "js.ob_play": "Odtwórz",
    "js.ob_pause": "Pauza",
    "js.ob_step_of": "Krok {n} z {total}",
}
