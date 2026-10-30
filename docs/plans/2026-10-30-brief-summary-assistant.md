# Plan B — "Check the draft" as a short summary and an assistant, not a long form

**Goal.** After "Draft my brief", an owner sees a short card that reads like their own
brief, and the studio asks them, one at a time, only what is missing or unconfirmed —
with one-click answers where possible. The full Brief v1 form stays, folded under
"All details". Today step 2 is one very long form (≈15 fieldsets), and a missing required
field (e.g. the countries) only shows up as an error after "Confirm".

**Why now.** Hackathon deadline 2026-11-04; this is the first screen judges and new owners
use. Plain JS + server-rendered HTML, as the rest of the studio.

## Where things are
- Template: `studio/ui/templates/studio.html` — the brief panel; step 2 starts at
  `<h2 id="bv-h">` (≈ line 91): error summary `#bv-errors`, open questions
  `#bv-questions`, facts, advertiser, offer, objective, promotion `#bv-promotion`,
  audience, tone, mandatories, budget, placements, channels, assets; confirm buttons.
- Logic: `studio/ui/static/brief.js` — `fill(b)` puts a draft into the form, `collect()`
  reads it back, `showErrors(fields)`, `renderQuestions(list)` (open [FACT] questions with
  answer / strike), `showDraft(data)`, `setStep(n)`, the confirm POST to `/api/runs`.
- The draft answer (`POST /api/briefs/draft`, `studio/routes_studio.py`) already carries
  `brief`, `problems` (`{path: code}` for the unconfirmed draft, from
  `studio/brief.py::problems`), `budget_advice`, `site`, `currency`, `ceiling`. A parallel
  change may add `from_profile: [paths]` (fields filled from the owner's saved profile) —
  handle it if present, ignore if absent.
- Texts: `studio/ui/i18n.py` (EN + PL; parity tests in `tests/test_i18n.py`), JS strings
  through the `js.` keys the page already uses (`T("…")`).

## Design
1. **Summary card** (new, at the top of step 2, `id="bv-summary"`): 5–7 sentences built
   from the draft, each with a "Change" button that opens and focuses the matching form
   field (open the `<details>` first). Sentences (EN/PL templates with placeholders):
   - who: `advertiser.display_name` + kind;
   - what: `offer.product` — `offer.offer` (+ "until {valid_until}" if set);
   - to whom / where: audience age range, `audience.locations` (country names via
     `Intl.DisplayNames`), languages;
   - goal: objective in plain words (reuse the objective labels the form uses);
   - budget: amount + currency per day/lifetime, with the budget advice line if present;
   - where it leads: `advertiser.landing_url` (host only);
   - "from your profile" badge on sentences whose paths are in `from_profile`.
   The card re-renders on every form `input`/`change` (debounced 150 ms), so edits in the
   details stay in sync.
2. **Assistant questions** (`id="bv-assistant"`, replaces where the owner looks first; the
   existing open-question list `#bv-questions` is reused, not duplicated): one card at a
   time, "Question 2 of 4", Back / Skip / Next, focus moves to the card's heading:
   - every open [FACT] question (as today: answer in own words, or "don't use it");
   - every **required** problem from `problems` turned into a question with quick
     choices, at minimum:
     - `advertiser.countries` → chips "Poland", "United Kingdom", "Germany", "Whole EU"
       (+ free text "other: …" accepting ISO codes or names mapped to ISO-2 for the EU
       27 + UK, NO, CH, US, CA, AU); also sets `audience.locations` if empty;
     - `advertiser.landing_url` → URL input with https check;
     - `budget.amount` → chips from `budget_advice` (if present) + number input;
     - `offer.product` / `advertiser.display_name` → text input;
     - any other required path → a text input labelled with that field's form label
       (generic fallback, so nothing is ever missing).
   - Answers write into the form fields (single source of truth stays the form +
     `collect()`); the error state of that field is cleared.
   - When the queue is empty: "All set — check the summary and confirm" and the confirm
     button gets focus.
3. **Full form** folded: wrap the existing fieldsets (facts … assets) in
   `<details id="bv-details"><summary>All details (for checking every field)</summary>`.
   `showErrors` must open it when it shows an error inside, and focus as today.
   Nothing in the form is removed; ids stay (other tests and the recorder rely on them:
   `#brief-v1-form`, `li.bv-question`, `input[type=radio][value=strike]`,
   `#confirm-button`, `#confirm-status`, `[name="advertiser.countries"]`).
4. **No JavaScript**: the page already needs JS for the flow; keep the form usable if
   the summary/assistant code throws (wrap init in try/catch → details open).
5. Styling in `studio/ui/static/components.css` with existing tokens; summary readable at
   phone width; respects `prefers-reduced-motion` (no animation needed).

## Tests
- Python/template tests (see `tests/test_ui_studio.py`, `tests/test_onboarding.py` for
  patterns): the studio page contains `#bv-summary`, `#bv-assistant`, `#bv-details`, the
  new i18n keys in both languages; i18n parity passes.
- A JS behaviour check is welcome but optional: if you add one, use the repo's existing
  approach (if any) — do not add a Node test framework to the repo.
- Manual check you must do: run the app locally in replay mode exactly like the existing
  dev command in the docs (`STUDIO_REPLAY=1 STUDIO_REPLAY_SET=brief META_ADAPTER=mock`, a
  throwaway `STUDIO_DATA_DIR`, `COOKIE_SECURE=0`, `JUDGE_PASSWORD=…`, empty `STRIPE_*`)
  and drive it with playwright-core + `/usr/bin/chromium` (playwright-core is installed
  in `/tmp/claude-1000/-home-divi/061cf2c3-2e41-41b6-a949-e9af8ab02c97/scratchpad/uiaudit/tool`;
  write your script there, not in the repo): draft → summary visible → answer every
  assistant question → confirm starts the run. Take screenshots (EN and PL, 1280 and 390
  wide) and list their paths in your report.

## Constraints (all mandatory)
- Repo: `~/code/muse-studio-lite`. `.venv/bin/ruff check`, `.venv/bin/mypy studio`, full
  `.venv/bin/python -m pytest -q` (≈13 min) green before the final commit.
- Code/comments English; UI copy EN + PL, plain words for a one-person business.
- CSP: no inline scripts/styles; new JS in a new file `studio/ui/static/brief_summary.js`
  loaded with `defer` after `brief.js`, or inside `brief.js` if it must share its
  closures — prefer exposing a tiny API from `brief.js` (`window.BriefForm = {fill,
  collect, focusField, …}`) over duplicating logic.
- Accessibility first (the studio claims keyboard + screen-reader support): live region
  announcements via the existing `say()`, real buttons, visible focus.
- Commits: repo identity is configured (Piotr Kaczmarski, signed). No `Co-Authored-By`
  or "Generated with" lines. Never push, never deploy, never touch `.env`/secrets, never
  call live model/Meta APIs (replay mode only).
- Do not edit `studio/accounts.py`, `studio/routes_account.py`,
  `studio/ui/templates/account.html`, `studio/meta/*`, or the draft route in
  `studio/routes_studio.py` (another agent works there in parallel).
- Report: files changed, test counts, screenshot paths, anything left open.
