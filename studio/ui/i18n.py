"""Every UI string, English and Polish, in one place.

Templates call ``t("key", name=value)`` (``str.format`` placeholders); keys ending in
``_html`` hold trusted markup from this file and are returned as ``Markup``. Scripts get the
``js.*`` keys as a JSON data block (``<script type="application/json" id="i18n">``, not
executed, so the CSP holds) and read them through ``/static/i18n.js``.

The language comes from ``?lang=en|pl`` (which also sets the ``lang`` cookie), else the
cookie, else English. ``tests/test_i18n.py`` checks that both catalogues have the same keys
and the same placeholders. FAQ answers live in ``help_texts.py``.
"""

from __future__ import annotations

import string
from typing import Any

from jinja2 import pass_context
from markupsafe import Markup

LANGS = ("en", "pl")
DEFAULT = "en"
NAMES = {"en": "English", "pl": "Polski"}

EN: dict[str, str] = {
    # header, footer, language
    "brand": "Muse Studio Lite",
    "nav.studio": "Studio", "nav.inbox": "Inbox", "nav.leads": "Leads", "nav.report": "Report",
    "nav.receipts": "Receipts", "nav.help": "Help", "nav.label": "Main",
    "tagline": "every agent leaves a receipt · a human signs every send",
    "badge.read_only": "read-only", "badge.replay": "replay",
    "badge.replay_title": "Model API answers are recorded responses served offline "
                          "(STUDIO_REPLAY=1)",
    "auth.sign_in": "Sign in", "auth.sign_out": "Sign out",
    "lang.label": "Language",
    "foot.ledger": "Ledger", "foot.seq": "manifest seq", "foot.manifest": "manifest",
    "foot.key": "ledger key",
    # common
    "common.receipt_cols.time": "time (UTC)", "common.receipt_cols.run": "run",
    "common.receipt_cols.agent": "agent", "common.receipt_cols.kind": "kind",
    "common.receipt_cols.model": "provider · model", "common.receipt_cols.tier": "tier",
    "common.receipt_cols.tokens": "tokens in/out", "common.receipt_cols.cost": "cost",
    "common.receipt_cols.hash": "hash",
    # index
    "index.kicker": "two views of every ad",
    "index.lead": "Muse agents plan, write, draw and review. Nothing leaves the studio until "
                  "a person signs the exact content.",
    "index.signed_in": "You are signed in as the judge.",
    "index.judges": "Judges sign in to run the studio.",
    "index.login_off": "Sign-in is disabled on this instance.",
    "index.help_html": 'Who checks what, and what it costs: <a href="/help">help</a>.',
    "index.art": "Four owners of small businesses side by side, the four agents as diamonds "
                 "on a dotted trace, the owner's signature as the last node, and the ad that "
                 "is complete only after the signature.",
    "index.latest": "Latest receipts",
    "index.all": "All receipts, filter by run, copy as JSON →",
    "index.empty_html": 'No receipts yet. Start a run in the <a href="/studio">studio</a> or '
                        'triage a comment in the <a href="/inbox">inbox</a>; every model call, '
                        "tool call, review, signature and write appears here.",
    # login
    "login.title": "Sign in",
    "login.hint": "Judges: the password is in the submission form. Nothing is published "
                  "without your signature; campaigns are created paused.",
    "login.user": "User name", "login.password": "Password",
    "login.failed": "Sign-in failed. Check the user name and password, or try again in a "
                    "minute.",
    "login.disabled": "Sign-in is disabled on this instance.",
    "login.public_html": 'Public and read-only without signing in: <a href="/api/ledger-key">'
                         'ledger key</a>, <a href="/api/ledger-manifest">manifest</a>, '
                         '<a href="/api/approver-keys">approver keys</a> (what '
                         "<code>studio verify --server</code> needs).",
    # 404
    "nf.title": "Page not found",
    "nf.lead_html": 'There is nothing at this address. Try the <a href="/">latest receipts</a>, '
                    'the <a href="/studio">studio</a> or the <a href="/help">help page</a>.',
    # studio
    "studio.title": "Studio",
    "studio.judge": "Judge sign-in",
    "studio.login_off": "Sign-in is disabled on this instance; runs below are read-only.",
    "studio.brief": "1 · Brief",
    "studio.record": "Record a voice brief", "studio.stop": "Stop and transcribe",
    "studio.voice_hint": "Speak in Polish or English (up to 60 s here; the server accepts "
                         "10 min / 32 MB). Muse Voice Transcribe writes the brief; the audio is "
                         "deleted after transcription.",
    "studio.turns": "Transcript turns",
    "studio.brief_label": "Campaign brief (type it, or record it above)",
    "studio.brief_default": "Autumn campaign for Demo Ceramics Studio: hand-thrown stoneware "
                            "mugs, 89 PLN, free delivery in Poland above 150 PLN. Audience: "
                            "people working from home in Poland, 25-55. Budget 30 PLN per day. "
                            "Facebook and Instagram feed plus stories.",
    "studio.brief_help": "The producer plans, the copywriter writes, the art director cuts the "
                         "{concept} out of the product photo with SAM 3.1 and renders three "
                         "placements with Muse Image, two reviewers check it. Nothing is "
                         "published: the run ends in an approval only you can sign.",
    "studio.run": "Run the studio",
    "studio.open_label": "or follow an existing run id", "studio.open": "Open",
    "studio.hud": "run",
    "studio.step.producer": "Producer", "studio.step.copywriter": "Copywriter",
    "studio.step.art_director": "Art director", "studio.step.reviewers": "Reviewers",
    "studio.step.approval": "Your signature",
    "studio.plan": "2 · Plan", "studio.no_plan": "No plan yet.",
    "studio.copy": "3 · Copy variants", "studio.no_copy": "No copy yet.",
    "studio.images": "4 · Creatives (SAM 3.1 cut-out → Muse Image)",
    "studio.no_images": "No images yet.",
    "studio.reviews": "5 · Reviews", "studio.no_review": "No review yet.",
    "studio.approve": "6 · Approve and sign",
    "studio.approve_lead": "You sign the exact bytes below with a key that never leaves this "
                           "browser (WebCrypto Ed25519, non-extractable). The bytes bind the "
                           "content hash, the chain head of the receipts that produced it, your "
                           "key id and a one-time nonce.",
    "studio.ap.action": "action", "studio.ap.sha": "content sha256",
    "studio.ap.head": "chain head", "studio.ap.key": "your key", "studio.ap.exp": "expires",
    "studio.ap.content": "Content you are approving (campaign stays PAUSED)",
    "studio.ap.signed": "signed", "studio.ap.bytes": "Exact signed bytes",
    "studio.ap.button": "Approve and sign", "studio.ap.signature": "Signature",
    "studio.ap.receipt": "Approval receipt",
    "studio.campaign": "7 · Campaign (PAUSED) and preview",
    "studio.activation_hint": "Activation is a second signature, refused above the "
                              "daily-budget ceiling.",
    "studio.activate": "Request activation and sign",
    "studio.ticker": "Receipts (live)",
    "studio.ticker_caption": "Every model call, tool call, review and approval of this run.",
    "studio.col.model_tool": "model / tool", "studio.total": "Total",
    # inbox
    "inbox.title": "Inbox",
    "inbox.lead": "Comments under the ads. The community agent reads each one inside an "
                  "untrusted envelope, with no tools: it can only write a draft. Sending a "
                  "reply or hiding a comment needs your signature on the exact text. Meta "
                  "adapter:",
    "inbox.read_only": "Read-only view. E-mails and phone numbers are redacted.",
    "inbox.simulate": "Simulate a comment", "inbox.add": "Add",
    "inbox.list": "Comments", "inbox.intent": "intent", "inbox.sentiment": "sentiment",
    "inbox.risk": "risk", "inbox.human": "needs a human", "inbox.triage": "Triage",
    "inbox.draft_label": "Draft reply (edit before signing)",
    "inbox.send": "Approve & send reply", "inbox.hide": "Hide comment",
    "inbox.signed_content": "Exact signed content", "inbox.draft": "Draft:",
    "inbox.empty": "No comments yet. Add one with “Simulate a comment” above, or poll a Page "
                   "post once a Meta adapter is connected. Every reply is drafted by the "
                   "community agent and sent only after you sign it.",
    # leads
    "leads.title": "Leads (CRM)",
    "leads.lead": "Scores come from fixed rules (base 20; +20 e-mail; +25 phone; +10 city; "
                  "+15 UK; +10 business; −30 private). Muse Spark only explains them: it never "
                  "sees the e-mail address or phone number and cannot change the score. HOT "
                  "(≥ 80) gets a \"call within 30 min\" task.",
    "leads.read_only": "Read-only view; contact data redacted.",
    "leads.cards": "Lead cards", "leads.not_scored": "not scored",
    "leads.source": "source", "leads.comment": "comment", "leads.email": "e-mail",
    "leads.phone": "phone", "leads.city": "city", "leads.company": "company",
    "leads.persona": "persona", "leads.owner": "owner", "leads.next": "next action",
    "leads.task": "task:", "leads.score": "Score", "leads.rescore": "Re-score",
    "leads.empty": "No leads yet. A comment the community agent triages as a lead becomes a "
                   "scored CRM card here (rules decide the score, Muse Spark writes the "
                   "reasons).",
    # report
    "report.title": "Report",
    "report.lead_html": "The analyst (Muse Spark 1.3, reasoning effort high) reads the whole "
                        "studio ledger — every receipt — plus the ad insights and, if you add "
                        "one, an Ads Manager screenshot, in <strong>one prompt</strong>. "
                        "Evidence links are receipt ids that were in that prompt; anything "
                        "else the model cites is listed as unverified.",
    "report.shot": "Ads Manager screenshot (PNG/JPEG, optional, ≤ 5 MB)",
    "report.write": "Write the report",
    "report.read": "Read {tokens} tokens in one prompt",
    "report.cached": "cached", "report.context": "context", "report.receipts": "receipts",
    "report.dropped": "oldest left out", "report.screenshot": "screenshot",
    "report.yes": "yes", "report.no": "no", "report.insights": "insights",
    "report.synthetic": "demo fixture, not Meta data", "report.output": "output tokens",
    "report.reasoning": "reasoning", "report.model_receipt": "model receipt",
    "report.wins": "Wins", "report.losses": "Losses", "report.recs": "Recommendations",
    "report.evidence": "Evidence (receipt ids)", "report.none": "none",
    "report.unverified": "Unverified refs (not in the prompt):",
    "report.summary": "Reasoning summary",
    "report.empty": "No report yet.",
    "report.empty_judge": "Write one above: the analyst reads every receipt in one "
                          "long-context prompt.",
    "report.empty_visitor": "Once a judge writes one, the analyst’s summary, KPIs and "
                            "evidence links appear here.",
    # receipts
    "receipts.title": "Receipts",
    "receipts.lead_html": "Every model call, tool call, review, human approval and external "
                          "write leaves a receipt: hash-chained per run and signed with the "
                          "ledger key. Hover a hash for the full value; copy any receipt as "
                          "JSON. Check the whole export offline with <code>studio verify "
                          "receipts.jsonl --server &lt;this site&gt;</code>.",
    "receipts.visitor": "visitor view: e-mails and phone numbers redacted",
    "receipts.filter": "Filter receipts", "receipts.run": "Run", "receipts.all_runs": "all runs",
    "receipts.kind": "Kind", "receipts.all_kinds": "all kinds",
    "receipts.filter_button": "Filter", "receipts.clear": "Clear filter",
    "receipts.showing": "Showing {shown} of {matched} matching receipts ({total} in the "
                        "ledger).",
    "receipts.newest_html": "Only the newest {limit} are shown; the full log is "
                            '<a href="/api/receipts.jsonl">receipts.jsonl</a>.',
    "receipts.list": "Receipt list", "receipts.table": "Receipts table, scrolls sideways",
    "receipts.caption": "Receipts, newest first",
    "receipts.envelope_title": "customer text was wrapped in the untrusted-content envelope",
    "receipts.envelope": "envelope", "receipts.copy": "Copy",
    "receipts.copy_label": "Copy receipt {id} as JSON",
    "receipts.empty_html": 'No receipts yet. Start a run in the <a href="/studio">studio</a> '
                           'or triage a comment in the <a href="/inbox">inbox</a>; every call '
                           "appears here.",
    "receipts.no_match_html": 'No receipts match this filter. <a href="/receipts">Show all '
                              "receipts</a>.",
    # cast gallery
    "cast.title": "The cast",
    "cast.lead": "Owners of one-person businesses, drawn as friendly monoline people who "
                 "stand side by side and never overlap. The dotted trace carries the agents as "
                 "diamonds and the owner's signature as the last node; the ad on the right is "
                 "complete only after it. This page is not indexed.",
    "cast.groups": "groups", "cast.scenes": "scenes",
    # help page chrome
    "help.title": "Help",
    "help.lead": "Plain answers for the owner of a one-person business. Each answer says "
                 "what the studio does today; anything not built yet says so.",
    "help.toc": "Questions", "help.back": "Back to the questions",
    # scripts
    "js.signing_in": "Signing in…", "js.sign_in_failed": "Sign-in failed ({error})",
    "js.signed_in_key": "Signed in. Your approver key is {key}.",
    "js.your_key": "Your approver key is {key}.",
    "js.audience": "Audience: {value}",
    "js.budget": "Budget: {pln} PLN / day · placements: {placements}",
    "js.reasoning": "Reasoning summary: {text}", "js.sent_for_review": " (sent for review)",
    "js.risks": "Risks: {list}",
    "js.cutout_alt": "SAM 3.1 cut-out of the product ({px} foreground pixels)",
    "js.cutout_caption": "SAM 3.1 cut-out · {px} px",
    "js.creative_alt": "Ad creative for {placement} ({w}×{h})",
    "js.no_review": "No review yet.", "js.combined": "Combined verdict ",
    "js.spark": "Muse Spark 1.3 (vision)", "js.spark_open": "Muse Spark Open on Baseten "
                                                             "(open weights)",
    "js.second_opinion": "Second opinion {state} (Muse Spark Open on Baseten).",
    "js.disagreement": "Disagreement: {text}",
    "js.sign_to_approve": "Sign in to approve this run.", "js.ready": "Ready to sign.",
    "js.already": "Already {status}.", "js.item": "{action} (item {id})",
    "js.signing": "Signing in the browser…",
    "js.signed_executed": "Signed and verified. The media buyer created the campaign, ad set, "
                          "creative and ad — all PAUSED — via the {adapter} adapter; nothing "
                          "went live.",
    "js.signed_failed": "Signed, but the media buyer refused or failed: {error}",
    "js.signed_only": "Signed and verified by the server. The campaign is approved (PAUSED); "
                      "nothing went live.",
    "js.approved": "{run}: approved", "js.following": "Following {run}…",
    "js.connection_lost": "Connection lost; reload to resume.", "js.starting": "Starting…",
    "js.cannot_start": "Cannot start: {error}",
    "js.no_recording": "This browser cannot record audio.",
    "js.too_big": "Recording is larger than 32 MB.",
    "js.transcribing": "Transcribing with Muse Voice Transcribe…",
    "js.transcription_failed": "Transcription failed: {error}",
    "js.transcribed": "Transcribed {seconds} s for ${cost}.", "js.recording": "Recording…",
    "js.no_mic": "Microphone not available: {error}",
    "js.campaign": "{name} · status {status} · {budget} PLN/day · adapter {adapter} · "
                   "ceiling {ceiling} PLN/day",
    "js.preview_title": "Meta ad preview", "js.preview_alt": "Ad image (feed 1:1)",
    "js.mock_preview": "Mock preview rendered by the studio (no Meta call).",
    "js.no_preview": "Preview unavailable ({error}); the PAUSED ad is in Ads Manager.",
    "js.activation_refused": "activation refused: {error}",
    "js.signing_activation": "Signing activation of {budget} PLN/day…",
    "js.not_activated": "not activated: {error}",
    "js.activated": "Activated via the {adapter} adapter (signature {sig}…).",
    "js.signing_label": "Signing {label} in the browser…",
    "js.triaging": "Triaging (one Muse Spark call, no tools)…",
    "js.reply": "Reply", "js.hide": "Hide", "js.cannot_add": "Cannot add: {error}",
    "js.scoring": "Scoring (rules + one Muse Spark rationale call)…",
    "js.scoring_failed": "Scoring failed: {error}",
    "js.shot_too_big": "Screenshot is larger than 5 MB.",
    "js.analyst_reading": "The analyst is reading the whole ledger in one prompt (reasoning "
                          "effort high)…",
    "js.report_failed": "Report failed: {error}", "js.copied": "Receipt copied as JSON.",
    "js.clipboard": "Clipboard unavailable: the JSON is selected, press Ctrl+C.",
    "js.tokens_unit": "tokens",
    "js.audio_kept": "The audio is kept (STUDIO_KEEP_AUDIO).",
    "js.audio_deleted": "The audio was deleted.",
    "js.edit_then_run": "Edit the brief, then run the studio.",
    "js.label_signed": "{label} signed ({sig}…).",
    "js.sent_via": "Sent via the {adapter} adapter.", "js.not_sent": "Not sent: {error}.",
    "js.no_sender": "Approved; no sender configured.",
    "js.no_webcrypto": "WebCrypto is not available in this browser.",
    "js.triage_failed": "triage failed: {error}", "js.cannot_prepare": "cannot prepare: {error}",
}

