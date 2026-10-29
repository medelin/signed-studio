# Security

Muse Studio Lite lets AI agents draft ads, replies and campaigns for a real ad account. The
security goal is narrow and testable: **nothing leaves the studio — no post, no campaign, no
spend — unless a human signed exactly that content, and anyone can check afterwards that this
held.** Report a vulnerability privately to the repository owner (GitHub security advisory)
rather than in a public issue.

## Threat model

| Asset | Threat | Control | Where |
|---|---|---|---|
| Ad account, Page | an agent (or a prompt injection steering it) posts, creates or activates | models have no external-write tools; external skills become pending approvals built by code; execution needs a consumed, signed approval; every Meta object is created `PAUSED`; activation is a second approval under `STUDIO_MAX_DAILY_BUDGET_PLN` | `agents/dispatcher.py`, `skills.yaml`, `executor.py`, `agents/media_buyer.py`, `meta/*` |
| Human signature | replay, edit after signing, signing a different item, stale approval | Ed25519 over `{action, item_id, content_sha256, chain_head, approver, nonce, ts}`; nonce consumed in the same SQLite transaction as the state change; TTL 900 s, skew 60 s; key bound to the login session | `approvals.py`, `routes_studio.py` |
| Review | content changed after review, an image nobody reviewed | review bound to the hash of the variant and every placement; each placement reviewed; P9 code check | `agents/producer.py`, `agents/reviewer.py` |
| Customer text (comments, leads, transcripts) | prompt injection | untrusted envelope (neutralized markers, declared by the caller and checked before I/O), no tools on customer-text calls, code tightens model output (unapproved prices, links, PII → human) | `envelope.py`, `agents/community.py`, `agents/lead_scorer.py` |
| Personal data | sent to the Contributor tier, shown to visitors, stored in receipts | PII routing in code before any call (`choose_tier`), redaction in receipts and in every anonymous view | `pii.py`, `receipts.py`, `routes_inbox.public_view` |
| Receipt log | edited, truncated, re-ordered, rolled back, writes without approval | hash chain per run, ledger signature, signed manifest (seq, heads, counts), approval-before-write and step checks in `studio verify` | `receipts.py`, `cli.py` |
| Secrets | Model API key, Meta tokens leak | env only, `.env` mode 600 enforced, never in receipts, errors, URLs or `repr`; Meta token only in the graph adapter; the sandbox has none; gitleaks + kingfisher | `settings.py`, `muse/client.py`, `meta/graph.py` |
| Budget | runaway agent loop or abuse burns credits | per-run step and cost caps, durable daily budget with reservation before each call, sandbox limits and kill switch | `settings.py`, `agents/producer.py`, `sandbox.py` |
| Web app | CSRF, session theft, brute force, oversized bodies, XSS | same-origin check on writes, HttpOnly/SameSite/Secure session, login limiter, body limits, CSP without inline script, autoescaped templates | `app.py`, `sandbox.py` |
| MCP clients (`studio mcp`) | an external agent signs, sends, changes a budget or an account, or injects a send through a brief | stdio only, one active account; read and draft tools only, replay and a read-only mock Meta surface; a send answers `awaiting_human_signature` with the approval page; agent text enveloped like a transcript; a `tool_call` receipt per call (`mcp:<client>`) outside campaign runs | `mcp_server.py`, [mcp.md](mcp.md) |
| Accounts | see [Accounts](#accounts) below | argon2id, no enumeration, TOTP second factor, account-bound sessions, typed-confirmation deletion | `accounts.py`, `routes_account.py`, `cli_account.py` |

## Accounts

There is no self-registration: the operator creates accounts with `studio account create`
(password from the terminal or stdin, never argv). The built-in `judge` account is the
*demo* account, seeded from `JUDGE_PASSWORD` at every start (disabled when it is empty).

| Threat | Control | Where |
|---|---|---|
| Password guessing online | argon2id (argon2-cffi defaults: t=3, 64 MiB, p=4); the login limiter (5 attempts per client prefix per 15 min, counted before the body is read, plus the short capped global pause); 12+ characters, a common-password list, not the user name | `accounts.py`, `app.py` |
| User enumeration | an unknown name verifies against a dummy hash (the same work); unknown, disabled, deleted and wrong-password logins get the same status and one generic sentence; the second step has its own single generic sentence | `accounts.authenticate`, `routes_account` |
| Stolen password | optional TOTP (RFC 6238, SHA-1, 6 digits, ±30 s); enrolment needs the password and a confirming code; disabling needs the password and a code | `accounts.py` |
| Second-step guessing | the password step that succeeds for a two-step account does **not** clear the client's attempt bucket, so the password and code attempts share the same 5 per 15 min; the code step reserves before reading the body; a challenge dies after 3 wrong codes or 5 minutes. Per account, whatever the client or challenge, wrong codes are counted in the database: after 5, each further one locks code checks for 30 s × 2ⁿ (at most 1 h; even a right code waits), reset by a right code or an operator reset | `app.py` (`settle(clear=False)`), `accounts.Challenges`, `accounts._code_failed` |
| Stale second step | a challenge remembers the account's credential generation, which every password change, operator reset, enable/disable, 2FA change and deletion bumps (in the database, so also from the CLI process); `/login/2fa` refuses a challenge whose generation changed | `accounts.credential_gen`, `verify_second_factor(gen=…)` |
| Code replay | the last accepted TOTP step is stored and updated with a compare-and-set, so a code (or an older one in the window) is accepted once, also under concurrency; recovery codes (10 × 80 bits) are stored as SHA-256 hashes and consumed with `used_ts IS NULL` | `accounts._check_code` |
| TOTP secret at rest | sealed with AES-GCM under a key derived (HKDF-SHA256) from the ledger key, with the account id as associated data; a copy of `studio.db` alone does not yield codes | `accounts.SecretBox` |
| Session fixation | a new session is issued on every login and on the completion of the second step; the cookie the browser brought is revoked; no session exists between the two steps, only a 5-minute HttpOnly, SameSite=Strict challenge cookie scoped to `/login` | `routes_account.start_session` |
| Session after a change | each session has a row (hash of its id, account, created, last seen, a short browser label, never the raw User-Agent); a cookie is valid only while its row is live and its account is `active`, so a password change (other sessions), "sign out all others", `studio account disable` (from another process) or a deletion take effect on the next request; revoking a session revokes its approver keys | `accounts.AccountSessions` |
| CSRF on account forms | every `/account/*` and `/login/2fa` POST checks `Origin`, the Gate's `Sec-Fetch-Site`, and the `csrf` form field against the double-submit cookie; read-only mode refuses them | `routes_account.posted`, `auth.FORM_CHECKED_PREFIXES` |
| A stolen session cookie as a password oracle | the forms that check a password or a code count against the same login limiter | `routes_account.limited` |
| Locking judges out | on the demo account the password, the second factor and deletion cannot be changed from the UI; the operator CLI refuses it too | `accounts.DemoAccount` |
| Deletion vs. the ledger | receipts are append-only and hash-chained, so they stay; they name an approver only by key fingerprint. Deletion erases every personal field, the password hash, the second factor and the recovery codes, revokes every session and key, and keeps a tombstone with the random account id, so a fingerprint resolves to that pseudonym only | `accounts.delete` |
| Caching of secrets | account pages, the second step and the export are `Cache-Control: no-store`; recovery codes are shown once | `routes_account.NO_STORE` |
| Notification mails | off unless configured; JMAP over HTTPS (Basic auth, no redirects followed, certificate verified by httpx) or SMTP over implicit TLS, 10 s timeout, sent after the response on a worker thread so a slow or failing server cannot block or break a request; credentials only from the environment (hidden from `repr`, never logged or receipted; errors log the exception class and SMTP code); bodies carry a count and a link, never lead data; one receipt per mail with the account id and no address; leads and recommendations rate-limited to one mail per ten minutes per account; the security mails (password, 2FA, new sign-in, export, deletion) cannot be switched off, so a hijacked session cannot stay quiet; the judge account never mails | `notify.py` |

## Attack suite (`tests/attack/`, run in CI)

| File | What it attacks |
|---|---|
| `test_dispatcher.py` | a scripted model asking for `create_campaign`, `activate_campaign`, `post_reply`, `hide_comment` with ACTIVE status; smuggled payloads; forged skill registry |
| `test_paused_only.py` | ACTIVE at creation (top level or nested, even signed); missing, forged, replayed, expired or mismatched approvals; swapped image file |
| `test_activation_chain.py` | activation without its own approval, over the ceiling, failure mid-chain |
| `test_external_steps.py` | adapter steps without an approval, on another run's approval, after the write, after the TTL, in an edited export |
| `test_injection.py` | 10-comment injection corpus with a fully obedient model: draft only, no tools, no approvals, no writes |
| `test_untrusted_paths.py` | every customer-text path wraps its input (canary per path, completeness over `studio/`) |
| `test_voice_transcript.py` | a voice brief stays enveloped customer text after edits |
| `test_comment_pii.py` | PII in comments never reaches the model unredacted |
| `test_guest_views.py` | anonymous views of campaigns, comments and leads |
| `test_review_binding.py`, `test_review_every_format.py` | content swapped after review; unreviewed placements |
| `test_unfilled_fact.py` | `[FACT]` placeholders hidden with look-alike characters |
| `test_parallel_cost.py`, `test_placements_cost.py` | cost cap bypass through parallel tool calls and placements |
| `test_objective_binding.py` | every objective created PAUSED from the signed delivery only; a signed but invalid mapping, a payload without one, another Page: no call; verify rejects a step that sent another mapping |
| `test_lead_ads.py` | the instant form without the owner's privacy page; unsigned, wrongly signed or unconfigured leadgen webhooks; leads for forms the studio did not make; contact data in receipts |
| `test_recommendations.py` | a recommendation executed without, or twice on, a signed `campaign.update`; a budget over the ceiling; a "pause" that activates; verify on an update logged without approval |
| `test_mcp.py` | every MCP tool with valid, extra and malformed arguments and invented signing tools while signing, execution, Meta writes and account changes are tripwires; a prompt injection in the brief fields and a forged approval in a `[FACT]` answer; verify on writes that name an MCP receipt or the unsigned pending approval |

Accounts: `tests/test_account_pages.py` (wrong current password, a reused TOTP code, a
recovery code used twice, CSRF missing or forged on every account form, cross-origin posts,
a deleted account's old cookie, a disabled account, judge deletion and 2FA refused, the rate
limit on the second step, session fixation, one generic failure), `tests/test_accounts.py`
(RFC 6238 vectors, sealing, policy) and `tests/test_account_cli.py`.

Plus `tests/test_verify_cli.py` (19 tamper classes → exit 2 naming the receipt) and the
generated tamper suite in [`docs/eval.md`](eval.md).

## Meta webhook

`POST /api/meta/webhook` takes Page `leadgen` events. It is refused (403) unless
`X-Hub-Signature-256` is `sha256=` + the HMAC-SHA256 of the raw body keyed with
`META_APP_SECRET` (constant-time compare); without an app secret every request is refused.
A valid event can only make the studio fetch a lead of a form it created itself, on its own
Page, and store it once; it cannot write to Meta or approve anything. The route sits behind
the login gate like every other, so it answers only where `STUDIO_REQUIRE_LOGIN` is off
(the operator's machine); the sandbox never receives Meta events.

## Landing-page fetch (pixel check)

The pixel check (`studio/meta/pixel.py`) is the only place the studio fetches a URL the
owner typed (`advertiser.landing_url`), so the fetch is built against SSRF: https on port
443 only, no userinfo; the host is resolved and refused unless every address is public
(no private, loopback, link-local, CGNAT `100.64.0.0/10`, multicast, reserved or
unspecified address, v4 or v6, including IPv4 embedded in mapped, 6to4 or Teredo
addresses); the request goes to the vetted address itself, with the host name as `Host`
and TLS SNI (certificate checked for that name), so a second DNS answer cannot move it. At
most 3 redirects, each re-checked the same way; 10 s deadline, 2 MB cap, a fresh client
per hop (no cookies), no proxy from the environment, a plain user agent. The fetched HTML is
only searched for the pixel id and never stored, rendered or passed to a model. With an
injected Graph transport (tests, replay) nothing is fetched at all.

## Residual risks

- **TOFU in `studio verify --server`.** Without a pinned key, `--server` trusts the key and
  `seq` the server serves at that moment; a compromised server can serve a matching forged
  key. Pin `ledger.pub` out of band (`--key-file`) and keep the highest `seq` you have seen
  (`--min-seq`).
- **A server that lies by omission.** The ledger key lives on the server. Verify catches a
  write logged without an approval, but not a write the server made and never logged. The
  Meta side (Ads Manager history) is the cross-check.
- **Heuristic PII detection.** `pii.py` finds e-mails, PL/UK phone numbers, NIP and PESEL
  (with checksums) and long digit runs; a phone number spelled out in words or split across
  fields is not detected, and relies on the reviewers and the human reading the payload.
- **Heuristic P9 check.** `reviewer.policy_net` matches known phrasings (EN/PL); a
  paraphrase relies on the two model reviewers and the human.
- **Prompt injection is mitigated, not solved.** The envelope reduces it; the boundary is
  "no tools on customer-text calls + a human signature on every send".
- **Graph API behaviour** (token transport, activation order, `appsecret_proof`): the create path
  was run live once on 2026-10-29 (campaign, ad set and ad, all PAUSED); activation and other
  writes are tested against hand-written cassettes.
- **Second-opinion reviewer** is unavailable without `BASETEN_API_KEY`; the approval screen
  says so.
- **Sessions live in process memory**: a restart signs everyone out and revokes their keys
  (past approvals stay verifiable). Pending second steps are in memory too: a restart between
  the two steps means signing in again.
- **TOTP secrets depend on the ledger key.** Replacing `ledger.key` (possible only while the
  ledger is empty) makes every sealed secret unreadable: those users cannot finish the second
  step until the operator runs `studio account reset-password <user> --clear-2fa`.
- **No account recovery by e-mail.** The studio sends no e-mail; a lost password (or a lost
  phone without recovery codes) needs the operator's `reset-password`.
- **Code lock as a nuisance.** Someone who knows a user's password can keep that user's
  second step locked (at most an hour at a time) by sending wrong codes; the cure is a new
  password (`/account` or the operator), which also clears the lock.
- **Shared demo account.** Judges share `judge`; one judge can see the others' sessions (a
  browser label and times) and sign them out, which costs them a fresh login, never a
  lock-out.
