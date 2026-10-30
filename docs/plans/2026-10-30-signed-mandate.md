# Plan E — A signed mandate: the owner's assistant creates PAUSED campaigns within limits

**Goal.** Today an owner's AI assistant (API/MCP token, scopes `read`/`draft`) can draft and
run, but every campaign waits for the owner's tap. A **mandate** is a one-time, signed,
limited permission — the ad-buying counterpart of AP2's "intent mandate": the owner signs
the limits once on their phone (same WebCrypto Ed25519 key and approval flow as every
other signature); within them, a run started by that owner's token creates its campaign
**PAUSED** without a new tap. Anything outside the limits, and every activation (spend),
still needs the owner's own signature. Minimal version for the hackathon.

## The mandate (signed payload, action `mandate.grant`)
```
{"action": "mandate.grant", "mandate": {
  "scope": ["campaign.create"],            # only this; never activate, budget or reply
  "account_id": "<the owner's account>",
  "token_ids": ["<api token id>", ...],    # which of the owner's tokens may use it (≥1)
  "objectives": ["traffic", ...],          # Brief v1 goals allowed (objectives.TABLE)
  "countries": ["PL", ...],                # ISO-2; every campaign's countries ⊆ these
  "landing_hosts": ["example.com"],        # every destination URL's host ∈ these
  "max_daily_budget": 10,                  # whole units of the ad account currency
  "currency": "PLN",                       # must equal the ad account currency
  "max_campaigns": 5,                      # campaigns created under this mandate, ≤ 20
  "valid_until": <unix ts>                 # ≤ 30 days from signing
}}
```
Validated by code before it can be signed (pending): all fields present, lists non-empty
and bounded, budget ≤ the server's daily ceiling (`settings.max_daily_budget`),
`valid_until` in (now, now+30 d], tokens belong to the account and are not revoked/expired.

## Scope
1. **`studio/mandates.py`** (new): table `mandates(id, account_id, body TEXT, sha256,
   approval_id, created_ts, revoked_ts, used INTEGER DEFAULT 0)` via the module's own
   `ensure_schema` called where other modules' schemas are (follow `clip.ensure_schema`);
   `request(conn, ledger, account, body) -> Pending` (uses the existing pending/approval
   machinery in `studio/approvals.py` exactly like other actions — read how
   `campaign.activate` requests are built in `studio/agents/media_buyer.py`),
   `activate(conn, approval)` stores it once signed, `fits(mandate, payload) ->
   list[str]` (every reason it does not fit; empty = fits: objective, countries,
   landing hosts of every URL in the payload's delivery spec, daily budget, currency,
   count, expiry, revoked, token), `revoke(conn, ledger, account, mandate_id)`.
2. **Executing under a mandate**: where a run reaches `awaiting_approval` for
   `campaign.create` (find it in `studio/agents/producer.py` / the approval request path),
   if the run was started by an API token (assistants path, `studio/assistants.py`, the
   principal's token id is known) and a live mandate of that account lists the token and
   `fits(...)` is empty: the server records an approval receipt of kind `approval` with
   `details={"by_mandate": mandate_id, "mandate_sha256": …, "checked": {objective,
   countries, hosts, daily_budget, currency, n_of_max}}` and the payload's
   `content_sha256`, consumes it, and runs the executor exactly as after a human
   signature (PAUSED only — `check_paused` still applies). Increment `used` in the same
   transaction. If it does not fit, nothing changes: the run waits for the owner as today,
   and the reasons are returned to the assistant (`awaiting_human_signature` + `mandate:
   {"fits": false, "reasons": [...]}`).
   The executor's check (`studio/executor.py`: "a consumed, human-signed approval") must
   accept a mandate approval **only** for `campaign.create` and only with a stored,
   unrevoked, signed mandate whose sha256 matches — add that branch with its own error
   codes; every other action keeps requiring a human signature.
3. **`studio verify`** (find the verifier, `studio/receipts.py`/`studio/cli.py`): an
   execution whose approval receipt has `by_mandate` is valid only if the chain contains
   the signed `mandate.grant` approval with that sha256 (approver signature checked like
   any approval), before it in time, not revoked before it (a `mandate.revoke` receipt),
   and `checked` satisfies the mandate body (the grant receipt must therefore carry the
   mandate body or its canonical JSON hash + the body in details — choose carrying the
   body, it is small and has no personal data). Add tamper cases to the eval/tests:
   mandate edited after signing, execution after revoke, after expiry, budget above the
   mandate, n > max_campaigns.
4. **UI** — `studio/ui/templates/account_assistants.html` (+ its route in
   `studio/routes_account.py` only if strictly needed; prefer a new small router file
   `studio/routes_mandates.py` registered in `app.py`): a "Mandates" block under the
   tokens: a form (objectives as checkboxes from the objectives table, countries text,
   landing hosts text, daily budget, max campaigns, days valid 1–30, tokens as
   checkboxes) → "Prepare mandate" creates the pending → the owner signs on the studio
   approval screen (reuse the existing approval page/flow; the content shown must list
   every limit in plain words). List of mandates: limits, used n/max, valid until,
   status, "Revoke" (session + CSRF, `mandate.revoke` receipt). Hidden for the demo
   (judge) and trial accounts on a live adapter (same rule as `clip.may_add`), shown on
   the mock adapter.
5. **API/MCP**: `GET /api/v1/mandates` (read scope: the token's account's mandates and
   whether this token is listed); the assistants `run` result includes the mandate
   decision. No endpoint can create, sign or revoke a mandate with a token.
6. **Docs**: `docs/mandates.md` (what it is, the AP2 analogy in one paragraph, limits,
   what still needs a tap, how verify checks it).

## Tests
Unit: `fits` for every limit (each reason), expiry, revoke, count; request validation
(30-day cap, ceiling, foreign token). Integration (mock adapter, replay/mocked models as in
`tests/test_assistants*.py`): a token run inside the mandate → campaign PAUSED, approval
receipt `by_mandate`, `used` incremented; outside (other country, bigger budget, other
token, revoked, expired, n exhausted) → waits for the owner with reasons; activation
never by mandate; the judge account cannot create one on a live adapter. Verify: clean
chain passes; the five tamper cases fail with a named receipt. i18n parity.

## Constraints (all mandatory)
`~/code/muse-studio-lite`; ruff + mypy clean; full pytest (~13 min) green before the final
commit; English code; EN+PL plain copy; CSP; accessibility. Signed commits with the
configured identity, no Co-Authored-By / Generated-with. Never push, deploy, touch
`.env`/secrets, call live APIs. No new dependencies. **Reserved for the parallel agent
(plan C), do not edit:** `studio/billing.py`, `studio/routes_billing.py`,
`studio/ui/legal_texts.py`, `studio/routes_help.py`, `studio/ui/templates/account.html`,
`studio/ui/templates/base.html`, `studio/ui/templates/login.html`. Report: files changed,
test line, open points — especially anything where the safety argument depends on an
assumption.
