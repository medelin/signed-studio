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

from studio.ui.i18n_accounts import EN_ACCOUNTS, PL_ACCOUNTS
from studio.ui.i18n_google import EN_GOOGLE, PL_GOOGLE
from studio.ui.i18n_mcp import EN_MCP, PL_MCP
from studio.ui.i18n_meta import EN_META, PL_META
from studio.ui.i18n_notify import EN_NOTIFY, PL_NOTIFY
from studio.ui.i18n_onboarding import EN_ONBOARDING, PL_ONBOARDING
from studio.ui.i18n_open import EN_OPEN, PL_OPEN
from studio.ui.i18n_pixel import EN_PIXEL, PL_PIXEL
from studio.ui.i18n_tour import EN_TOUR, PL_TOUR

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
    "theme.label": "Display", "theme.mode": "Mode", "theme.palette": "Colours",
    "theme.system": "auto (system)", "theme.light": "light", "theme.dark": "dark",
    "palette.graphite": "graphite", "palette.warm": "warm paper", "palette.green": "green",
    "palette.contrast": "high contrast", "theme.apply": "Apply",
    "foot.ledger": "Ledger", "foot.seq": "manifest seq", "foot.manifest": "manifest",
    "foot.key": "ledger key", "foot.privacy": "Privacy", "foot.deletion": "Data deletion",
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
                            "mugs, 350 ml, 89 PLN each, free delivery in Poland above 150 PLN. We"
                            " want traffic to the shop website. For people working from home, "
                            "aged 25-55, all of Poland. The mugs are dishwasher safe and we make "
                            "them in small batches. Free gift wrapping. Lightning-fast shipping. "
                            "Budget 30 PLN per day from 3 to 30 November 2026. Warm and concrete,"
                            " no hype. Don't say these are the best mugs in the world.",
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
    "studio.loop": "Reviewer → rewrite loop",
    "loop.mark_blocked": "BLOCK ({rules}) → rewrite",
    "loop.mark_rewrite": "rewrite after BLOCK",
    "loop.mark_after": "second review: {verdict}",
    "js.loop_banner": "Reviewers BLOCKED variant {n} (rule {rules}) → copywriter rewrote → "
                      "{verdict}",
    "js.loop_banner_wait": "Reviewers BLOCKED variant {n} (rule {rules}) → copywriter rewrote → "
                           "second review pending",
    "js.loop_before": "Blocked version", "js.loop_after": "Rewritten version",
    "js.loop_finding": "{rule}: {evidence}",
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
    "js.budget": "Budget: {amount} / day (ad account currency) · placements: {placements}",
    "js.reasoning": "Reasoning summary: {text}", "js.sent_for_review": " (sent for review)",
    "js.risks": "Risks: {list}",
    "js.cutout_alt": "SAM 3.1 cut-out of the product ({px} foreground pixels)",
    "js.cutout_caption": "SAM 3.1 cut-out · {px} px",
    "js.creative_alt": "Ad creative for {placement} ({w}×{h})",
    "js.sam_ok": "SAM 3.1 check: silhouette IoU {iou} vs the cut-out · product {area} % of "
                 "the image",
    "js.sam_people": " · {n} person match(es)",
    "js.sam_edges": " · touches the edge: {edges}",
    "js.sam_missing": "SAM 3.1 check: the product was not found in this image",
    "js.sam_not_recorded": "SAM 3.1 check: not recorded in this replay set",
    "js.sam_skipped": "SAM 3.1 check: skipped (run budget reached)",
    "js.sam_failed": "SAM 3.1 check failed ({error})",
    "js.sam_show": "Show SAM mask", "js.sam_hide": "Hide SAM mask",
    "js.sam_overlay_alt": "SAM 3.1 mask of {placement}: product in blue, people in red",
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
    "js.campaign": "{name} · status {status} · {budget} {currency}/day · adapter {adapter} · "
                   "ceiling {ceiling} {currency}/day",
    "js.preview_title": "Meta ad preview", "js.preview_alt": "Ad image (feed 1:1)",
    "js.mock_preview": "Mock preview rendered by the studio (no Meta call).",
    "js.no_preview": "Preview unavailable ({error}); the PAUSED ad is in Ads Manager.",
    "js.activation_refused": "activation refused: {error}",
    "js.signing_activation": "Signing activation of {budget} {currency}/day…",
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
    # audit: meta, enums, errors, script strings
    "meta.default": "Muse Studio Lite: AI agents plan, write and draw ads for small businesses; a"
                    " person signs every send, and every step leaves a signed receipt.",
    "meta.index": "The latest signed receipts from Muse Studio Lite: every model call, tool call,"
                  " review and human signature, in one hash-chained ledger.",
    "meta.studio": "Write or speak a brief, follow the agents live, then sign the exact campaign "
                   "before anything is created (always paused).",
    "meta.inbox": "Comments under the ads: the community agent drafts a reply, and nothing is "
                  "sent or hidden until a person signs the exact text.",
    "meta.leads": "Leads scored by fixed rules; the model only explains the score and never sees "
                  "the e-mail address or phone number.",
    "meta.report": "One analyst report from the whole receipt ledger and the ad insights, with "
                   "evidence links to receipts that were in the prompt.",
    "meta.receipts": "The full receipt ledger: filter by run or kind, copy any receipt as JSON, "
                     "and verify the export offline.",
    "meta.help": "Plain answers for the owner of a one-person business: what the studio does, "
                 "what it costs, and who checks what.",
    "meta.login": "Sign in to run the studio, triage comments and sign approvals.",
    "meta.cast": "The cast of owners drawn for Muse Studio Lite, in light and dark themes.",
    "meta.nf": "This page does not exist.",
    "receipts.chip_title": "hash {hash} · prev {prev} · id {id}",
    "enum.status.new": "new",
    "enum.status.drafted": "drafted",
    "enum.status.replied": "replied",
    "enum.status.hidden": "hidden",
    "enum.risk.low": "low",
    "enum.risk.medium": "medium",
    "enum.risk.high": "high",
    "enum.sentiment.positive": "positive",
    "enum.sentiment.neutral": "neutral",
    "enum.sentiment.negative": "negative",
    "enum.intent.question": "question",
    "enum.intent.price": "price",
    "enum.intent.lead": "lead",
    "enum.intent.complaint": "complaint",
    "enum.intent.praise": "praise",
    "enum.intent.spam": "spam",
    "enum.intent.other": "other",
    "enum.insights.live": "live",
    "enum.insights.synthetic": "synthetic",
    "enum.severity.info": "info",
    "enum.severity.warn": "warning",
    "enum.severity.block": "blocker",
    "enum.cta.LEARN_MORE": "Learn more",
    "enum.cta.SHOP_NOW": "Shop now",
    "enum.cta.SIGN_UP": "Sign up",
    "enum.cta.CONTACT_US": "Contact us",
    "enum.cta.BOOK_NOW": "Book now",
    "enum.cta.GET_OFFER": "Get offer",
    "enum.run.queued": "queued",
    "enum.run.running": "running",
    "enum.run.awaiting_approval": "awaiting your signature",
    "enum.run.approved": "approved",
    "enum.run.done": "done",
    "enum.run.failed": "failed",
    "enum.run.max_steps": "stopped: step limit reached",
    "enum.run.max_cost": "stopped: cost cap reached",
    "enum.run.timeout": "timed out",
    "enum.scene.signed": "signed",
    "enum.scene.unsigned": "unsigned",
    "enum.scene.blocked": "blocked",
    "enum.scene.verify": "verify",
    "enum.scene.private": "private",
    "enum.scene.budget": "budget",
    "enum.scene.edit": "edit",
    "enum.scene.reset": "reset",
    "enum.scene.lost": "lost",
    "err.login_required": "Sign in first.",
    "err.login_disabled": "Sign-in is disabled on this instance.",
    "err.cross_site": "The request came from another site and was refused.",
    "err.cross_origin": "The request came from another site and was refused.",
    "err.csrf": "The form token is missing or stale; reload the page and try again.",
    "err.read_only": "This instance is read-only.",
    "err.not_found": "Not found.",
    "err.too_many_attempts": "Too many attempts; try again in a minute.",
    "err.body_timeout": "The request took too long.",
    "err.rate_limited": "Too many requests; slow down and try again shortly.",
    "err.invalid_public_key": "That approver key is not valid.",
    "err.too_many_keys": "Too many approver keys for this session.",
    "err.key_conflict": "A different approver key is already registered.",
    "err.invalid_approver": "The approver id is not valid.",
    "err.invalid_brief": "The brief is empty or is not text.",
    "err.brief_too_long": "The brief is too long (2000 characters at most).",
    "err.invalid_brief_id": "The voice brief id is not valid.",
    "err.unknown_voice_brief": "That voice brief is unknown or was already used.",
    "err.model_api_unavailable": "The model API is not configured on this instance.",
    "err.run_in_progress": "A run is already in progress.",
    "err.unsupported_audio_type": "That audio format is not supported.",
    "err.unsupported_media_type": "That file type is not supported.",
    "err.model_error": "The model returned an error.",
    "err.too_many_streams": "Too many live streams are open; close another tab.",
    "err.invalid_signature_format": "The signature is not in the expected format.",
    "err.screenshot_too_large": "The screenshot is larger than 5 MB.",
    "err.invalid_screenshot": "The screenshot could not be read as PNG or JPEG.",
    "err.report_in_progress": "A report is already being written.",
    "err.context_too_large": "The ledger is too large for one prompt.",
    "err.analyst_budget": "The analyst budget for this report is used up.",
    "err.invalid_comment": "The comment is not valid.",
    "err.invalid_request": "The request is not valid.",
    "err.invalid_reply": "The reply is not valid.",
    "err.invalid_object_id": "The object id is not valid.",
    "err.aborted": "The request was aborted.",
    "err.network": "Network error.",
    "err.bad_response": "The model service sent an unreadable answer.",
    "err.invalid_output": "The model returned an answer in the wrong shape.",
    "err.no_api_key": "No model API key is configured.",
    "err.no_image": "The image model returned no image.",
    "err.reasoning_only": "The model only reasoned and wrote no answer.",
    "err.bad_stream": "The model sent a broken stream.",
    "err.empty_box": "The cut-out area is empty.",
    "err.no_match": "The product could not be found in the photo.",
    "err.empty_transcript": "The recording contains no speech.",
    "err.unknown_action": "That action is not known.",
    "err.invalid_item": "The item id is not valid.",
    "err.field_mismatch": "The approval fields do not match.",
    "err.content_mismatch": "The content changed after it was prepared; start again.",
    "err.chain_head_mismatch": "The ledger moved on since this approval was prepared; request it "
                               "again.",
    "err.nonce_mismatch": "The one-time nonce does not match.",
    "err.expired": "The approval expired; request it again.",
    "err.from_future": "The approval carries a time in the future.",
    "err.unknown_key": "That approver key is not registered.",
    "err.key_inactive": "That approver key is no longer active.",
    "err.wrong_session": "The approval belongs to another session.",
    "err.bad_signature": "The signature does not verify.",
    "err.nonce_used": "That approval was already signed.",
    "err.approval_error": "The approval was refused.",
    "err.inbox_no_comment": "No such comment.",
    "err.inbox_unknown_action": "Unknown inbox action.",
    "err.inbox_already": "The comment is already {status}.",
    "err.inbox_reply_length": "A reply must be 1-{max} characters.",
    "err.inbox_reply_pending": "A reply to this comment is already waiting for approval.",
    "err.inbox_comment_length": "A comment must be 1-{max} characters.",
    "js.key_registration_failed": "Key registration failed ({error}).",
    "js.cannot_load_approval": "Cannot load the approval ({error}).",
    "js.no_key": "No approver key.",
    "js.approval_refused": "Approval refused: {error}",
    "js.signed_dump": "content sha256 {sha}\nsigned bytes {bytes}",
    "js.no_preview_default": "no preview",
    "js.no_executor": "no executor",
    "err.adapter_error": "The ad platform adapter reported an error.",
    "err.invalid_credentials": "Wrong user name or password.",
    "err.not_approved": "This item is not approved yet.",
    "err.paused_only": "Only a paused campaign can be activated.",
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
    "theme.label": "Wygląd", "theme.mode": "Tryb", "theme.palette": "Kolory",
    "theme.system": "auto (jak w systemie)", "theme.light": "jasny", "theme.dark": "ciemny",
    "palette.graphite": "grafit", "palette.warm": "ciepły papier", "palette.green": "zielony",
    "palette.contrast": "wysoki kontrast", "theme.apply": "Zastosuj",
    "foot.ledger": "Rejestr", "foot.seq": "manifest seq", "foot.manifest": "manifest",
    "foot.key": "klucz rejestru", "foot.privacy": "Prywatność",
    "foot.deletion": "Usuwanie danych",
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
    "studio.brief_default": "Jesienna kampania Demo Ceramics Studio: ręcznie toczone kubki z "
                            "kamionki, 350 ml, 89 zł za sztukę, darmowa dostawa w Polsce od 150 "
                            "zł. Chcemy ruchu na stronę sklepu. Dla osób pracujących z domu, "
                            "25-55 lat, cała Polska. Kubki można myć w zmywarce i robimy je w "
                            "małych seriach. Pakujemy na prezent gratis. Wysyłka błyskawiczna. "
                            "Budżet 30 zł dziennie od 3 do 30 listopada 2026. Ciepło i "
                            "konkretnie, bez przesady. Nie piszcie, że to najlepsze kubki na "
                            "świecie.",
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
    "studio.loop": "Pętla recenzent → przepisanie",
    "loop.mark_blocked": "BLOCK ({rules}) → przepisanie",
    "loop.mark_rewrite": "przepisanie po BLOCK",
    "loop.mark_after": "druga recenzja: {verdict}",
    "js.loop_banner": "Recenzenci ZABLOKOWALI wariant {n} (reguła {rules}) → copywriter "
                      "przepisał → {verdict}",
    "js.loop_banner_wait": "Recenzenci ZABLOKOWALI wariant {n} (reguła {rules}) → copywriter "
                           "przepisał → druga recenzja w toku",
    "js.loop_before": "Wersja zablokowana", "js.loop_after": "Wersja przepisana",
    "js.loop_finding": "{rule}: {evidence}",
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
    "js.budget": "Budżet: {amount} / dzień (w walucie konta reklamowego) · formaty: "
                 "{placements}",
    "js.reasoning": "Podsumowanie rozumowania: {text}",
    "js.sent_for_review": " (wysłany do recenzji)", "js.risks": "Ryzyka: {list}",
    "js.cutout_alt": "Wycięcie produktu przez SAM 3.1 ({px} pikseli pierwszego planu)",
    "js.cutout_caption": "Wycięcie SAM 3.1 · {px} px",
    "js.creative_alt": "Kreacja reklamy dla {placement} ({w}×{h})",
    "js.sam_ok": "Kontrola SAM 3.1: IoU sylwetki {iou} względem wycięcia · produkt zajmuje "
                 "{area} % obrazu",
    "js.sam_people": " · wykryte osoby: {n}",
    "js.sam_edges": " · dotyka krawędzi: {edges}",
    "js.sam_missing": "Kontrola SAM 3.1: na tym obrazie nie znaleziono produktu",
    "js.sam_not_recorded": "Kontrola SAM 3.1: nie ma jej w tym nagraniu (replay)",
    "js.sam_skipped": "Kontrola SAM 3.1: pominięta (wyczerpany budżet przebiegu)",
    "js.sam_failed": "Kontrola SAM 3.1 nie powiodła się ({error})",
    "js.sam_show": "Pokaż maskę SAM", "js.sam_hide": "Ukryj maskę SAM",
    "js.sam_overlay_alt": "Maska SAM 3.1 dla {placement}: produkt na niebiesko, osoby na "
                          "czerwono",
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
    "js.campaign": "{name} · status {status} · {budget} {currency}/dzień · adapter "
                   "{adapter} · limit {ceiling} {currency}/dzień",
    "js.preview_title": "Podgląd reklamy Mety", "js.preview_alt": "Obraz reklamy (feed 1:1)",
    "js.mock_preview": "Podgląd z atrapy, wyrenderowany przez studio (bez wywołania Mety).",
    "js.no_preview": "Podgląd niedostępny ({error}); WSTRZYMANA reklama jest w Ads Managerze.",
    "js.activation_refused": "włączenie odrzucone: {error}",
    "js.signing_activation": "Podpisywanie włączenia {budget} {currency}/dzień…",
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
    # audit: meta, enums, errors, script strings
    "meta.default": "Muse Studio Lite: agenci AI planują, piszą i rysują reklamy dla małych firm;"
                    " każdą publikację podpisuje człowiek, a każdy krok zostawia podpisane "
                    "pokwitowanie.",
    "meta.index": "Najnowsze podpisane pokwitowania z Muse Studio Lite: każde wywołanie modelu i "
                  "narzędzia, recenzja oraz podpis człowieka w jednym łańcuchu skrótów.",
    "meta.studio": "Napisz lub nagraj brief, śledź agentów na żywo, a potem podpisz dokładną "
                   "treść kampanii, zanim cokolwiek powstanie (zawsze wstrzymanej).",
    "meta.inbox": "Komentarze pod reklamami: agent społeczności przygotowuje odpowiedź, a nic nie"
                  " jest wysyłane ani ukrywane, dopóki człowiek nie podpisze dokładnego tekstu.",
    "meta.leads": "Leady oceniane stałymi regułami; model tylko uzasadnia ocenę i nigdy nie widzi"
                  " adresu e-mail ani numeru telefonu.",
    "meta.report": "Jeden raport analityka z całego rejestru pokwitowań i danych reklam, z "
                   "odnośnikami do pokwitowań, które były w prompcie.",
    "meta.receipts": "Pełny rejestr pokwitowań: filtruj po przebiegu lub rodzaju, kopiuj dowolne "
                     "pokwitowanie jako JSON i sprawdź eksport offline.",
    "meta.help": "Proste odpowiedzi dla właściciela jednoosobowej firmy: co robi studio, ile "
                 "kosztuje i kto co sprawdza.",
    "meta.login": "Zaloguj się, aby uruchamiać studio, segregować komentarze i podpisywać "
                  "zatwierdzenia.",
    "meta.cast": "Obsada właścicieli narysowana dla Muse Studio Lite, w jasnym i ciemnym motywie.",
    "meta.nf": "Takiej strony nie ma.",
    "receipts.chip_title": "skrót {hash} · poprzedni {prev} · id {id}",
    "enum.status.new": "nowy",
    "enum.status.drafted": "szkic gotowy",
    "enum.status.replied": "odpowiedziano",
    "enum.status.hidden": "ukryty",
    "enum.risk.low": "niskie",
    "enum.risk.medium": "średnie",
    "enum.risk.high": "wysokie",
    "enum.sentiment.positive": "pozytywny",
    "enum.sentiment.neutral": "neutralny",
    "enum.sentiment.negative": "negatywny",
    "enum.intent.question": "pytanie",
    "enum.intent.price": "cena",
    "enum.intent.lead": "lead",
    "enum.intent.complaint": "reklamacja",
    "enum.intent.praise": "pochwała",
    "enum.intent.spam": "spam",
    "enum.intent.other": "inne",
    "enum.insights.live": "na żywo",
    "enum.insights.synthetic": "syntetyczne",
    "enum.severity.info": "informacja",
    "enum.severity.warn": "ostrzeżenie",
    "enum.severity.block": "blokada",
    "enum.cta.LEARN_MORE": "Dowiedz się więcej",
    "enum.cta.SHOP_NOW": "Kup teraz",
    "enum.cta.SIGN_UP": "Zapisz się",
    "enum.cta.CONTACT_US": "Skontaktuj się",
    "enum.cta.BOOK_NOW": "Zarezerwuj",
    "enum.cta.GET_OFFER": "Odbierz ofertę",
    "enum.run.queued": "w kolejce",
    "enum.run.running": "w toku",
    "enum.run.awaiting_approval": "czeka na Twój podpis",
    "enum.run.approved": "zatwierdzony",
    "enum.run.done": "gotowy",
    "enum.run.failed": "nie powiódł się",
    "enum.run.max_steps": "zatrzymany: osiągnięto limit kroków",
    "enum.run.max_cost": "zatrzymany: osiągnięto limit kosztu",
    "enum.run.timeout": "przekroczono czas",
    "enum.scene.signed": "podpisane",
    "enum.scene.unsigned": "niepodpisane",
    "enum.scene.blocked": "zablokowane",
    "enum.scene.verify": "weryfikacja",
    "enum.scene.private": "prywatne",
    "enum.scene.budget": "budżet",
    "enum.scene.edit": "edycja",
    "enum.scene.reset": "reset",
    "enum.scene.lost": "zgubione",
    "err.login_required": "Najpierw się zaloguj.",
    "err.login_disabled": "Logowanie jest wyłączone na tej instancji.",
    "err.cross_site": "Żądanie przyszło z innej witryny i zostało odrzucone.",
    "err.cross_origin": "Żądanie przyszło z innej witryny i zostało odrzucone.",
    "err.csrf": "Brakuje tokenu formularza albo jest nieaktualny; odśwież stronę i spróbuj "
                "ponownie.",
    "err.read_only": "Ta instancja działa tylko do odczytu.",
    "err.not_found": "Nie znaleziono.",
    "err.too_many_attempts": "Zbyt wiele prób; spróbuj ponownie za minutę.",
    "err.body_timeout": "Żądanie trwało zbyt długo.",
    "err.rate_limited": "Zbyt wiele żądań; zwolnij i spróbuj za chwilę.",
    "err.invalid_public_key": "Ten klucz zatwierdzającego jest nieprawidłowy.",
    "err.too_many_keys": "Zbyt wiele kluczy zatwierdzającego w tej sesji.",
    "err.key_conflict": "Zarejestrowano już inny klucz zatwierdzającego.",
    "err.invalid_approver": "Identyfikator zatwierdzającego jest nieprawidłowy.",
    "err.invalid_brief": "Brief jest pusty albo nie jest tekstem.",
    "err.brief_too_long": "Brief jest za długi (najwyżej 2000 znaków).",
    "err.invalid_brief_id": "Identyfikator briefu głosowego jest nieprawidłowy.",
    "err.unknown_voice_brief": "Nie ma takiego briefu głosowego albo został już użyty.",
    "err.model_api_unavailable": "API modeli nie jest skonfigurowane na tej instancji.",
    "err.run_in_progress": "Inny przebieg jest już w toku.",
    "err.unsupported_audio_type": "Ten format dźwięku nie jest obsługiwany.",
    "err.unsupported_media_type": "Ten typ pliku nie jest obsługiwany.",
    "err.model_error": "Model zwrócił błąd.",
    "err.too_many_streams": "Otwarto zbyt wiele strumieni na żywo; zamknij inną kartę.",
    "err.invalid_signature_format": "Podpis nie ma oczekiwanego formatu.",
    "err.screenshot_too_large": "Zrzut ekranu jest większy niż 5 MB.",
    "err.invalid_screenshot": "Nie udało się odczytać zrzutu ekranu jako PNG ani JPEG.",
    "err.report_in_progress": "Raport jest już w trakcie pisania.",
    "err.context_too_large": "Rejestr jest za duży na jeden prompt.",
    "err.analyst_budget": "Budżet analityka na ten raport został wyczerpany.",
    "err.invalid_comment": "Komentarz jest nieprawidłowy.",
    "err.invalid_request": "Żądanie jest nieprawidłowe.",
    "err.invalid_reply": "Odpowiedź jest nieprawidłowa.",
    "err.invalid_object_id": "Identyfikator obiektu jest nieprawidłowy.",
    "err.aborted": "Żądanie zostało przerwane.",
    "err.network": "Błąd sieci.",
    "err.bad_response": "Usługa modelu zwróciła nieczytelną odpowiedź.",
    "err.invalid_output": "Model zwrócił odpowiedź w złym formacie.",
    "err.no_api_key": "Nie skonfigurowano klucza API modeli.",
    "err.no_image": "Model obrazu nie zwrócił obrazu.",
    "err.reasoning_only": "Model tylko rozumował i nie napisał odpowiedzi.",
    "err.bad_stream": "Model przysłał uszkodzony strumień.",
    "err.empty_box": "Obszar wycięcia jest pusty.",
    "err.no_match": "Nie znaleziono produktu na zdjęciu.",
    "err.empty_transcript": "Nagranie nie zawiera mowy.",
    "err.unknown_action": "Nieznana akcja.",
    "err.invalid_item": "Identyfikator pozycji jest nieprawidłowy.",
    "err.field_mismatch": "Pola zatwierdzenia się nie zgadzają.",
    "err.content_mismatch": "Treść zmieniła się po przygotowaniu; zacznij od nowa.",
    "err.chain_head_mismatch": "Od przygotowania tego zatwierdzenia w rejestrze pojawiły się "
                               "nowe wpisy; przygotuj je ponownie.",
    "err.nonce_mismatch": "Jednorazowy kod (nonce) się nie zgadza.",
    "err.expired": "Zatwierdzenie wygasło; poproś o nie ponownie.",
    "err.from_future": "Zatwierdzenie ma czas z przyszłości.",
    "err.unknown_key": "Ten klucz zatwierdzającego nie jest zarejestrowany.",
    "err.key_inactive": "Ten klucz zatwierdzającego nie jest już aktywny.",
    "err.wrong_session": "Zatwierdzenie należy do innej sesji.",
    "err.bad_signature": "Podpis się nie weryfikuje.",
    "err.nonce_used": "To zatwierdzenie zostało już podpisane.",
    "err.approval_error": "Zatwierdzenie zostało odrzucone.",
    "err.inbox_no_comment": "Nie ma takiego komentarza.",
    "err.inbox_unknown_action": "Nieznana akcja skrzynki.",
    "err.inbox_already": "Komentarz ma już status: {status}.",
    "err.inbox_reply_length": "Odpowiedź musi mieć od 1 do {max} znaków.",
    "err.inbox_reply_pending": "Odpowiedź na ten komentarz już czeka na zatwierdzenie.",
    "err.inbox_comment_length": "Komentarz musi mieć od 1 do {max} znaków.",
    "js.key_registration_failed": "Rejestracja klucza nie powiodła się ({error}).",
    "js.cannot_load_approval": "Nie można wczytać zatwierdzenia ({error}).",
    "js.no_key": "Brak klucza zatwierdzającego.",
    "js.approval_refused": "Zatwierdzenie odrzucone: {error}",
    "js.signed_dump": "sha256 treści {sha}\npodpisane bajty {bytes}",
    "js.no_preview_default": "brak podglądu",
    "js.no_executor": "brak wykonawcy",
    "err.adapter_error": "Adapter platformy reklamowej zgłosił błąd.",
    "err.invalid_credentials": "Błędna nazwa użytkownika lub hasło.",
    "err.not_approved": "Ta pozycja nie jest jeszcze zatwierdzona.",
    "err.paused_only": "Włączyć można tylko wstrzymaną kampanię.",
}

