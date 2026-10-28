# Brief v1

A campaign starts from the owner's own words, typed or spoken. The producer turns them into a
structured **Brief v1** (`brief/v1`), the owner checks it, answers or strikes every open
`[FACT]` question and confirms it. Only then does the run start. The copywriter may use only
the confirmed facts, both reviewers check every claim against them, and the owner's
signature on the campaign covers the brief's hash as well as the ad.

Code: `studio/brief.py` (schema, hash, field checks, grounding), `studio/agents/producer.py`
(`draft_brief`, the run), `studio/routes_studio.py` (`POST /api/briefs/draft`,
`POST /api/runs`), `studio/ui/static/brief.js` (the form), prompts in
`studio/agents/prompts/producer_brief.md` and `_grounding.md`.

## The four steps

| Step | Who | What happens |
|---|---|---|
| 1. Tell | owner | Free text, or a recording transcribed by `muse-voice-transcribe-1.0` (`languageBias` = the page language, `pl` or `en`). The transcript lands in the same text box and can be edited before the producer reads it. |
| 2. Draft | producer (`muse-spark-1.3`) | One strict-JSON call with the `BriefV1` schema. The prompt holds the policy checklist and the drafting role, not the brand sheet: a draft carries only what the owner wrote. Code then runs `ground_draft`: a fact, product, offer, validity date or proposition holding a number that is not in the owner's words becomes an open `[FACT]` question. |
| 3. Answer | owner | Every open question is answered in a short sentence (it becomes a confirmed fact, in the owner's words) or the claim is struck (it is dropped). Every field of the draft can be edited, by typing or by dictation. |
| 4. Confirm | owner, then code | The server checks every field (`brief.problems`), refuses a brief with an unresolved question, hashes it and writes the `brief.confirmed` receipt in the same transaction that starts the run. |

A free-text brief (`POST /api/runs {"brief": "..."}`) still runs as before, without a
`brief.confirmed` receipt; the recorded `demo`, `office` and `pushy` sets replay that path.

## Sections

| Section | Fields | Why |
|---|---|---|
| `advertiser` | `kind` (`person` or `company`), `display_name`, `countries` (ISO 3166-1 alpha-2), `landing_url` (https) | A display name that does not match the page or legal name gets ads rejected. Global: no national registry or tax ids. |
| `confirmed_facts` | short statements | The only facts an ad may state: prices, times, guarantees, numbers. |
| `offer` | `product`, `offer`, `valid_until` (`YYYY-MM-DD` or empty), `reasons_to_believe[{text, facts}]` | Each reason names the indices of the confirmed facts it rests on; a reason without a fact is refused. |
| `objective` | `goal` (exactly one of `awareness`, `traffic`, `engagement`, `leads`, `app_promotion`, `sales`), `kpi`, `sub` (Meta's sub-choice, `""` = the goal's default) | Meta's six campaign objectives. Code maps goal and sub-choice to Meta's fields (table below); the mapping is the payload's signed `delivery` block. |
| `promotion` | `pixel_id`, `custom_event_type`, `application_id`, `object_store_url`, `event_id`, `product_catalog_id`, `product_set_id`, `privacy_policy_url`, `lead_fields` | The ids the chosen objective needs, as the owner gives them. A missing one is the field error `required_for_objective`; an id or URL the draft holds but the owner never wrote is cleared (`ground_ids`). |
| `audience` | `locations`, `age_min`, `age_max` (13–65), `languages`, `insight{who, belief, barrier, trigger}`, `advantage_audience` (default true), `genders`, `interests`, `excluded_interests`, `excluded_countries`, `custom_audience_ids`, `excluded_custom_audience_ids`, `lookalike_source_id`, `lookalike_percent` | With Advantage+ audience the age range, genders and interests are suggestions and locations, the minimum age (18–25), languages and excluded audiences are controls. Interests and languages are resolved to Meta ids by code through the targeting search before the approval; audience ids come only from the owner (never guessed, never uploaded data). |
| `proposition` | one sentence | The single thing the ad says. |
| `tone_brand` | `tone`, `colours`, `donts` | Brand guidance for copy and images. |
| `mandatories` | `disclaimers`, `banned_claims`, `dates` | Banned claims are blocked by code and by both reviewers (P10). |
| `budget` | `kind` (`daily` or `lifetime`), `amount`, `currency`, `start`, `end` | The currency must be the ad account's (read from the account; the field error names it, nothing is converted). A daily amount above `STUDIO_MAX_DAILY_BUDGET` (in that currency) is refused at confirmation; a lifetime amount is spread over its days, rounded up. Code, not the model, puts the owner's daily budget into the plan, in the currency's minor units. |
| `placements` | `automatic` (default `true`), `deliverables` (`feed_1x1`, `feed_4x5`, `story_9x16`), `carousel_cards` (0, or 2–10), `video` (a 9:16 video ad), `copy_variants` (1–3) | The deliverables replace the plan's placements; carousel cards, the video poster and every A/B variant are reviewed like the other creatives. Event and catalog ads take one image and one variant. |
| `channels` | `google_search` (default `false`), `google_daily_budget` (whole units of the Google account's currency) | Meta is always the primary channel. Ticking Google Search adds a second approval (`google.create`) for one Search campaign made from this same brief; its budget is separate and under the same ceiling, and the brief's currency must be the Google account's (`google_account_currency`, never converted); every country needs a Google geo target (`google_geo`). |
| `assets` | `[{kind, note}]` (`product_photo`, `logo`, `other`) | The product photo for the SAM 3.1 cut-out is configured on the server (`STUDIO_PRODUCT_PHOTO`); notes record what else the owner has. |
| `open_questions` | `[{question, claim}]` | The gaps. Must be empty when the brief is confirmed. |

Fields added after the first version have defaults, so briefs and recordings made before
them still validate.

## Objectives (code table, `studio/meta/objectives.py`)

| Goal | Sub-choice | Meta objective | Optimisation goal | Destination | Promoted object | The owner gives |
|---|---|---|---|---|---|---|
| awareness | reach / ad_recall_lift | `OUTCOME_AWARENESS` | `REACH` / `AD_RECALL_LIFT` | — | `page_id` | — |
| traffic | link_clicks / landing_page_views | `OUTCOME_TRAFFIC` | `LINK_CLICKS` / `LANDING_PAGE_VIEWS` | `WEBSITE` | — | — |
| engagement | post_engagement | `OUTCOME_ENGAGEMENT` | `POST_ENGAGEMENT` | `ON_POST` | — | — |
| engagement | messenger / instagram_direct / whatsapp | `OUTCOME_ENGAGEMENT` | `CONVERSATIONS` | `MESSENGER` / `INSTAGRAM_DIRECT` / `WHATSAPP` | `page_id` | — (WhatsApp: a number on the Page) |
| engagement | event_responses | `OUTCOME_ENGAGEMENT` | `EVENT_RESPONSES` | `ON_EVENT` | — | `event_id` |
| leads | instant_form | `OUTCOME_LEADS` | `LEAD_GENERATION` | `ON_AD` | `page_id` | `privacy_policy_url`, questions |
| leads | website | `OUTCOME_LEADS` | `OFFSITE_CONVERSIONS` | `WEBSITE` | `pixel_id`, `custom_event_type` (LEAD) | `pixel_id` |
| app_promotion | app_installs | `OUTCOME_APP_PROMOTION` | `APP_INSTALLS` | — | `application_id`, `object_store_url` | both |
| sales | website | `OUTCOME_SALES` | `OFFSITE_CONVERSIONS` | `WEBSITE` | `pixel_id`, `custom_event_type` (PURCHASE) | `pixel_id` |
| sales | catalog | `OUTCOME_SALES` | `OFFSITE_CONVERSIONS` | `WEBSITE` | ad set: `pixel_id`, `custom_event_type`, `product_set_id`; campaign: `product_catalog_id` | all three |

Billing is `IMPRESSIONS` for every row; conversion rows (website leads, app, sales) set
`attribution_spec` to 7-day click, 1-day view. Sources: the ODAX mapping table of the
campaign reference and the lead ads guide (cited in the module).

The Pydantic models are strict (`extra="forbid"`, no type coercion) and are also the
producer's strict JSON schema, so length limits live in `brief.problems` (field path →
error code; the UI shows each code's message, in English or Polish, next to its field).

## The [FACT] mechanic

The copywriter writes `[FACT]` instead of inventing a fact, and an unfilled placeholder can
never be signed. Brief v1 asks the owner first, before any copy exists:

1. the producer is told to turn every vague or missing claim into an open question, never
   into a fact (`producer_brief.md`);
2. code demotes any drafted fact with a number the owner never wrote (`ground_draft`);
3. the owner answers or strikes each question; the run does not start while one is open;
4. the copywriter gets the confirmed facts with the rule that they replace the brand sheet's
   facts and that anything else is `[FACT]`;
5. the reviewers get the facts and rule **P10** (`docs/policy.md`); code adds the same check
   for numbers and banned claims (`reviewer.grounding_net`), and `request_approval` refuses a
   variant that fails it.

## Receipt and signature

On confirmation the ledger gets:

```json
{
  "kind": "brief.confirmed",
  "agent": "owner",
  "output_sha256": "<brief_sha256>",
  "details": {
    "schema": "brief/v1",
    "brief_sha256": "<sha256 of canonical JSON>",
    "facts": 8,
    "open_questions": 0,
    "answered": 1,
    "struck": 2,
    "voice": "muse-voice-transcribe-1.0"
  }
}
```

`brief_sha256` is SHA-256 over the canonical JSON (sorted keys, no whitespace, UTF-8) of
`{"schema": "brief/v1", ...the confirmed brief}`; the brief itself is stored in the database
(`briefs.brief_v1`), never in a receipt. `voice` names the transcription model when any part
of the brief was spoken, and such a brief runs as a transcript (enveloped, Standard tier).

The `campaign.create` payload carries `brief_sha256` and its objective mapping (`delivery`),
and the approval message the browser signs gains two fields (`spec_sha256` is the hash of
the `delivery` block):

```json
{"type": "approval", "action": "campaign.create", "item_id": "...", "content_sha256": "...",
 "chain_head": "...", "approver": "...", "nonce": "...", "ts": 0, "brief_sha256": "...",
 "spec_sha256": "..."}
```

The server refuses to issue such an approval for a brief that is not the run's latest
confirmed brief, refuses to consume it if the run's brief changed since (`brief_changed`),
and the media buyer checks it once more before any Meta call. `studio verify` checks the
same binding offline:

- a `brief.confirmed` receipt must name schema `brief/v1`, carry its hash as its output hash
  and have `open_questions: 0`;
- an approval that signs a `brief_sha256` must sign the run's current confirmed brief;
- a `campaign.create` approval in a run with a confirmed brief must sign it;
- a write or adapter step whose approval signed a brief fails when the run's brief changed
  after the approval.

The tamper suite in [eval.md](eval.md) covers each rule.

## Accessibility of input

A person with a disability must be able to make an ad:

- **Two equal paths**: typing, or speaking (up to 10 minutes per recording, record again to
  add more). The transcript is editable text before the producer reads it.
- **Voice everywhere**: every text field of the draft and every answer to a `[FACT]`
  question has a **Dictate** button (`POST /api/briefs/voice?purpose=field`, no brief row).
- **Keyboard and screen reader**: labelled controls, a semantic form with `fieldset` and
  `legend`, each error tied to its field with `aria-describedby` and `aria-invalid`, an error
  summary that links to each field, and an `aria-live` region that announces each step, the
  new `[FACT]` questions and the result of every action. Focus is visible on every control,
  including the chamfered buttons.
- **No time limits**: no step expires; if the 15-minute signing window of an approval closes,
  `POST /api/approvals/{id}/renew` issues a fresh one for the same content (the old one can no
  longer be consumed).
- **Large hit targets** (at least 44 px) and **reduced motion** respected.
- **Read-back**: the draft can be read aloud with the browser's built-in `speechSynthesis`
  (no network, no new service); the button is hidden where the browser cannot speak.
- **No microphone**: a denied permission or a missing microphone gives a clear message and
  the typing path keeps working.

## Replay

`fixtures/replay/brief/` is a recorded end-to-end run of this flow (Polish owner's words,
draft, one answered question, the rest struck, confirmation, run, signature, campaign
PAUSED). `make demo-replay SET=brief` replays it headless and runs `studio verify`;
`make serve-replay` serves the UI on it (the default set for the UI). In replay, a draft request whose words differ
from the recording falls back to the recorded draft (route fallback prefers the same strict
output schema), so the form can be explored offline.

`fixtures/replay/muse/` is the second example: this studio advertised as an open-source
tool for one-person businesses (traffic to the sandbox, 6 GBP a day for 7 days, Poland, no
company named), recorded on an ad account that books in GBP (`account_currency` in the
set). `make demo-replay SET=muse` replays it; `make serve-replay SET=muse` serves the UI on
it. In the studio both examples are in the "Example brief" list; the ceramics one is the
default.
