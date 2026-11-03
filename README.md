# Muse Studio Lite

**The AI ad agency that works on Meta's real ad platform — and can't spend a cent without your signature.**

Seven Muse agents plan, write, draw, cut out, review and report; code — never a model —
creates the Facebook/Instagram campaign through the Marketing API, **PAUSED**, and only
after a person signed the exact bytes in the browser. Every step leaves a receipt that
anyone can check offline with `studio verify`.

Built for the Meta Global AI Developer Hackathon 2026 (Indie track · Build for Your World).
**Everything in this repository was built during the event (27 Oct – 4 Nov 2026).**

## Judges: 60-second path

- Sandbox: **https://muse.udun.eu** (login `judge`; the password is in the submission form).
- Built during the event, with dates from git: [HACKATHON.md](HACKATHON.md).

1. Open **Studio**, press "or use an example brief instead", then **Draft my brief**. Answer or strike the questions and press **Confirm the brief and run the studio**. The agents (Producer, Copywriter, Art director, Reviewers) fill sections 2-5.
2. At "6 · Approve and sign" press **Approve and sign**. The key stays in your browser; the signature covers the exact bytes shown.
3. Section "7 · Campaign (PAUSED) and preview" shows the campaign created PAUSED. Then open **Receipts** in the footer, or press **Verify the chain** at https://muse.udun.eu/try (no login).

| Result (from [docs/eval.md](docs/eval.md)) | Value |
|---|---|
| `studio verify` on tampered exports | 600/600 rejected (24 classes × 25) |
| `studio verify` on clean exports | 25/25 accepted |
| Attack suites | 247/247 pass (23 files) |
| Reviewer: bad ads stopped (WARN or BLOCK) | 18/20 (model alone 18/20) |
| Reviewer: clean ads blocked (false positives) | 0/5 |
| Spark vision vs Spark Open (Baseten) agreement | 23/25 same OK-vs-flag decision, 20/25 same verdict |
| Cost of one campaign run (brief → approval) | $0.0945 (`demo` brief) |

- Live sandbox (judge login; the password is in the submission): **https://muse.udun.eu**
- Numbers: **[docs/eval.md](docs/eval.md)** · how it works: [docs/architecture.md](docs/architecture.md) ·
  Brief v1 and the [FACT] questions: [docs/brief.md](docs/brief.md) ·
  threat model: [docs/SECURITY.md](docs/SECURITY.md) · MCP server: [docs/mcp.md](docs/mcp.md) · review policy and platform limits: [docs/policy.md](docs/policy.md) · video script: [docs/demo-script.md](docs/demo-script.md)

## For judges