# Brief v1 strings: their own block (merged below), English and Polish.
EN_BRIEF: dict[str, str] = {
    # Brief v1 (studio page): steps, two input paths, the editable form, [FACT] questions
    "brief.title": "1 · Brief v1",
    "brief.steps_label": "Brief steps",
    "brief.step1": "Tell us (type or speak)", "brief.step2": "Check the draft",
    "brief.step3": "Answer the [FACT] questions", "brief.step4": "Confirm and run",
    "brief.no_time_limit": "No step has a time limit. Everything works with a keyboard and "
                           "a screen reader.",
    "brief.a11y_link": "Accessibility notes",
    "brief.paths_legend": "Your brief: type it or say it",
    "brief.paths_hint": "Both ways are equal. Say or write what you sell, to whom, what it "
                        "costs and what you want from the ad. The producer turns it into a "
                        "Brief v1 that you check before anything else happens.",
    "brief.path_text": "Type", "brief.path_voice": "Speak",
    "brief.text_label": "Your brief, in your own words (a voice transcript lands here too)",
    "brief.text_help": "Edit freely, also after a recording: the producer reads exactly this "
                       "text.",
    "brief.voice_hint": "Speak in Polish or English, up to 10 minutes per recording; you can "
                        "record again to add more. Muse Voice Transcribe writes the text into "
                        "the box on the left, where you can correct it. The audio is deleted "
                        "after transcription.",
    "brief.draft_button": "Draft Brief v1",
    "brief.check_title": "2 · Check your Brief v1",
    "brief.check_lead": "This is the producer's draft. Change anything. The copywriter may use "
                        "only the confirmed facts; every gap is a [FACT] question for you, "
                        "never a guess.",
    "brief.one_per_line": "One item per line.",
    "brief.read_aloud": "Read the brief aloud", "brief.read_stop": "Stop reading",
    "brief.errors_title": "Please fix these fields",
    "brief.s.questions": "3 · [FACT] questions",
    "brief.questions_hint": "Each question is a claim the producer could not confirm from your "
                            "words. Answer it in a short sentence (it becomes a confirmed "
                            "fact), or strike the claim (it will not be used).",
    "brief.no_questions": "No open questions.",
    "brief.s.facts": "Confirmed facts",
    "brief.facts_hint": "Prices, times, guarantees, numbers: the only facts the ad may state.",
    "brief.add_fact": "Add a fact",
    "brief.s.advertiser": "Advertiser",
    "brief.f.kind": "Who advertises", "brief.kind_person": "a person",
    "brief.kind_company": "a company",
    "brief.f.display_name": "Display name",
    "brief.h.display_name": "As on your page or legal name; a mismatch can get ads rejected.",
    "brief.f.countries": "Countries you sell in",
    "brief.h.countries": "Two-letter codes, for example PL, DE, GB.",
    "brief.f.landing_url": "Landing page (https)",
    "brief.s.offer": "Offer",
    "brief.f.product": "Product or service", "brief.f.offer": "Offer",
    "brief.f.valid_until": "Offer valid until",
    "brief.f.reasons": "Reasons to believe",
    "brief.reasons_hint": "Each reason must rest on at least one confirmed fact.",
    "brief.add_reason": "Add a reason",
    "brief.s.objective": "Objective",
    "brief.f.goal": "Meta objective (exactly one)",
    "brief.h.goal": "What the campaign optimises for.",
    "brief.goal.awareness": "awareness", "brief.goal.traffic": "traffic",
    "brief.goal.engagement": "engagement", "brief.goal.leads": "leads",
    "brief.goal.sales": "sales",
    "brief.f.kpi": "How you measure success",
    "brief.h.kpi": "For example: sign-ups from the form.",
    "brief.s.audience": "Audience",
    "brief.f.locations": "Locations", "brief.f.age": "Age range",
    "brief.f.age_min": "from", "brief.f.age_max": "to",
    "brief.f.languages": "Languages",
    "brief.f.who": "Who they are", "brief.f.belief": "What they believe",
    "brief.f.barrier": "What holds them back", "brief.f.trigger": "What would move them",
    "brief.s.proposition": "Proposition",
    "brief.f.proposition": "The one thing the ad says",
    "brief.h.proposition": "One sentence.",
    "brief.s.tone": "Tone and brand",
    "brief.f.tone": "Tone words", "brief.f.colours": "Colours",
    "brief.f.donts": "What not to do",
    "brief.s.mandatories": "Mandatory items",
    "brief.f.disclaimers": "Required disclaimers", "brief.f.banned_claims": "Banned claims",
    "brief.h.banned": "The reviewers block any ad that makes one of these claims.",
    "brief.f.dates": "Dates that must appear",
    "brief.s.budget": "Budget",
    "brief.f.budget_kind": "Budget type", "brief.budget_daily": "daily",
    "brief.budget_lifetime": "total for the whole campaign",
    "brief.f.amount": "Amount",
    "brief.h.amount": "Daily budgets above the server's ceiling are refused.",
    "brief.f.currency": "Currency",
    "brief.h.currency": "The ad account's currency: Meta books budgets in it, so the studio "
                        "never converts.",
    "brief.f.start": "Start date", "brief.f.end": "End date",
    "brief.s.placements": "Placements",
    "brief.f.automatic": "Automatic placements (recommended by Meta)",
    "brief.f.deliverables": "Images to make",
    "brief.pl.feed_1x1": "feed 1:1", "brief.pl.feed_4x5": "feed 4:5",
    "brief.pl.story_9x16": "stories 9:16",
    "brief.s.assets": "Assets",
    "brief.assets_hint": "This studio cuts the {concept} out of its configured product photo "
                         "with SAM 3.1; note here what else you have.",
    "brief.f.assets": "Your assets",
    "brief.confirm_hint": "Confirming records the brief's hash in a receipt. Your signature on "
                          "the ad will cover these facts too: if one changes later, the "
                          "signature no longer counts.",
    "brief.confirm_button": "Confirm the brief and run the studio",
    "brief.back_button": "Back to my words",
    "brief.ap_brief": "brief sha256 (the facts you sign too)",
    # Brief v1, scripts
    "js.brief_drafting": "The producer is drafting your Brief v1 (one Muse Spark call)…",
    "js.brief_drafted": "Draft ready. {n} [FACT] questions need your answer. Step 2 of 4: "
                        "check the brief.",
    "js.brief_drafted_none": "Draft ready, no open questions. Step 2 of 4: check the brief.",
    "js.brief_draft_failed": "Could not draft the brief: {error}",
    "js.brief_step": "Step {n} of 4: {name}",
    "js.brief_question": "Question {n}",
    "js.brief_claim": "Your words: “{text}”",
    "js.brief_answer": "Answer", "js.brief_strike": "Strike the claim",
    "js.brief_answer_label": "Your answer to question {n}",
    "js.brief_choice": "What to do with question {n}",
    "js.brief_struck": "Struck: this claim will not be used.",
    "js.brief_questions_left": "{n} questions still open.",
    "js.brief_questions_done": "All questions answered or struck. Step 4 of 4: confirm.",
    "js.brief_fact_label": "Fact {n}", "js.brief_remove": "Remove",
    "js.brief_remove_fact": "Remove fact {n}",
    "js.brief_reason_label": "Reason {n}", "js.brief_reason_facts": "Rests on facts",
    "js.brief_remove_reason": "Remove reason {n}",
    "js.brief_no_facts": "Add a confirmed fact first.",
    "js.brief_dictate": "Dictate", "js.brief_dictate_into": "Dictate into: {label}",
    "js.brief_dictate_stop": "Stop dictation",
    "js.brief_dictating": "Listening… press Stop dictation when you are done.",
    "js.brief_dictated": "Dictated text added to {label}. Check it before you go on.",
    "js.brief_no_mic": "No microphone is available or it is not allowed in this browser. "
                       "Everything works by typing as well.",
    "js.brief_confirming": "Confirming the brief and starting the run…",
    "js.brief_confirmed": "Brief confirmed (sha256 {sha}…, {facts} facts). The run has "
                          "started.",
    "js.brief_errors": "{n} fields need attention; the list is at the top of the form.",
    "js.brief_reading": "Reading the brief aloud…",
    "js.brief_err_required": "This field is required.",
    "js.brief_err_too_long": "This is too long.",
    "js.brief_err_too_many": "Too many items.",
    "js.brief_err_empty_item": "Remove the empty line.",
    "js.brief_err_bad_url": "Enter an https address, for example https://example.com/.",
    "js.brief_err_bad_country": "Use two-letter country codes, one per line.",
    "js.brief_err_bad_date": "Enter a valid date.",
    "js.brief_err_date_order": "The end date is before the start date.",
    "js.brief_err_age_range": "Ages must be between 13 and 65, from ≤ to.",
    "js.brief_err_amount": "Enter an amount above zero.",
    "js.brief_err_over_ceiling": "Above the daily-budget ceiling of this studio ({ceiling} "
                                 "{currency}).",
    "js.brief_err_bad_currency": "Use a three-letter currency code, for example PLN.",
    "js.brief_err_account_currency": "This studio's ad account books budgets in {currency}; "
                                     "give the budget in {currency}.",
    "js.brief_err_no_fact_ref": "Pick at least one fact this reason rests on.",
    "js.brief_err_bad_fact_ref": "This reason points to a fact that no longer exists.",
    "js.brief_err_one_sentence": "Keep it to one sentence.",
    "js.brief_err_placeholder": "A fact cannot contain [FACT].",
    "js.brief_err_open_question": "Answer or strike every [FACT] question.",
    "js.brief_err_unresolved": "Answer this question or strike the claim.",
    "js.brief_err_answer_required": "Write the answer, or strike the claim instead.",
    "js.brief_err_duplicate": "Each placement once.",
    "js.brief_err_invalid": "This value is not valid.",
    "err.brief_changed": "The confirmed brief changed after this approval was requested; "
                         "confirm the brief again.",
    "err.not_pending": "This approval is no longer waiting for a signature.",
    "err.superseded": "This approval was renewed; sign the renewed one shown on the page.",
    "err.unknown_draft": "This draft is unknown or was already confirmed; draft the brief "
                         "again.",
    "js.brief_edit_then_draft": "Check and edit the transcript, then draft Brief v1.",
    "js.brief_renewed": "The signing window had closed; a fresh one was issued for the same "
                        "content. Sign again when you are ready.",
}

