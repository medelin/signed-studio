# After the video: sign-in with PayPal and Facebook, finish, owner's tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (one implementer per task, a reviewer after each). Steps use checkbox (`- [ ]`) syntax.

**Goal:** judges and new users sign in to https://pay.udun.eu with PayPal or Facebook in one
step; the submission video and text are final; the owner's own test round is prepared.

**Architecture:** the base already has Google/Facebook sign-in (`studio/social_login.py`,
`studio/routes_social.py`: OAuth 2.0 code flow, server-held `state`, `SameSite=Lax` state
cookie, bounce page for the `Strict` session cookie, new identities become `trial` accounts,
linking only from a signed-in session). PayPal becomes a third provider of the same shape
("Log in with PayPal" is OAuth 2.0 / OpenID Connect on the same sandbox app the studio already
uses for money). Facebook needs configuration only.

**Tech Stack:** Python 3.13, FastAPI, httpx, SQLite; pytest; PayPal Identity API (sandbox).

**Spec:** this file plus `docs/plans/2026-11-05-paypal-judging-fit.md` (P4 remains).

## Global Constraints

- PayPal sandbox only; never live keys. No secret in a commit, a test, a log or a receipt.
- Everything new is tested; `.venv/bin/python -m pytest -q` stays green (3,271+ tests).
- English names in code, keys, paths. i18n: every new string in EN and PL.
- The session cookie stays `SameSite=Strict`; the OAuth state cookie stays `SameSite=Lax` on
  `/auth/`. No loosening of the CSP (`form-action`, `connect-src`).
- A provider's e-mail never links an existing account (identity = provider + stable user id).
- Commits by Piotr Kaczmarski, SSH-signed, no trailers. Deploy to pay only by the operator.

## Review Focus

1. A callback with a valid state but a PayPal `code` that the token endpoint rejects → the user
   sees `social.error`, nothing is registered, the state is consumed.
2. `userinfo` without `user_id` (or with an id longer than 255) → refused, no account.
3. PayPal's `email_verified` false or absent → the e-mail is not kept on the account.
4. The same PayPal user signing in twice → one account, two sessions, no duplicate.
5. `PAYPAL_LOGIN_CLIENT_ID` set without its secret, or with `PAYPAL_ENV=live` → startup
   refuses with a message naming the variable (same as Google/Facebook).

---

## Part A — finish the video (operator, no subagent)

- [x] A1 Vault: saving the same PayPal again moves PayPal's reused vault id to the new save
      (`studio/paypal/vault.py` `finish`), another account is refused (`saved_elsewhere`).
      Tests in `tests/test_paypal_vault.py`. Full suite, commit, deploy.
- [x] A2 Re-record the mandate scene in English (`--lang=en-US`, `locale="en-US"` in
      `record.py`), render `edit2.py`, copy to `media/video/signed-studio-demo.mp4` + captions.
- [x] A3 Operator uploads to YouTube (replace or new link); update `docs/devpost.md`, Devpost
      form (video field), `docs/demo-script-paypal.md` beats.
- [ ] A4 Afterwards: revoke the recording token (`demo video assistant`), disable `demo`
      (`account disable demo`), delete the recording vault/mandate from the demo account.

## Part B — Facebook sign-in on pay (configuration, operator)

- [x] B1 Meta app (the one `muse-studio-lite` uses, `FACEBOOK_LOGIN_CLIENT_ID` on `muse`):
      add `https://pay.udun.eu/auth/facebook/callback` to **Valid OAuth Redirect URIs**
      (Facebook Login → Settings) in neo.
- [x] B2 `/etc/muse-pay/env`: `FACEBOOK_LOGIN_CLIENT_ID`, `FACEBOOK_LOGIN_CLIENT_SECRET` (copy
      from `muse:/etc/muse-studio-lite/env`, mode 640, never through the chat), restart.
- [ ] B3 Check: `/login` shows "Continue with Facebook"; a sign-in with the operator's own
      Facebook test user creates a `trial` account; `studio verify` unaffected.
- [ ] B4 Optional, same way: Google (`GOOGLE_LOGIN_*`, redirect URI in Google Cloud console).

## Part C — Log in with PayPal (subagent, Sonnet 5.5; reviewer Opus 5.5)

### Task C1: the `paypal` provider in `studio/social_login.py`

**Files:**
- Modify: `studio/social_login.py` (PROVIDERS, SocialLoginConfig, fetch_identity)
- Modify: `studio/settings.py` (nothing new: `SocialLoginConfig.from_env` reads the env;
  pass `PAYPAL_ENV` through, see below)
- Test: `tests/test_social_login.py`

**Interfaces:**
- Consumes: `SocialLoginConfig.from_env(e)`, `fetch_identity(provider, client, code,
  redirect_uri, pending, http)`, `Identity(provider, subject, email, name)`.
