# Signed mandates

Every external write in Muse Studio Lite waits for a human signature: the owner's browser
signs the exact payload with a non-extractable Ed25519 key, and `studio verify` checks
that every write in the receipt log rides on such a signature. A **mandate** is the one
narrow exception. The owner signs a set of limits once; within them, a run started by
the owner's own AI assistant (an API or MCP token, see [assistants.md](assistants.md))
creates its campaign **PAUSED** without a new tap.

## The AP2 analogy

Google's Agent Payments Protocol separates an *intent mandate* (the user signs, in
advance, what an agent may buy and within which limits) from the *cart mandate* (the user
signs one concrete purchase). Until now the studio only had cart mandates: one signature
per campaign. A signed mandate is the intent mandate for ad buying. It is deliberately
smaller than AP2's: it can never spend. Creating a PAUSED campaign costs nothing; turning
it on (`campaign.activate`) is the spend, and that always needs the owner's own
signature.

## What the owner signs

`/account` → Assistants & API → **Mandates**: goals, countries, websites the ads may link
to, the highest daily budget, the most campaigns, days valid and which tokens may use it.
"Prepare mandate" makes a pending `mandate.grant` approval and opens the studio's signing
screen, which lists every limit in plain words above the hashes. The signed content is:

```json
{"action": "mandate.grant", "mandate": {
  "scope": ["campaign.create"],
  "account_id": "acct_…", "token_ids": ["tok_…"],
  "objectives": ["traffic"], "countries": ["PL"], "landing_hosts": ["example.com"],
  "max_daily_budget": 30, "currency": "PLN", "max_campaigns": 3,
  "valid_until": 1793836800}}
```

Checked before it can be signed (`mandates.check`): a closed schema; goals from the
objective table; ISO-2 countries; plain host names; a budget from 1 to the studio's
ceiling (`STUDIO_MAX_DAILY_BUDGET`) in the ad account's currency; 1 to 20 campaigns;
`valid_until` at most 30 days ahead; every token belongs to the account and is live.
Only the account the mandate is for can sign it. The shared judge account never sees the
block; on a live Meta adapter trial accounts do not either (the same rule as adding an own
video clip, `clip.may_add`), and the server refuses them at every step.

## What a run under a mandate does

When a run started by a token ends at its pending `campaign.create`, the worker looks for
a stored mandate of the token's account that lists the token, and checks the exact
payload (`mandates.fits`): the brief's goal, every targeted country (lookalike included),
the host of every URL in the ad (link, lead-form privacy and follow-up pages, and any URL
written anywhere in the payload: all `https`, all on the mandate's hosts), the daily budget
and currency, the pixel check, PAUSED everywhere, the count, expiry and revocation. If it
fits, in one write transaction: the approval's nonce is consumed, the approval is marked
approved by `mandate:<id>`, the mandate's use count goes up, and an `approval` receipt is
written with `by_mandate`, `mandate_sha256` and the `checked` values. The media buyer then
runs as after a human signature, `check_paused` first. If it does not fit, nothing
changes: the run waits for the owner, and `GET /api/v1/runs/{id}` returns
`"mandate": {"fits": false, "reasons": [...]}` next to the `sign_url`.

## What still needs a tap

Everything else: turning a campaign on, budget and status changes, replacing a creative,
replies and hidden comments, Google Ads, and any campaign outside the limits. The
executor (`executor.authorize`) accepts a mandate approval for `campaign.create` only, and
only when the stored mandate is the signed one (same hash as the signed grant), not
revoked, not expired, its use was recorded for this approval by a listed token, and the
payload still fits. No API or MCP operation creates, signs or revokes a mandate:
`GET /api/v1/mandates` (MCP tool `mandates`) only lists them.

Revoking (`/account`, session + CSRF) is immediate and leaves a `mandate.revoke` receipt.

## How `studio verify` checks it

- The `mandate.grant` approval is checked like every approval (approver signature, chain
  head, nonce), and its receipt must carry the body whose canonical hash is the signed
  `content_sha256`: a mandate edited after signing fails.
- An approval with `by_mandate` is valid only for `campaign.create`, only after that
  grant in the log, with the same `mandate_sha256`, before any `mandate.revoke` of it, no
  later than `valid_until`, with `checked` inside the limits (goal, countries, hosts,
  budget, currency), and no more such approvals than `max_campaigns`. The write and the
  adapter steps then bind to it like to any approval.

What the verifier cannot see: the payload itself is in the log only by hash, so
`checked` is the server's statement of the values it compared, signed by the ledger key
like every receipt. A server that lied in `checked` would be lying in a signed receipt
that names the exact campaign (`content_sha256`), which anyone holding the payload can
recompute.