PL_BRIEF: dict[str, str] = {
    # Brief v1 (strona studia): kroki, dwie drogi wejścia, edytowalny formularz, pytania [FACT]
    "brief.title": "1 · Brief v1",
    "brief.steps_label": "Kroki briefu",
    "brief.step1": "Opowiedz (wpisz albo powiedz)", "brief.step2": "Sprawdź szkic",
    "brief.step3": "Odpowiedz na pytania [FACT]", "brief.step4": "Potwierdź i uruchom",
    "brief.no_time_limit": "Żaden krok nie ma limitu czasu. Wszystko działa z klawiatury i z "
                           "czytnikiem ekranu.",
    "brief.a11y_link": "Uwagi o dostępności",
    "brief.paths_legend": "Twój brief: wpisz go albo powiedz",
    "brief.paths_hint": "Obie drogi są równorzędne. Powiedz albo napisz, co sprzedajesz, komu, "
                        "ile to kosztuje i czego oczekujesz od reklamy. Producent zrobi z tego "
                        "Brief v1, który sprawdzisz, zanim cokolwiek się wydarzy.",
    "brief.path_text": "Wpisz", "brief.path_voice": "Powiedz",
    "brief.text_label": "Twój brief, własnymi słowami (tu trafia też transkrypcja nagrania)",
    "brief.text_help": "Poprawiaj śmiało, także po nagraniu: producent czyta dokładnie ten "
                       "tekst.",
    "brief.voice_hint": "Mów po polsku albo po angielsku, do 10 minut na jedno nagranie; "
                        "możesz nagrać jeszcze raz, żeby coś dodać. Muse Voice Transcribe "
                        "wpisze tekst do pola obok, gdzie możesz go poprawić. Nagranie jest "
                        "usuwane po transkrypcji.",
    "brief.draft_button": "Przygotuj Brief v1",
    "brief.check_title": "2 · Sprawdź swój Brief v1",
    "brief.check_lead": "To szkic producenta. Zmień, co chcesz. Copywriter może użyć tylko "
                        "potwierdzonych faktów; każda luka to pytanie [FACT] do Ciebie, nigdy "
                        "zgadywanie.",
    "brief.one_per_line": "Jedna pozycja w wierszu.",
    "brief.read_aloud": "Przeczytaj brief na głos", "brief.read_stop": "Przestań czytać",
    "brief.errors_title": "Popraw te pola",
    "brief.s.questions": "3 · Pytania [FACT]",
    "brief.questions_hint": "Każde pytanie to twierdzenie, którego producent nie mógł "
                            "potwierdzić na podstawie Twoich słów. Odpowiedz krótkim zdaniem "
                            "(stanie się potwierdzonym faktem) albo skreśl twierdzenie (nie "
                            "zostanie użyte).",
    "brief.no_questions": "Brak otwartych pytań.",
    "brief.s.facts": "Potwierdzone fakty",
    "brief.facts_hint": "Ceny, godziny, gwarancje, liczby: jedyne fakty, które może podać "
                        "reklama.",
    "brief.add_fact": "Dodaj fakt",
    "brief.s.advertiser": "Reklamodawca",
    "brief.f.kind": "Kto się reklamuje", "brief.kind_person": "osoba",
    "brief.kind_company": "firma",
    "brief.f.display_name": "Nazwa wyświetlana",
    "brief.h.display_name": "Taka jak na Twojej stronie albo nazwa prawna; niezgodność może "
                            "skończyć się odrzuceniem reklamy.",
    "brief.f.countries": "Kraje, w których sprzedajesz",
    "brief.h.countries": "Dwuliterowe kody, na przykład PL, DE, GB.",
    "brief.f.landing_url": "Strona docelowa (https)",
    "brief.s.offer": "Oferta",
    "brief.f.product": "Produkt lub usługa", "brief.f.offer": "Oferta",
    "brief.f.valid_until": "Oferta ważna do",
    "brief.f.reasons": "Powody, by uwierzyć",
    "brief.reasons_hint": "Każdy powód musi opierać się na co najmniej jednym potwierdzonym "
                          "fakcie.",
    "brief.add_reason": "Dodaj powód",
    "brief.s.objective": "Cel",
    "brief.f.goal": "Cel w Mecie (dokładnie jeden)",
    "brief.h.goal": "Pod co kampania jest optymalizowana.",
    "brief.goal.awareness": "rozpoznawalność", "brief.goal.traffic": "ruch",
    "brief.goal.engagement": "zaangażowanie", "brief.goal.leads": "kontakty (leady)",
    "brief.goal.sales": "sprzedaż",
    "brief.f.kpi": "Jak mierzysz sukces",
    "brief.h.kpi": "Na przykład: zapisy z formularza.",
    "brief.s.audience": "Odbiorcy",
    "brief.f.locations": "Lokalizacje", "brief.f.age": "Przedział wieku",
    "brief.f.age_min": "od", "brief.f.age_max": "do",
    "brief.f.languages": "Języki",
    "brief.f.who": "Kim są", "brief.f.belief": "W co wierzą",
    "brief.f.barrier": "Co ich powstrzymuje", "brief.f.trigger": "Co by ich poruszyło",
    "brief.s.proposition": "Myśl przewodnia",
    "brief.f.proposition": "To jedno, co mówi reklama",
    "brief.h.proposition": "Jedno zdanie.",
    "brief.s.tone": "Ton i marka",
    "brief.f.tone": "Słowa opisujące ton", "brief.f.colours": "Kolory",
    "brief.f.donts": "Czego nie robić",
    "brief.s.mandatories": "Elementy obowiązkowe",
    "brief.f.disclaimers": "Wymagane zastrzeżenia", "brief.f.banned_claims": "Zakazane "
                                                                            "twierdzenia",
    "brief.h.banned": "Recenzenci blokują każdą reklamę, która zawiera któreś z nich.",
    "brief.f.dates": "Daty, które muszą się pojawić",
    "brief.s.budget": "Budżet",
    "brief.f.budget_kind": "Rodzaj budżetu", "brief.budget_daily": "dzienny",
    "brief.budget_lifetime": "łączny na całą kampanię",
    "brief.f.amount": "Kwota",
    "brief.h.amount": "Budżet dzienny powyżej limitu serwera jest odrzucany.",
    "brief.f.currency": "Waluta",
    "brief.h.currency": "Waluta konta reklamowego: Meta rozlicza w niej budżety, więc studio "
                        "niczego nie przelicza.",
    "brief.f.start": "Data startu", "brief.f.end": "Data końca",
    "brief.s.placements": "Umiejscowienia",
    "brief.f.automatic": "Automatyczne umiejscowienia (zalecane przez Metę)",
    "brief.f.deliverables": "Obrazy do przygotowania",
    "brief.pl.feed_1x1": "feed 1:1", "brief.pl.feed_4x5": "feed 4:5",
    "brief.pl.story_9x16": "relacje 9:16",
    "brief.s.assets": "Materiały",
    "brief.assets_hint": "To studio wycina {concept} ze skonfigurowanego zdjęcia produktu za "
                         "pomocą SAM 3.1; zanotuj tu, co jeszcze masz.",
    "brief.f.assets": "Twoje materiały",
    "brief.confirm_hint": "Potwierdzenie zapisuje skrót briefu w pokwitowaniu. Twój podpis pod "
                          "reklamą obejmie także te fakty: jeśli któryś później się zmieni, "
                          "podpis przestaje się liczyć.",
    "brief.confirm_button": "Potwierdź brief i uruchom studio",
    "brief.back_button": "Wróć do moich słów",
    "brief.ap_brief": "sha256 briefu (fakty, które też podpisujesz)",
    # Brief v1, skrypty
    "js.brief_drafting": "Producent przygotowuje Twój Brief v1 (jedno wywołanie Muse Spark)…",
    "js.brief_drafted": "Szkic gotowy. Pytania [FACT] czekające na odpowiedź: {n}. Krok 2 z "
                        "4: sprawdź brief.",
    "js.brief_drafted_none": "Szkic gotowy, bez otwartych pytań. Krok 2 z 4: sprawdź brief.",
    "js.brief_draft_failed": "Nie udało się przygotować briefu: {error}",
    "js.brief_step": "Krok {n} z 4: {name}",
    "js.brief_question": "Pytanie {n}",
    "js.brief_claim": "Twoje słowa: „{text}”",
    "js.brief_answer": "Odpowiedz", "js.brief_strike": "Skreśl twierdzenie",
    "js.brief_answer_label": "Twoja odpowiedź na pytanie {n}",
    "js.brief_choice": "Co zrobić z pytaniem {n}",
    "js.brief_struck": "Skreślone: to twierdzenie nie zostanie użyte.",
    "js.brief_questions_left": "Otwarte pytania: {n}.",
    "js.brief_questions_done": "Na wszystkie pytania odpowiedziano albo je skreślono. Krok 4 "
                               "z 4: potwierdź.",
    "js.brief_fact_label": "Fakt {n}", "js.brief_remove": "Usuń",
    "js.brief_remove_fact": "Usuń fakt {n}",
    "js.brief_reason_label": "Powód {n}", "js.brief_reason_facts": "Opiera się na faktach",
    "js.brief_remove_reason": "Usuń powód {n}",
    "js.brief_no_facts": "Najpierw dodaj potwierdzony fakt.",
    "js.brief_dictate": "Podyktuj", "js.brief_dictate_into": "Podyktuj do pola: {label}",
    "js.brief_dictate_stop": "Zakończ dyktowanie",
    "js.brief_dictating": "Słucham… naciśnij „Zakończ dyktowanie”, gdy skończysz.",
    "js.brief_dictated": "Podyktowany tekst dodano do pola: {label}. Sprawdź go, zanim "
                         "pójdziesz dalej.",
    "js.brief_no_mic": "Mikrofon jest niedostępny albo ta przeglądarka nie ma na niego zgody. "
                       "Wszystko działa także po wpisaniu tekstu.",
    "js.brief_confirming": "Potwierdzam brief i uruchamiam przebieg…",
    "js.brief_confirmed": "Brief potwierdzony (sha256 {sha}…, faktów: {facts}). Przebieg "
                          "wystartował.",
    "js.brief_errors": "Pola wymagające uwagi: {n}; lista jest na górze formularza.",
    "js.brief_reading": "Czytam brief na głos…",
    "js.brief_err_required": "To pole jest wymagane.",
    "js.brief_err_too_long": "To jest za długie.",
    "js.brief_err_too_many": "Za dużo pozycji.",
    "js.brief_err_empty_item": "Usuń pusty wiersz.",
    "js.brief_err_bad_url": "Wpisz adres https, na przykład https://example.com/.",
    "js.brief_err_bad_country": "Użyj dwuliterowych kodów krajów, po jednym w wierszu.",
    "js.brief_err_bad_date": "Wpisz poprawną datę.",
    "js.brief_err_date_order": "Data końca jest wcześniejsza niż data startu.",
    "js.brief_err_age_range": "Wiek musi mieścić się między 13 a 65, „od” nie większe niż "
                              "„do”.",
    "js.brief_err_amount": "Wpisz kwotę większą od zera.",
    "js.brief_err_over_ceiling": "Powyżej dziennego limitu budżetu w tym studiu ({ceiling} "
                                 "{currency}).",
    "js.brief_err_bad_currency": "Użyj trzyliterowego kodu waluty, na przykład PLN.",
    "js.brief_err_account_currency": "Konto reklamowe tego studia rozlicza budżety w "
                                     "{currency}; podaj budżet w {currency}.",
    "js.brief_err_no_fact_ref": "Wybierz co najmniej jeden fakt, na którym opiera się ten "
                                "powód.",
    "js.brief_err_bad_fact_ref": "Ten powód wskazuje fakt, którego już nie ma.",
    "js.brief_err_one_sentence": "Zmieść to w jednym zdaniu.",
    "js.brief_err_placeholder": "Fakt nie może zawierać [FACT].",
    "js.brief_err_open_question": "Odpowiedz na każde pytanie [FACT] albo je skreśl.",
    "js.brief_err_unresolved": "Odpowiedz na to pytanie albo skreśl twierdzenie.",
    "js.brief_err_answer_required": "Wpisz odpowiedź albo zamiast tego skreśl twierdzenie.",
    "js.brief_err_duplicate": "Każde umiejscowienie tylko raz.",
    "js.brief_err_invalid": "Ta wartość jest niepoprawna.",
    "err.brief_changed": "Potwierdzony brief zmienił się po wystawieniu tego zatwierdzenia; "
                         "potwierdź brief jeszcze raz.",
    "err.not_pending": "To zatwierdzenie nie czeka już na podpis.",
    "err.superseded": "To zatwierdzenie zostało odnowione; podpisz odnowione, widoczne na "
                      "stronie.",
    "err.unknown_draft": "Ten szkic jest nieznany albo został już potwierdzony; przygotuj "
                         "brief od nowa.",
    "js.brief_edit_then_draft": "Sprawdź i popraw transkrypcję, a potem przygotuj Brief v1.",
    "js.brief_renewed": "Okno podpisu się zamknęło; wydano nowe dla tej samej treści. "
                        "Podpisz, kiedy zechcesz.",
}