- **Pitch:** an AI ad agency for a one-person business that creates the Meta campaign PAUSED, and only after the owner signed the exact bytes; every step is a receipt you can check offline.
- **Theme:** Build for Your World, Indie track: small-business owners running their own Meta ads safely.
- **Try it:** https://muse.udun.eu, login `judge`; the password is in the submission form, not in this repo.
- **No login:** a recorded run replayed and verifiable in your browser at https://muse.udun.eu/try
- **How it works in 1 minute:** https://muse.udun.eu/start
- **From your own assistant:** with your own account (tokens are off for `judge`), make a token on `/account#assistants`, then `claude mcp add --transport http muse https://muse.udun.eu/mcp --header "Authorization: Bearer …"` (or the HTTPS API, `/api/v1/openapi.json`); it drafts and runs, a human still signs every send ([docs/assistants.md](docs/assistants.md)).
- **Sign up in one step:** "Continue with Facebook" or "Continue with Google" creates a trial account (campaigns stay PAUSED on the studio's own page until the operator lifts the trial); Stripe Checkout runs in test mode on the sandbox.
- **A short check, not a long form:** after "Draft my brief" the owner sees a six-sentence summary of what the studio understood, and an assistant asks only what is missing or unconfirmed, one question at a time with one-click answers (countries, page, budget); the full Brief v1 form is folded under "All details".
- **Business profile:** website, the countries you sell in and your pixel (picked from the ad account's list) are saved once in Account → Profile; every draft starts from them, marked "from your profile".
- **For your accountant:** a monthly statement ties every ad expense to the signature that allowed it (approval id, receipt hash, Meta spend, the studio's model costs) — CSV for Xero, QuickBooks or wFirma, an hledger journal, or JSON; leads export as CSV, JSON or vCard and import from CSV/JSON/vCard; the same data over the API (`/api/v1/statement`, `/api/v1/leads`) ([docs/export.md](docs/export.md)).
- **Your own clip:** a short vertical phone video (from the brief page or sent on WhatsApp) becomes the ad's 9:16 video; it is probed, cut to 15 s and signed by hash like every image.
- **WhatsApp:** notices (approval waiting → link to sign in the browser, new leads, ad disapproved) and voice-note briefs that become a Brief v1 draft; links only, nothing signed or run from WhatsApp ([docs/whatsapp.md](docs/whatsapp.md)).
- **3-minute video:** **https://youtu.be/_UEMnIl29T8**
- **Built during the event:** first commit 2026-10-27; solo.

| Meta model | What it does | File |
|---|---|---|
| `muse-spark-1.3` | plan and tools, image review (vision), comment triage, lead rationale, report | `studio/agents/producer.py`, `reviewer.py`, `community.py`, `lead_scorer.py`, `analyst.py` |
| `muse-spark-1.3-contributor` | copy and image prompts when the input has no personal data | `studio/agents/copywriter.py`, `studio/pii.py` |
| `muse-image-1.0` | the three placements and carousel cards | `studio/muse/image.py` |
| `sam-3.1` | product cut-out | `studio/muse/sam.py` |
| `muse-voice-transcribe-1.0` | spoken brief and dictation | `studio/muse/voice.py` |
| `muse-wonder-1.0` | video shot list | `studio/agents/video.py` |
| `meta-models/muse-spark-open` (Baseten) | second-opinion reviewer | `studio/muse/openweights.py` |

Read first: `studio/approvals.py` (signature bound to bytes), `studio/receipts.py` (hash chain), `studio/executor.py` (code creates the campaign, PAUSED), `studio/agents/producer.py` (the agent loop), `studio/agents/reviewer.py` (reviewers and policy checks).

Verify receipts in one command (export from the footer link after signing in): `studio verify receipts.jsonl --server https://muse.udun.eu`

## Before / after, for a one-person business

**Before.** I run ads for my own small firm. A campaign takes an evening: write three
variants, make images for feed and stories, check them against ad policies, click through
Ads Manager. A comment that arrives at 21:03 ("how much is it?") waits until the morning. And
I would never hand an AI my ad account — one bad post or one wrong budget is my money and my
name.

**After.** I type (or say, in Polish) one brief. The studio returns a plan, three copy
variants, a product cut-out and three matching placements, two compliance reviews of every
image — and one approval screen with the content hash. I sign; code creates the campaign,
ad set, creative and ad, all **PAUSED**, and shows the ad preview. A comment gets a draft
reply in seconds (prompt injections included, they fail); I edit one word and sign; the
commenter becomes a scored lead. At the end of the day the analyst reads the whole ledger in
one prompt and tells me what changed. When Google Ads disapproved two human-written ads of
mine ("Załóż spółkę LTD … przenieś firmę do UK", "Niskie podatki, brak obowiązku ZUS"), it was
after publishing; the reviewer now catches the same issue before anything goes out (policy
rule P9).

## The demo loop

brief (typed, or spoken and edited) → **Producer** drafts a Brief v1 → the owner answers
or strikes every [FACT] question and confirms (a `brief.confirmed` receipt) → **Producer** plan → **Copywriter** three variants →
**Art director** SAM 3.1 cut-out + three Muse Image placements → **Reviewers** (Spark vision
+ Spark Open second opinion + P9, P10 and SAM S1/S2 code checks) → **human signature** → **Media buyer** (code)
creates the campaign PAUSED → **Community** drafts a reply to a comment → signature → reply
→ **Lead scorer** card → **Analyst** report → `studio verify` on the exported log.

## Run it in 3 commands

```bash
make install       # Python 3.12+, uv
make demo-replay   # the whole loop offline from a recorded run, then studio verify (10–15 s)
make demo          # the UI at http://localhost:8000/studio (MODEL_API_KEY in .env for live calls)
```

`make serve-replay` serves the UI on recorded answers (the `brief` set by default: the Brief v1
flow end to end; no key, no network; the header says **replay**). `make demo-replay SET=brief`
replays that flow headless. `make test` runs the suite offline, `make eval` regenerates
[docs/eval.md](docs/eval.md).

## Meta models used (exact ids from the code)

| Model id | Where | How |
|---|---|---|
| `muse-spark-1.3` | Producer, compliance reviewer (vision), community triage, lead rationale, analyst | Responses API with function tools, `parallel_tool_calls`, `reasoning.summary`, `previous_response_id`; strict JSON schemas; images in user messages; the analyst reads the whole ledger (long context) plus Meta's insights API (no screenshots) |
| `muse-spark-1.3-contributor` | Copywriter and image-prompt writer | chosen by code only when the input has no personal data (`pii.choose_tier`); the reason is in every receipt |
| `muse-image-1.0` | Art director | first placement as a Responses turn with the cut-out, the others (and up to 10 carousel cards) via `/images/edits` with the cut-out and the first image, so the series matches |
| `sam-3.1` | Art director | product cut-out from the photo, then a fidelity check of every generated image (product + `person`: silhouette IoU, area, edges → rules S1/S2): streamed transport, batch parse (`meta_sam_parser`) |
| `muse-voice-transcribe-1.0` | Voice brief, dictation into any Brief v1 field and [FACT] answers | `POST /asr/transcribe`, `ENDPOINTING`, `languageBias` from the page language (`pl` or `en`), turns with timestamps |
| `meta-models/muse-spark-open` | Second-opinion reviewer (open weights, on Baseten) | same `Review` schema, different weights; BLOCK from either side wins; disagreements shown before signing |
| `muse-wonder-1.0` | Art director, optional video placement | strict-JSON storyboard: which reviewed image comes when and for how long; code cuts the 9:16 MP4 with ffmpeg (Wonder reads images and writes text, it does not render video); code orders the shots if the call fails |

## Meta advertising capabilities

| Capability | What the studio does | Who decides |
|---|---|---|
| Objectives | All six ODAX objectives with Meta's sub-choices (reach, ad recall, link clicks, landing page views, post engagement, messages on Messenger/Instagram/WhatsApp, event responses, instant form, website leads, app installs, website sales, catalog sales), mapped by one code table to objective, optimisation goal, billing, destination and promoted object ([docs/brief.md](docs/brief.md#objectives-code-table-studiometaobjectivespy)) | the owner picks; code maps; the mapping is signed (`spec_sha256`) |
| Pixel check | website leads and sales: the owner's pixel checked read-only before signing (exists, usable by the ad account, events in the last 7 days, id in the landing page fetched SSRF-safe), shown as a badge and per-check lines ([docs/brief.md](docs/brief.md#pixel-check-before-signing)) | a failed check cannot be signed or executed; warnings are shown, the owner decides |
| Conversions API (off by default) | `STUDIO_CAPI=1`: each new instant-form lead sent to the owner's dataset as a `Lead` event (`system_generated`, `event_id` for dedup, optional `test_event_code`), e-mail and phone as SHA-256 only ([docs/brief.md](docs/brief.md#conversions-api-optional-off-by-default)) | only for an own account that ticked customer consent and saved a dataset id; one receipt per lead with digests only; mock/replay never call Meta |
| Lead ads | instant form with the owner's questions and privacy page, created through the API; leads polled or pushed by a signature-checked webhook into the lead store and scorer | the owner signs the form with the campaign |
| Audiences | Advantage+ audience by default, hints resolved to Meta ids by the targeting search and shown before signing, exclusions, the owner's custom audiences, lookalikes from the owner's source audience; no user data uploaded | the owner |
| Formats | feed and stories images, carousel (2–10 cards), 9:16 video, copy A/B (dynamic creative where supported, else separate ads) | both reviewers check every creative and variant, then the owner signs |
| Measurement | results and cost per result for the objective, reach, frequency, CPM, spend, CTR, link clicks, by age and gender, placement and region, with the attribution setting; hourly `insights.snapshot` receipts; code-rule recommendations | a budget change or pause runs only after a new signature |
| Creative angles | three copy variants written from three different reasons to buy (not personas), checked by code for word overlap before signing (`creative_diversity` in the signed payload), so Advantage+ has real choices | code measures; the owner sees it before signing |
| Reach estimate | Meta's own `delivery_estimate` for the draft's audience and goal, interpolated at the owner's daily budget next to the budget advice | Meta estimates; the owner sets the budget |
| Brand scan | before drafting, the owner's site (named in the brief) is read: up to 4 same-site pages, offers, prices, tone and linked social profiles, inside the untrusted-content envelope as suggestions the owner confirms | the owner confirms every fact |
| Currency | budgets in the ad account's currency (read from the account), minor units by Meta's offsets | the ad account |

Development used **Muse Code** for the Muse-specific modules and prompt tuning; see
[docs/build-log/](docs/build-log/) for what is and is not logged there.

## What is different here: a signature bound to bytes, and a log anyone can check

Human approval buttons are common. Here the approval is cryptographic and checkable by a
third party:

- The browser holds a **non-extractable WebCrypto Ed25519 key**. "Approve" signs
  `{action, item_id, content_sha256, chain_head, approver, nonce, ts}` — the exact payload
  hash *and* the provenance chain that produced it — plus, for a campaign made from a
  confirmed [Brief v1](docs/brief.md), its `brief_sha256`: the owner signs the facts the
  ad stands on, not only the ad. The server consumes the one-time nonce in
  the same transaction as the approval receipt; a replay, an edit after signing or a stale
  approval is refused.
- No model can write outside the studio: external skills are not in any tool list; the media
  buyer is code that runs only on a consumed approval, creates every object PAUSED, and
  activation is a second signature under a daily budget ceiling.
- Every model call, tool call, review, approval and write is a **receipt** in a hash chain
  per run, signed by the server's ledger key and closed by a signed manifest.
  `studio verify` re-checks it offline and exits 2 naming the receipt when anything is off:
  in [docs/eval.md](docs/eval.md) it rejects **600/600 tampered exports across 24 tamper
  classes** (edited, re-hashed, deleted, reordered, truncated, rolled back, forged
  signatures, writes the server logged without an approval, a Brief v1 fact edited
  after signing, a campaign created with another objective or promoted object than the one
  signed, a Google Search campaign created with another spec than the one signed, and a
  recommendation executed without approval) and accepts every clean one.
- The reviewer stops **18 of 20 seeded bad ads** (all **5/5 P9** ads blocked by the model
  alone), with **0 of 5** clean ads blocked. The open-weights second opinion (Muse Spark
  Open on Baseten) makes the **same OK-vs-flag decision on 23/25** ads (same verdict on
  20/25; every disagreement is shown before signing). One campaign run (brief → pending
  approval, both reviewers on every placement) costs **$0.08–0.13**.

### Verify a receipt log

```bash
# receipts.jsonl: the footer link after signing in at https://muse.udun.eu/login
studio verify receipts.jsonl --server https://muse.udun.eu   # key, manifest, approver keys are public
# offline: studio verify receipts.jsonl --key-file ledger.pub \
#          --approver-keys approvers.json --min-seq <seq from /api/ledger-manifest>
```

Exit codes: 0 chain OK, 2 chain INVALID (each problem names the receipt or run), 3 the
check could not run. `--server` fetches the published ledger key, the latest manifest `seq`
(so an older, rolled-back export fails) and the approver public keys (so every approval
signature is checked). The receipts page (`/receipts`) filters by run, shows full hashes as
tooltips and copies any receipt as JSON.

`--server` is **trust on first use (TOFU)**: it trusts whatever key and `seq` the server
serves at that moment, so a compromised server could serve a matching forged key. For a
real check, pin the ledger key once, out of band (`ledger.pub`, published with the
release or copied from the operator), pass it with `--key-file ledger.pub` together with
`--server` (a mismatch is refused), and keep the highest `seq` you have seen to pass as
`--min-seq` next time, so a rollback to an older log cannot go unnoticed.

## Drive it from another agent (MCP)

`studio mcp` is a stdio MCP server for an external agent such as Claude Code: it reads
briefs, runs, campaigns, receipts and the report, drafts a Brief v1 and runs the pipeline
from recorded answers, and runs `studio verify`. It cannot sign, send, change a budget or
touch an account; a run ends with `awaiting_human_signature` and the link to the approval
page, and every call is a receipt with the agent `mcp:<client name>`.

```bash
claude mcp add muse-studio -- /path/to/.venv/bin/studio mcp --account owner \
    --env-file /path/to/.env --ui-url http://localhost:8000
```

Tools, receipts and limits: [docs/mcp.md](docs/mcp.md).

## Honest status

- Meta writes go through `META_ADAPTER=graph` (Marketing API v25.0, own ad account,
  PAUSED-only) or the mock adapter. **The sandbox runs the graph adapter**
  (`STUDIO_SANDBOX_LIVE_META=1`): a judge's signature creates a real campaign, ad set,
  creative and ad in the studio's own ad account, all PAUSED, with every link on the
  studio's landing host; activation and any other live write are refused for the `judge`
  account, and an hourly reset clears the demo data (receipts are kept). On 2026-10-29 a live run on the Muse Studio Lite ad account (after the app went
  Live) created a real campaign (52566255666766), ad set (52566255672966) and ad
  (52566255715566), all PAUSED, daily budget 6 GBP, with the EU DSA beneficiary and payor
  fields sent; the ad preview needed `business.facebook.com`, which is now accepted. The
  graph adapter's other write paths are tested against hand-written cassettes; its reads
  (account currency, targeting search, insights fields, lead forms) were checked live,
  read-only, and recorded (`fixtures/cassettes/graph_readonly.json`).
- Google Search is a minimal second channel (`GOOGLE_ADS_ADAPTER=rest`, Google Ads REST API
  v23, no client library): tick it in the brief and a second approval signs one Search
  campaign (its spec hashed into the signature, created paused after a dry run, activated
  by a third signature). Tested against the mock and recorded REST shapes; the live call
  is run by the operator ([docs/SANDBOX.md](docs/SANDBOX.md)).
- Every fixture says what it is: `"_provenance": "recorded"` (a real API response) or
  `"synthetic"` (hand-written, `*.SYNTHETIC.json`, with a `_note`). The demo brand, comments
  and leads are synthetic; the demo product photo is model-generated (set
  `STUDIO_PRODUCT_PHOTO` and `STUDIO_PRODUCT_CONCEPT` to use your own).
- The second-opinion reviewer needs `BASETEN_API_KEY` (the recorded runs include it); without
  it the screen says "unavailable".
- Accounts: judges share the `judge` demo account (two-step sign-in and deletion are off
  on it). Anyone can sign up with Facebook or Google: a new account starts as a **trial**
  (it sees no one's personal data, its campaigns stay PAUSED) until the operator promotes
  it (`studio account promote`); operator-made accounts (`studio account create`) start
  full. Optional TOTP two-step sign-in, sessions, export and deletion at `/account`.
  Billing: Stripe Checkout and the customer portal, a signature-checked webhook; the
  sandbox uses Stripe test mode only, and a test-mode payment never lifts a trial.
- The studio is single-tenant today: one operator's ad account, page and pixel; leads and
  comments are masked for trial and demo accounts. Per-client tenancy is the next step.
- Day-by-day changes: [CHANGELOG.md](CHANGELOG.md).

## Screens and the cast

Light and dark, from `make serve-replay` (recorded answers, no network). The studio after a
signed run — run HUD, receipt tape, cost and token counters:

<img src="docs/screenshots/studio-light.png" alt="Studio page after a signed run, light theme" width="49%"> <img src="docs/screenshots/studio-dark.png" alt="Studio page after a signed run, dark theme" width="49%">

The illustrations show the people themselves: friendly monoline owners of one-person
businesses standing side by side, a dotted trace whose diamonds are the agents and whose
last node is the owner's signature, and the ad on the right, complete only after it. Ten
owners (`studio/ui/cast.py`, gallery at `/style/cast`) answer the questions on
[`/help`](https://muse.udun.eu/help) (also in Polish at `/pl/help`):

<img src="docs/screenshots/help-light.png" alt="Help page with four owners side by side, light theme" width="49%"> <img src="docs/screenshots/help-mobile-dark.png" alt="Help page on a phone, dark theme" width="24%">

More: [index](docs/screenshots/index-light.png) · [inbox](docs/screenshots/inbox-dark.png) ·
[leads](docs/screenshots/leads-light.png) · [report](docs/screenshots/report-dark.png) ·
[receipts](docs/screenshots/receipts-light.png) · [cast](docs/screenshots/cast-light.png) · [404](docs/screenshots/404-dark.png)
(each also as `-light`/`-dark` in [docs/screenshots/](docs/screenshots/)). The header switches
EN/PL, light/dark and four colour palettes (graphite, warm paper, green, high contrast):
[docs/screenshots/palettes/](docs/screenshots/palettes/).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