PL: dict[str, str] = {
    "brand": "Muse Studio Lite",
    "nav.studio": "Studio", "nav.inbox": "Skrzynka", "nav.leads": "Leady",
    "nav.report": "Raport", "nav.receipts": "Pokwitowania", "nav.help": "Pomoc",
    "nav.label": "Główna",
    "tagline": "każdy agent zostawia pokwitowanie · każdą wysyłkę podpisuje człowiek",
    "badge.read_only": "tylko odczyt", "badge.replay": "nagranie",
    "badge.replay_title": "Odpowiedzi Model API to nagrane odpowiedzi podawane offline "
                          "(STUDIO_REPLAY=1)",
    "auth.sign_in": "Zaloguj", "auth.sign_out": "Wyloguj",
    "lang.label": "Język",
    "foot.ledger": "Rejestr", "foot.seq": "manifest seq", "foot.manifest": "manifest",
    "foot.key": "klucz rejestru",
    "common.receipt_cols.time": "czas (UTC)", "common.receipt_cols.run": "przebieg",
    "common.receipt_cols.agent": "agent", "common.receipt_cols.kind": "rodzaj",
    "common.receipt_cols.model": "dostawca · model", "common.receipt_cols.tier": "poziom",
    "common.receipt_cols.tokens": "tokeny we/wy", "common.receipt_cols.cost": "koszt",
    "common.receipt_cols.hash": "skrót",
    "index.kicker": "dwa widoki każdej reklamy",
    "index.lead": "Agenci Muse planują, piszą, rysują i recenzują. Nic nie wychodzi ze studia, "
                  "dopóki człowiek nie podpisze dokładnej treści.",
    "index.signed_in": "Jesteś zalogowany jako juror.",
    "index.judges": "Jurorzy logują się, żeby uruchomić studio.",
    "index.login_off": "Logowanie jest wyłączone na tej instancji.",
    "index.help_html": 'Kto co sprawdza i ile to kosztuje: <a href="/help">pomoc</a>.',
    "index.art": "Czworo właścicieli małych firm obok siebie, czterej agenci jako romby na "
                 "kropkowanym śladzie, podpis właściciela jako ostatni węzeł i reklama, która "
                 "jest kompletna dopiero po podpisie.",
    "index.latest": "Najnowsze pokwitowania",
    "index.all": "Wszystkie pokwitowania, filtr po przebiegu, kopia jako JSON →",
    "index.empty_html": 'Nie ma jeszcze pokwitowań. Uruchom przebieg w <a href="/studio">'
                        'studiu</a> albo posegreguj komentarz w <a href="/inbox">skrzynce</a>; '
                        "każde wywołanie modelu i narzędzia, recenzja, podpis i zapis pojawi "
                        "się tutaj.",
    "login.title": "Zaloguj się",
    "login.hint": "Jurorzy: hasło jest w formularzu zgłoszenia. Nic nie zostanie "
                  "opublikowane bez Twojego podpisu; kampanie powstają wstrzymane.",
    "login.user": "Nazwa użytkownika", "login.password": "Hasło",
    "login.failed": "Logowanie nie powiodło się. Sprawdź nazwę użytkownika i hasło albo "
                    "spróbuj ponownie za minutę.",
    "login.disabled": "Logowanie jest wyłączone na tej instancji.",
    "login.public_html": 'Publiczne i tylko do odczytu bez logowania: <a href="/api/ledger-key">'
                         'klucz rejestru</a>, <a href="/api/ledger-manifest">manifest</a>, '
                         '<a href="/api/approver-keys">klucze zatwierdzających</a> (tego '
                         "potrzebuje <code>studio verify --server</code>).",
    "nf.title": "Nie ma takiej strony",
    "nf.lead_html": 'Pod tym adresem nic nie ma. Zajrzyj do <a href="/">najnowszych '
                    'pokwitowań</a>, <a href="/studio">studia</a> albo <a href="/help">pomocy'
                    "</a>.",
    "studio.title": "Studio",
    "studio.judge": "Logowanie jurora",
    "studio.login_off": "Logowanie jest wyłączone na tej instancji; przebiegi poniżej są "
                        "tylko do odczytu.",
    "studio.brief": "1 · Brief",
    "studio.record": "Nagraj brief głosem", "studio.stop": "Zatrzymaj i przepisz",
    "studio.voice_hint": "Mów po polsku albo po angielsku (tutaj do 60 s; serwer przyjmuje "
                         "10 min / 32 MB). Muse Voice Transcribe pisze brief; nagranie jest "
                         "usuwane po transkrypcji.",
    "studio.turns": "Fragmenty transkrypcji",
    "studio.brief_label": "Brief kampanii (wpisz albo nagraj powyżej)",
    "studio.brief_default": "Jesienna kampania dla Demo Ceramics Studio: ręcznie toczone "
                            "kubki z kamionki, 89 zł, darmowa dostawa w Polsce powyżej 150 zł. "
                            "Odbiorcy: osoby pracujące z domu w Polsce, 25-55 lat. Budżet 30 zł "
                            "dziennie. Feed i relacje na Facebooku i Instagramie.",
    "studio.brief_help": "Producent planuje, copywriter pisze, dyrektor artystyczny wycina "
                         "{concept} ze zdjęcia produktu za pomocą SAM 3.1 i rysuje trzy "
                         "formaty w Muse Image, dwóch recenzentów to sprawdza. Nic nie jest "
                         "publikowane: przebieg kończy się zatwierdzeniem, które możesz "
                         "podpisać tylko Ty.",
    "studio.run": "Uruchom studio",
    "studio.open_label": "albo śledź istniejący przebieg", "studio.open": "Otwórz",
    "studio.hud": "przebieg",
    "studio.step.producer": "Producent", "studio.step.copywriter": "Copywriter",
    "studio.step.art_director": "Dyrektor artystyczny", "studio.step.reviewers": "Recenzenci",
    "studio.step.approval": "Twój podpis",
    "studio.plan": "2 · Plan", "studio.no_plan": "Nie ma jeszcze planu.",
    "studio.copy": "3 · Warianty tekstu", "studio.no_copy": "Nie ma jeszcze tekstów.",
    "studio.images": "4 · Kreacje (wycięcie SAM 3.1 → Muse Image)",
    "studio.no_images": "Nie ma jeszcze obrazów.",
    "studio.reviews": "5 · Recenzje", "studio.no_review": "Nie ma jeszcze recenzji.",
    "studio.approve": "6 · Zatwierdź i podpisz",
    "studio.approve_lead": "Podpisujesz dokładnie te bajty kluczem, który nigdy nie opuszcza "
                           "tej przeglądarki (WebCrypto Ed25519, nieeksportowalny). Bajty "
                           "wiążą skrót treści, czoło łańcucha pokwitowań, które ją wytworzyły, "
                           "identyfikator Twojego klucza i jednorazowy nonce.",
    "studio.ap.action": "akcja", "studio.ap.sha": "sha256 treści",
    "studio.ap.head": "czoło łańcucha", "studio.ap.key": "Twój klucz",
    "studio.ap.exp": "wygasa",
    "studio.ap.content": "Treść, którą zatwierdzasz (kampania zostaje WSTRZYMANA)",
    "studio.ap.signed": "podpisano", "studio.ap.bytes": "Dokładne podpisywane bajty",
    "studio.ap.button": "Zatwierdź i podpisz", "studio.ap.signature": "Podpis",
    "studio.ap.receipt": "Pokwitowanie zatwierdzenia",
    "studio.campaign": "7 · Kampania (WSTRZYMANA) i podgląd",
    "studio.activation_hint": "Włączenie to drugi podpis, odrzucany powyżej limitu budżetu "
                              "dziennego.",
    "studio.activate": "Poproś o włączenie i podpisz",
    "studio.ticker": "Pokwitowania (na żywo)",
    "studio.ticker_caption": "Każde wywołanie modelu i narzędzia, recenzja i zatwierdzenie "
                             "tego przebiegu.",
    "studio.col.model_tool": "model / narzędzie", "studio.total": "Razem",
    "inbox.title": "Skrzynka",
    "inbox.lead": "Komentarze pod reklamami. Agent społeczności czyta każdy w kopercie dla "
                  "niezaufanej treści, bez narzędzi: może tylko napisać szkic. Wysłanie "
                  "odpowiedzi albo ukrycie komentarza wymaga Twojego podpisu pod dokładnym "
                  "tekstem. Adapter Mety:",
    "inbox.read_only": "Widok tylko do odczytu. Adresy e-mail i numery telefonów są ukryte.",
    "inbox.simulate": "Symuluj komentarz", "inbox.add": "Dodaj",
    "inbox.list": "Komentarze", "inbox.intent": "intencja", "inbox.sentiment": "nastrój",
    "inbox.risk": "ryzyko", "inbox.human": "potrzebny człowiek", "inbox.triage": "Posegreguj",
    "inbox.draft_label": "Szkic odpowiedzi (popraw przed podpisem)",
    "inbox.send": "Zatwierdź i wyślij odpowiedź", "inbox.hide": "Ukryj komentarz",
    "inbox.signed_content": "Dokładna podpisywana treść", "inbox.draft": "Szkic:",
    "inbox.empty": "Nie ma jeszcze komentarzy. Dodaj jeden przez „Symuluj komentarz” powyżej "
                   "albo pobierz komentarze z posta, gdy adapter Mety jest podłączony. Każdą "
                   "odpowiedź szkicuje agent społeczności, a wysyła ją dopiero Twój podpis.",
    "leads.title": "Leady (CRM)",
    "leads.lead": "Punkty liczą stałe reguły (bazowo 20; +20 e-mail; +25 telefon; +10 miasto; "
                  "+15 UK; +10 firma; −30 osoba prywatna). Muse Spark tylko je uzasadnia: nie "
                  "widzi adresu e-mail ani numeru telefonu i nie może zmienić wyniku. HOT "
                  "(≥ 80) dostaje zadanie „zadzwoń w ciągu 30 min”.",
    "leads.read_only": "Widok tylko do odczytu; dane kontaktowe ukryte.",
    "leads.cards": "Karty leadów", "leads.not_scored": "bez oceny",
    "leads.source": "źródło", "leads.comment": "komentarz", "leads.email": "e-mail",
    "leads.phone": "telefon", "leads.city": "miasto", "leads.company": "firma",
    "leads.persona": "persona", "leads.owner": "opiekun", "leads.next": "następny krok",
    "leads.task": "zadanie:", "leads.score": "Oceń", "leads.rescore": "Oceń ponownie",
    "leads.empty": "Nie ma jeszcze leadów. Komentarz, który agent społeczności uzna za lead, "
                   "staje się tutaj ocenioną kartą CRM (wynik liczą reguły, uzasadnienie pisze "
                   "Muse Spark).",
    "report.title": "Raport",
    "report.lead_html": "Analityk (Muse Spark 1.3, wysoki wysiłek rozumowania) czyta cały "
                        "rejestr studia — każde pokwitowanie — razem z danymi reklam i, jeśli "
                        "dodasz, zrzutem ekranu z Ads Managera, w <strong>jednym promptcie"
                        "</strong>. Dowody to identyfikatory pokwitowań, które były w tym "
                        "promptcie; wszystko inne, na co powoła się model, jest oznaczone jako "
                        "niezweryfikowane.",
    "report.shot": "Zrzut ekranu z Ads Managera (PNG/JPEG, opcjonalnie, ≤ 5 MB)",
    "report.write": "Napisz raport",
    "report.read": "Przeczytano {tokens} tokenów w jednym promptcie",
    "report.cached": "z pamięci podręcznej", "report.context": "kontekst",
    "report.receipts": "pokwitowań", "report.dropped": "najstarszych pominięto",
    "report.screenshot": "zrzut ekranu", "report.yes": "tak", "report.no": "nie",
    "report.insights": "dane reklam", "report.synthetic": "dane demonstracyjne, nie z Mety",
    "report.output": "tokenów wyjścia", "report.reasoning": "rozumowanie",
    "report.model_receipt": "pokwitowanie modelu",
    "report.wins": "Sukcesy", "report.losses": "Straty", "report.recs": "Zalecenia",
    "report.evidence": "Dowody (identyfikatory pokwitowań)", "report.none": "brak",
    "report.unverified": "Niezweryfikowane odwołania (spoza promptu):",
    "report.summary": "Podsumowanie rozumowania",
    "report.empty": "Nie ma jeszcze raportu.",
    "report.empty_judge": "Napisz go powyżej: analityk czyta każde pokwitowanie w jednym "
                          "długim promptcie.",
    "report.empty_visitor": "Gdy juror go napisze, pojawią się tu podsumowanie analityka, "
                            "wskaźniki i odwołania do dowodów.",
    "receipts.title": "Pokwitowania",
    "receipts.lead_html": "Każde wywołanie modelu i narzędzia, recenzja, zatwierdzenie przez "
                          "człowieka i zapis zewnętrzny zostawia pokwitowanie: połączone "
                          "skrótami w łańcuch dla każdego przebiegu i podpisane kluczem "
                          "rejestru. Najedź na skrót, by zobaczyć całość; każde pokwitowanie "
                          "skopiujesz jako JSON. Cały eksport sprawdzisz offline poleceniem "
                          "<code>studio verify receipts.jsonl --server &lt;ta strona&gt;"
                          "</code>.",
    "receipts.visitor": "widok gościa: adresy e-mail i numery telefonów ukryte",
    "receipts.filter": "Filtruj pokwitowania", "receipts.run": "Przebieg",
    "receipts.all_runs": "wszystkie przebiegi", "receipts.kind": "Rodzaj",
    "receipts.all_kinds": "wszystkie rodzaje",
    "receipts.filter_button": "Filtruj", "receipts.clear": "Wyczyść filtr",
    "receipts.showing": "Widać {shown} z {matched} pasujących pokwitowań ({total} w "
                        "rejestrze).",
    "receipts.newest_html": "Pokazano tylko {limit} najnowszych; pełny log to "
                            '<a href="/api/receipts.jsonl">receipts.jsonl</a>.',
    "receipts.list": "Lista pokwitowań",
    "receipts.table": "Tabela pokwitowań, przewija się w bok",
    "receipts.caption": "Pokwitowania, najnowsze najpierw",
    "receipts.envelope_title": "tekst klienta został owinięty kopertą dla niezaufanej treści",
    "receipts.envelope": "koperta", "receipts.copy": "Kopiuj",
    "receipts.copy_label": "Kopiuj pokwitowanie {id} jako JSON",
    "receipts.empty_html": 'Nie ma jeszcze pokwitowań. Uruchom przebieg w <a href="/studio">'
                           'studiu</a> albo posegreguj komentarz w <a href="/inbox">skrzynce'
                           "</a>; każde wywołanie pojawi się tutaj.",
    "receipts.no_match_html": 'Żadne pokwitowanie nie pasuje do filtra. <a href="/receipts">'
                              "Pokaż wszystkie</a>.",
    "cast.title": "Obsada",
    "cast.lead": "Właściciele jednoosobowych firm narysowani jako przyjazne postaci jedną "
                 "linią, które stoją obok siebie i nigdy na siebie nie nachodzą. Kropkowany "
                 "ślad niesie agentów jako romby i podpis właściciela jako ostatni węzeł; "
                 "reklama po prawej jest kompletna dopiero po nim. Ta strona nie jest "
                 "indeksowana.",
    "cast.groups": "grupy", "cast.scenes": "sceny",
    "help.title": "Pomoc",
    "help.lead": "Proste odpowiedzi dla właściciela jednoosobowej firmy. Każda odpowiedź "
                 "mówi, co studio robi dziś; to, czego jeszcze nie ma, jest tak oznaczone.",
    "help.toc": "Pytania", "help.back": "Wróć do pytań",
    "js.signing_in": "Logowanie…", "js.sign_in_failed": "Logowanie nie powiodło się ({error})",
    "js.signed_in_key": "Zalogowano. Twój klucz zatwierdzającego to {key}.",
    "js.your_key": "Twój klucz zatwierdzającego to {key}.",
    "js.audience": "Odbiorcy: {value}",
    "js.budget": "Budżet: {pln} zł / dzień · formaty: {placements}",
    "js.reasoning": "Podsumowanie rozumowania: {text}",
    "js.sent_for_review": " (wysłany do recenzji)", "js.risks": "Ryzyka: {list}",
    "js.cutout_alt": "Wycięcie produktu przez SAM 3.1 ({px} pikseli pierwszego planu)",
    "js.cutout_caption": "Wycięcie SAM 3.1 · {px} px",
    "js.creative_alt": "Kreacja reklamy dla {placement} ({w}×{h})",
    "js.no_review": "Nie ma jeszcze recenzji.", "js.combined": "Werdykt łączny ",
    "js.spark": "Muse Spark 1.3 (widzenie)",
    "js.spark_open": "Muse Spark Open na Basetenie (otwarte wagi)",
    "js.second_opinion": "Druga opinia: {state} (Muse Spark Open na Basetenie).",
    "js.disagreement": "Różnica zdań: {text}",
    "js.sign_to_approve": "Zaloguj się, żeby zatwierdzić ten przebieg.",
    "js.ready": "Gotowe do podpisu.", "js.already": "Już: {status}.",
    "js.item": "{action} (element {id})",
    "js.signing": "Podpisywanie w przeglądarce…",
    "js.signed_executed": "Podpisano i zweryfikowano. Kupiec mediów utworzył kampanię, zestaw "
                          "reklam, kreację i reklamę — wszystko WSTRZYMANE — przez adapter "
                          "{adapter}; nic nie zostało włączone.",
    "js.signed_failed": "Podpisano, ale kupiec mediów odmówił albo zawiódł: {error}",
    "js.signed_only": "Podpisano i zweryfikowano na serwerze. Kampania jest zatwierdzona "
                      "(WSTRZYMANA); nic nie zostało włączone.",
    "js.approved": "{run}: zatwierdzono", "js.following": "Śledzenie {run}…",
    "js.connection_lost": "Połączenie zerwane; odśwież stronę, aby wznowić.",
    "js.starting": "Uruchamianie…", "js.cannot_start": "Nie można uruchomić: {error}",
    "js.no_recording": "Ta przeglądarka nie umie nagrywać dźwięku.",
    "js.too_big": "Nagranie jest większe niż 32 MB.",
    "js.transcribing": "Transkrypcja w Muse Voice Transcribe…",
    "js.transcription_failed": "Transkrypcja nie powiodła się: {error}",
    "js.transcribed": "Przepisano {seconds} s za ${cost}.", "js.recording": "Nagrywanie…",
    "js.no_mic": "Mikrofon niedostępny: {error}",
    "js.campaign": "{name} · status {status} · {budget} zł/dzień · adapter {adapter} · "
                   "limit {ceiling} zł/dzień",
    "js.preview_title": "Podgląd reklamy Mety", "js.preview_alt": "Obraz reklamy (feed 1:1)",
    "js.mock_preview": "Podgląd z atrapy, wyrenderowany przez studio (bez wywołania Mety).",
    "js.no_preview": "Podgląd niedostępny ({error}); WSTRZYMANA reklama jest w Ads Managerze.",
    "js.activation_refused": "włączenie odrzucone: {error}",
    "js.signing_activation": "Podpisywanie włączenia {budget} zł/dzień…",
    "js.not_activated": "nie włączono: {error}",
    "js.activated": "Włączono przez adapter {adapter} (podpis {sig}…).",
    "js.signing_label": "Podpisywanie ({label}) w przeglądarce…",
    "js.triaging": "Segregowanie (jedno wywołanie Muse Spark, bez narzędzi)…",
    "js.reply": "odpowiedź", "js.hide": "ukrycie", "js.cannot_add": "Nie można dodać: {error}",
    "js.scoring": "Ocena (reguły + jedno uzasadnienie od Muse Spark)…",
    "js.scoring_failed": "Ocena nie powiodła się: {error}",
    "js.shot_too_big": "Zrzut ekranu jest większy niż 5 MB.",
    "js.analyst_reading": "Analityk czyta cały rejestr w jednym promptcie (wysoki wysiłek "
                          "rozumowania)…",
    "js.report_failed": "Raport nie powiódł się: {error}",
    "js.copied": "Pokwitowanie skopiowane jako JSON.",
    "js.clipboard": "Schowek niedostępny: JSON jest zaznaczony, naciśnij Ctrl+C.",
    "js.tokens_unit": "tokenów",
    "js.audio_kept": "Nagranie zostaje (STUDIO_KEEP_AUDIO).",
    "js.audio_deleted": "Nagranie zostało usunięte.",
    "js.edit_then_run": "Popraw brief i uruchom studio.",
    "js.label_signed": "Podpisano: {label} ({sig}…).",
    "js.sent_via": "Wysłano przez adapter {adapter}.", "js.not_sent": "Nie wysłano: {error}.",
    "js.no_sender": "Zatwierdzono; nie skonfigurowano wysyłki.",
    "js.no_webcrypto": "Ta przeglądarka nie ma WebCrypto.",
    "js.triage_failed": "segregowanie nie powiodło się: {error}",
    "js.cannot_prepare": "nie można przygotować: {error}",
}

CATALOGS = {"en": EN, "pl": PL}


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT


def text(lang: str, key: str, **values: Any) -> str | Markup:
    raw = CATALOGS[norm(lang)].get(key) or EN[key]
    value = raw.format(**values) if values else raw
    return Markup(value) if key.endswith("_html") else value  # noqa: S704 - trusted catalog


def js_catalog(lang: str) -> dict[str, str]:
    return {k[3:]: v for k, v in CATALOGS[norm(lang)].items() if k.startswith("js.")}


@pass_context
def t(context: Any, key: str, **values: Any) -> str | Markup:
    """Template function: the string for ``key`` in the page's language."""
    return text(page_lang(context), key, **values)


def page_lang(context: Any) -> str:
    if context.get("page_lang"):
        return norm(context["page_lang"])
    request = context.get("request")
    state = getattr(request, "state", None)
    return norm(getattr(state, "lang", None))