EN.update(EN_BRIEF)
PL.update(PL_BRIEF)
EN.update(EN_ACCOUNTS)
PL.update(PL_ACCOUNTS)
EN.update(EN_META)
PL.update(PL_META)
EN.update(EN_NOTIFY)
PL.update(PL_NOTIFY)
EN.update(EN_GOOGLE)
PL.update(PL_GOOGLE)
EN.update(EN_MCP)
PL.update(PL_MCP)
EN.update(EN_PIXEL)
PL.update(PL_PIXEL)
EN.update(EN_TOUR)
PL.update(PL_TOUR)
EN.update(EN_OPEN)
PL.update(PL_OPEN)
EN.update(EN_ONBOARDING)
PL.update(PL_ONBOARDING)

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
    """The ``js.*`` keys (prefix dropped) plus the ``enum.*`` and ``err.*`` groups as they are."""
    return {(k[3:] if k.startswith("js.") else k): v for k, v in CATALOGS[norm(lang)].items()
            if k.startswith(("js.", "enum.", "err."))}


@pass_context
def tv(context: Any, group: str, value: Any) -> str:
    """Display label for an enum-like value (``enum.<group>.<value>``); unknown values are
    shown as they are."""
    key = f"enum.{group}.{value}"
    lang = page_lang(context)
    return str(CATALOGS[lang].get(key) or CATALOGS[DEFAULT].get(key) or value)


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