- Produces: provider name `"paypal"` accepted by `/auth/paypal/start` and
  `/auth/paypal/callback` (routes are generic; no route change).

**Facts (PayPal docs, 7 Nov 2026):**
- Authorize: sandbox `https://www.sandbox.paypal.com/connect`, live
  `https://www.paypal.com/connect`; query `flowEntry=static`, `client_id`, `response_type=code`,
  `scope=openid profile email`, `redirect_uri`, `state`. No PKCE (`pkce=False`).
- Token: POST `https://api-m.sandbox.paypal.com/v1/oauth2/token` (live `api-m.paypal.com`),
  **HTTP Basic `client_id:secret`**, form `grant_type=authorization_code&code=…`
  (PayPal does not take `client_id`/`client_secret` in the body like Google does). Answer has
  `access_token`, `token_type`, `expires_in`, `refresh_token` (discard the refresh token).
- Userinfo: GET `https://api-m.sandbox.paypal.com/v1/identity/oauth2/userinfo?schema=paypalv1.1`
  with `Authorization: Bearer <access_token>`. Fields: `user_id` (stable id, the subject),
  `name`, `email`, `email_verified`, `emails[]` (objects with `value`, `primary`, `confirmed`).
  Keep the e-mail only when `email_verified is True` (or the primary entry in `emails` has
  `confirmed: True`); otherwise `""`.
- Scopes need no review in the sandbox. The studio's own sandbox app (`PAYPAL_CLIENT_ID`) can
  have "Log in with PayPal" enabled, so the same client id and secret serve both.

- [ ] **Step 1: tests first** — in `tests/test_social_login.py` add, following the Google/
      Facebook fakes already there:
      - `test_paypal_start_uses_connect_with_static_flow_and_no_pkce`: `/auth/paypal/start`
        → 303 to `https://www.sandbox.paypal.com/connect?...` with `flowEntry=static`,
        `response_type=code`, `scope=openid profile email`, `state`, `redirect_uri`
        ending `/auth/paypal/callback`; no `code_challenge`.
      - `test_paypal_token_exchange_uses_basic_auth_and_userinfo_bearer`: the fake transport
        asserts the token POST carries `Authorization: Basic base64(id:secret)`, body
        `grant_type=authorization_code&code=…` and no `client_secret`; the userinfo GET carries
        `Authorization: Bearer <token>` and `schema=paypalv1.1`; `Identity.subject` is `user_id`.
      - `test_paypal_unverified_email_is_not_kept`: `email_verified: false` → `email == ""`.
      - `test_paypal_userinfo_without_user_id_is_refused`: no `user_id` → `ProviderError`,
        no account created.
      - `test_paypal_login_env_needs_both_halves_and_sandbox`: `PAYPAL_LOGIN_CLIENT_ID` alone
        → `ValueError` naming it; with `PAYPAL_ENV=live` → `ValueError` ("sandbox only").
      - `test_login_page_offers_the_configured_providers` extended: with PayPal configured the
        page shows `Continue with PayPal` first.
- [ ] **Step 2: run them; they fail** (`KeyError: 'paypal'` / assertion).
- [ ] **Step 3: implement**
      - `PROVIDERS["paypal"] = Provider("paypal", <authorize>, <token>, "openid profile email", False)`
        where the hosts come from a module-level `paypal_hosts(env: str) -> tuple[str, str]`
        (`"sandbox"` → sandbox URLs, anything else → live). Keep `PROVIDERS` static for the
        sandbox and let `SocialLoginConfig` carry `paypal_env` (default `"sandbox"`); in
        `fetch_identity`/`start` use `config`-aware URLs (smallest change: build the two URLs
        in `SocialLoginConfig` for PayPal and read them from there).
      - `SocialLoginConfig`: fields `paypal_id`, `paypal_secret` (repr=False), `paypal_env`;
        `from_env` reads `PAYPAL_LOGIN_CLIENT_ID`, `PAYPAL_LOGIN_CLIENT_SECRET`, `PAYPAL_ENV`
        (both halves or none; `PAYPAL_ENV` other than `sandbox` with a login id →
        `ValueError("PAYPAL_LOGIN_* is sandbox-only in this release")`). `providers()` order:
        `("paypal", "facebook", "google")`.
      - `fetch_identity`: for `paypal`, token POST with `auth=(client_id, secret)` and form
        `{"grant_type": "authorization_code", "code": code}` (PayPal ignores `redirect_uri`
        here; do not send the secret in the body); userinfo GET as above; subject =
        `data["user_id"]`; e-mail rule as in Facts; name = `data.get("name")`.
