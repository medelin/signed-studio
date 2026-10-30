# Plan A — Business profile: website, countries and pixel saved once

**Goal.** An owner states their website, the countries they sell in and their Meta pixel
once, in Account → Profile; every brief draft starts from them, and the brand scan reads
the saved website when the brief names none. Today all three are typed into every brief,
and a draft that misses the countries fails only at "Confirm" (seen in a live run).

**Why now.** Hackathon deadline 2026-11-04. Small, contained, testable without Meta writes.

## Scope

1. **Storage** (`studio/accounts.py`): three columns on `accounts`, added by the module's
   existing migration pattern (look at how `tier` was added: `ALTER TABLE … ADD COLUMN`
   guarded by a column check), all `TEXT NOT NULL DEFAULT ''`:
   - `website` — an https URL (same rule as `brief._https`), max 300 chars;
   - `sell_countries` — ISO 3166-1 alpha-2 codes, comma-separated, upper case, ≤ 10
     (reuse the `_ISO2` pattern from `studio/brief.py`);
   - `pixel_id` — digits only, ≤ 20, or `''`.
   Expose them on the `Account` dataclass and accept them in the method the profile form
   already saves through (find it from `POST /account/profile` in
   `studio/routes_account.py`). Validation errors use the existing `error_of("profile",
   …)` / `invalid(...)` mechanism with new error codes (EN + PL texts).
2. **Profile form** (`studio/ui/templates/account.html`, section `#profile`): a new
   fieldset "Your business online" / „Twoja firma w sieci" after the advertiser fieldset:
   - `website` (`type="url"`, `inputmode="url"`, hint: "The studio reads this site before
     drafting, to suggest facts you confirm.");
   - `sell_countries` (text input, hint with the format "PL, DE");
   - `pixel_id`: a `<select>` of the ad account's pixels (name + id) with a first option
     "No pixel" and, when the list cannot be read, a plain text input instead (hint links
     Meta's "Create a pixel" help page; the studio does **not** create pixels).
3. **Pixel list** (`studio/meta/adapter.py`, `graph.py`, `mock.py`): a read-only
   `list_pixels() -> list[dict[str, str]]` (`id`, `name`) on the adapter interface.
   Graph: reuse `_account_pixels`' paging but with `fields=id,name`; refactor so both use
   one paged helper. Mock: two demo pixels. Errors → empty list (the form falls back to
   the text input). Cache per process for 5 minutes (a dict with a timestamp is enough).
   No receipt needed (read-only, no model), but never log the token.
4. **Draft prefill** (`studio/routes_studio.py`, `draft_brief`): after the model's draft
   and before `store_draft`, fill from the signed-in account's profile **only fields the
   draft left empty**:
   - `advertiser.landing_url` ← `website` (only if the draft's value fails `_https` or is
     the settings default `settings.landing_url`),
   - `advertiser.countries` ← `sell_countries`, and `audience.locations` the same when
     empty,
   - `promotion.pixel_id` ← `pixel_id` when the objective needs a pixel
     (`studio/meta/objectives.py` says which) and the draft has none.
   Put the logic in one pure function `profile_defaults(draft: BriefV1, account) ->
   tuple[BriefV1, list[str]]` in `studio/brief.py` (returns the paths it filled) and add
   `"from_profile": [paths]` to the JSON answer, so the UI can say "from your profile".
   The brand scan: when the body has no `website` and the text names no site
   (`brand_scan.first_site`), use the profile's `website`.
   The WhatsApp/assistant draft path (`studio/assistants.py`, `routes_whatsapp.py`) gets
   the same call if it builds drafts through a different function — check.
5. **Demo/judge account**: the profile of an `is_demo` account may be shown but saving
   stays as today (look at what the profile route does for demo accounts; keep it).

## Out of scope
Creating pixels, Conversions API, storing anything about the owner's customers.

## Tests (pytest, no network; mock adapter / replay)
- `tests/test_business_profile.py`:
  - saving valid website/countries/pixel round-trips; invalid URL (http://, no host),
    bad country code, non-digit pixel → the field's error code, nothing saved;
  - `profile_defaults` fills only empty fields and reports the paths; never overwrites a
    value the draft has; pixel only for pixel objectives;
  - `POST /api/briefs/draft` (replay/mock model as other draft tests do — see
    `tests/test_brief_flow.py`) returns `from_profile` and the filled fields;
  - mock `list_pixels` renders as `<option>`s; a failing list renders the text input.
- Update any account-page test that counts fields/sections; i18n parity tests must pass
  (every new key in EN and PL: `studio/ui/i18n_accounts.py` or where profile texts live).

## Constraints (all mandatory)
- Repo: `~/code/muse-studio-lite`. Python 3.13, `.venv/bin/python`. Run
  `.venv/bin/ruff check`, `.venv/bin/mypy studio`, and the **full** `pytest -q` (≈13 min)
  before the final commit; all green.
- Code, identifiers, comments: English. UI copy: EN and PL, plain words for a
  one-person business, technical terms stay English in PL where the UI already does so.
- Match the surrounding style: module docstrings explain the why; comment density as in
  neighbouring code. CSP: no inline scripts or styles.
- Accessibility: labels, hints via `aria-describedby`, keyboard only works.
- Commits: author/committer already configured in the repo (Piotr Kaczmarski, signed).
  Do not add `Co-Authored-By` or "Generated with" lines. Never push, never deploy, never
  touch `.env` or any secret, never call live Meta/Google/Stripe APIs.
- Do not edit `studio/ui/templates/studio.html`, `studio/ui/static/brief.js` or
  `studio/ui/i18n.py` (another agent works there in parallel); if you need a UI hint in
  the studio, put it in the JSON (`from_profile`) only.
- Report: what changed (files), test counts, anything left open.