- [ ] **Step 4: tests pass**, then the whole file: `pytest -q tests/test_social_login.py`.
- [ ] **Step 5: commit** `feat(login): sign in with PayPal (sandbox): OAuth code flow,
      Basic-auth token exchange, userinfo user_id as the identity`.

### Task C2: button, copy, docs

**Files:**
- Modify: `studio/ui/i18n_accounts.py` (EN+PL: `social.continue_paypal`, `social.paypal`;
  `social.lead` now names PayPal, Facebook or Google), `studio/ui/static/components.css`
  (`.social-paypal` in the house style: no PayPal logo asset, text button like the others),
  `studio/ui/templates/login.html` (no change if it loops `social`).
- Modify: `docs/SANDBOX.md` or `deploy/PAY-DEPLOY.md`: the three env variables and the
  Developer Dashboard steps (app → Other features → Log in with PayPal → Advanced Settings →
  Return URL `https://pay.udun.eu/auth/paypal/callback`, share: full name, e-mail; privacy
  and user-agreement URLs `https://pay.udun.eu/help#privacy`).
- Test: `tests/test_i18n*.py` (every key in both languages) and the login page test from C1.

- [ ] Steps: add strings → run `pytest -q tests/test_i18n*.py tests/test_social_login.py`
      → commit `feat(login): PayPal button and setup notes`.

### Task C3: wire-up on pay (operator)

- [x] Developer Dashboard (neo, signed in): sandbox app of the studio → enable "Log in with
      PayPal", Return URL `https://pay.udun.eu/auth/paypal/callback`, share name + e-mail.
- [x] `/etc/muse-pay/env`: `PAYPAL_LOGIN_CLIENT_ID` = `PAYPAL_CLIENT_ID`,
      `PAYPAL_LOGIN_CLIENT_SECRET` = `PAYPAL_CLIENT_SECRET` (copied inside the file; never
      through the chat). Deploy, restart.
- [x] Check with the sandbox buyer (`sb-43c6n834336069@personal.example.com`): a new `trial`
      account appears; `Account → Sign-in` shows the PayPal link; sign-out and back in gives
      the same account. Add a line to `docs/devpost.md` (judges may sign in with PayPal
      using the sandbox buyer login from the private field).

## Part D — the owner's test round (operator + fixes by subagents)

- [ ] D1 Test checklist page (artifact) in Polish: register with PayPal, Facebook; brief →
      draft → questions → run; pay by card; sign; decline; activate; mandate (full account
      only — the operator's own account, since `trial` cannot grant); assistant run over MCP;
      money page; `studio verify`; PL/EN; mobile width.
- [ ] D2 Each finding becomes one task here (file, repro, expected), implemented by a Sonnet
      subagent with a failing test first; reviewer before merge; deploy in one batch.

## Not doing (this round)

Live PayPal keys or a PayPal app review; linking the PayPal identity to the Vault (a saved
PayPal stays an explicit, separate step); Google unless B4 is asked for; merchant dashboard
in the video.

## Part E — the read-only audit of 7 Nov (Codex, `PAYMUSE_READONLY_AUDIT_2026-11-07.md`)

Verified against `d15cb49`…`311089f` by the operator's session before planning. Each task is
one subagent (Sonnet 5.5) in its own worktree, failing test first, reviewer before merge.
Files are disjoint between tasks that run in parallel.

### Task E1 (P1): account deletion ends saved PayPal and mandates; a charge re-checks the account

**Files:** `studio/accounts.py` (`_erase`, the deletion transaction only), `studio/mandates.py`
(`revoke_all(conn, ledger, account_id, reason="account_deleted")`), `studio/paypal/vault.py`
(`retire_all(account_id) -> list[str]`: every row of the account → `deleted` locally, then a
best-effort PayPal DELETE per token with the existing `delete` semantics), `studio/paypal/hooks.py`
(`Funder.charge`: after PayPal answers, re-read the account; if it is not `active` → void the
authorization and settle the charge as `refused:account_gone`), `studio/routes_account.py` (the
delete route calls the three in this order: mandates → vault → erase, inside one request; PayPal
calls after the local transaction). Tests: `tests/test_accounts.py`, `tests/test_paypal_vault.py`,
`tests/test_mandate_charge.py` (or the file that holds the charge tests).
- Tests: deletion revokes N mandates and retires M vaults (local status first, receipts
  `mandate.revoke` / `paypal.vault_delete`); deletion while PayPal DELETE fails (500) still
  leaves the vault `deleted` locally; a charge whose account is deleted between reservation and
  PayPal's answer is voided and recorded; the account export after deletion raises.
- Keep `credential_gen` bump and session revocation as they are.

### Task E2 (P1): no blocking PayPal I/O on the event loop; a tighter public webhook

**Files:** `studio/routes_paypal.py` (every `async def` that calls the PayPal client or the
webhook verifier: wrap the blocking call in `starlette.concurrency.run_in_threadpool`; the
webhook additionally under an `asyncio.Semaphore(4)` and refusing with 429 when it is full),
`studio/routes_mandates.py` (vault start/delete the same way), `studio/paypal/webhooks.py`
(`cert_url_allowed`: host **and** path pattern `^/v1/notifications/certs/` …; a 60-s negative
cache of failed cert URLs capped at 64 entries; cert fetch timeout 5 s), `studio/paypal/hooks.py`
is NOT touched (E1 owns it); the webhook's resource read gets its own `timeout=8.0` through the
client's existing per-call timeout parameter (add one if `PayPalClient.get` has none:
`studio/paypal/client.py`). Tests: `tests/test_paypal_webhooks.py`, `tests/test_routes_paypal*.py`.
- Tests: a webhook with a cert URL on the right host but a wrong path is refused before any
  fetch; a failed cert URL is not fetched again within 60 s; the fifth concurrent webhook gets
  429; `/healthz` answers while a webhook's cert fetch sleeps 3 s (use a fake transport with a
  delay and `httpx.AsyncClient` against the app).

### Task E3 (P2, small): live tests opt-in; nginx limits; installer excludes; docs drift

**Files:** `tests/test_paypal_live.py` (skip unless `RUN_PAYPAL_LIVE_TESTS=1` **and** the file
exists; `pytest.ini`/`pyproject`: marker `live`), `deploy/nginx/signed-studio-pay.conf`
(per-path `client_max_body_size`: 32m for the voice route, 64m for clips, 2m default — mirror
`deploy/apache` and `deploy/caddy` examples), `deploy/pay-install.sh` (refuse to install when
the source dir contains `.env`, `sandbox.env`, `*.db`, `*.key`, `receipts*.jsonl` outside
`tests/`; print which), `deploy/PAY-DEPLOY.md` (webhook: registered and verified since 5 Nov;
remove "Day 4"), `README.md` (host names: pay.udun.eu for the PayPal edition),
`studio/ui/templates/account_assistants.html` (mandate amounts through the existing
zero-decimal-aware formatter used by `app.js`, or a `money` filter in Jinja).
- Tests: live module skipped without the env var even when the file exists; installer refusal
  (a shell test or a pytest calling the script with a temp dir); the mandate summary renders
  `HUF 1500` not `HUF 15.00`.

### Task E4 (P1/P2): privacy notice and export for the PayPal edition

**Files:** `studio/ui/legal_texts.py` (EN+PL: pay.udun.eu, PayPal as processor, PayPal's JS SDK
on the approval page, what is stored: vault references and PayPal ids, signed webhook bodies,
fundings; retention; deletion behaviour per E1), `studio/accounts.py` (`export` gains
`mandates`, `saved_paypal` (ref, SHA-256, status, dates — never token ids), `fundings`,
`mandate_charges`), `studio/routes_account.py` (the export route passes the PayPal stores).
Runs after E1 merges (both touch `accounts.py`). Tests: `tests/test_account_pages.py`,
`tests/test_legal_texts*.py`.

### Task E5 (P2): one PayPal order per approval; `onApprove` binds the exact order

**Files:** `studio/funding.py` (partial unique index: one funding in state `created`/`approved`
per `(item_id, content_sha256)`; `create` returns the existing open one on conflict),
`studio/routes_paypal.py` (`payer-approved` takes `order_id` from the body and matches it to the
approval's funding; mismatch → 409), `studio/ui/static/paypal.js` (send `data.orderID`). Tests:
two concurrent creates → one order; approval with another order id → 409.

### Task E6 (P2): durable retry of PayPal token deletion

**Files:** `studio/paypal/vault.py` (table `vault_deletions(vault_ref, token_id, attempts,
next_ts, last_error)`; `delete` enqueues when PayPal fails; `retry_deletions(now)` called from
the hourly sandbox-reset tick and from `studio` CLI `vault retry`; after PayPal 204/404 erase
`token_id`), operator view in `/money` not required. Tests: failed delete is retried and the
token id erased after success.

### Decision (operator, 7 Nov): single workspace now; accounts per client after the hackathon

Registration is open now (PayPal, Facebook). Every signed-in account shares the operator's
Studio workspace (`STUDIO` ownership): fundings, receipts and approvals created in the UI are
visible to all. For the hackathon this is the declared single-operator model; the privacy text
(E4) and the login page say so. Real per-account separation is a later project, not this week.

**Not doing now:** SBOM/CVE scan in CI, digest-pinned images, BLOB storage of webhook bodies,
`STUDIO_PUBLIC_URL` made mandatory (worth a line in PAY-DEPLOY.md: set it).
