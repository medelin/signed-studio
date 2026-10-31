# Group B — accounts, sessions, limiter, settings
Owns: `studio/accounts.py`, `studio/app.py` (sessions, limiter, the
`/api/campaigns/{id}` visitor branch only), `studio/routes_account.py`,
`studio/social_login.py`, `studio/settings.py`, their tests. Do not change
response headers/CSP in app.py (group D owns Caddy headers). Read common rules.

1. **2FA lockout race** (accounts.py ~749-802, also ~902, ~1033). Gate, code check and
   failure count are three transactions; parallel requests all pass the gate.
   Check the lock, verify, and record the failure in one BEGIN IMMEDIATE
   transaction (commit the incremented count instead of rolling back).
2. **Limiter reset by non-credential errors** (routes_account.py 313, 334, 352, 382,
   466). `settle(ip, True)` clears the bucket on e.g. ConfirmationMismatch. Only a
   real success may clear; other form errors settle without clearing.
   Also check accounts.delete order: verify password before the confirm text,
   or at least never let the confirm error reset the counter.
3. **Session eviction DoS** (app.py ~277-294, ~240-243, ~663). Global LRU of 1000
   evicts other users. Cap per account (evict that account's oldest) and keep a
   global cap that refuses new sessions (503/limit error) instead of evicting
   others; count successful logins toward the per-prefix rate. Same idea for
   social_login's OAuth state LRU (~119): per-client bound, or refuse rather
   than evict.
4. **argon2 on the event loop**: run password hashing/verification in the
   threadpool in async routes (login, account forms).
5. **Provider accounts without password** (accounts.py ~856 totp_begin, ~1030 delete):
   accept a fresh provider sign-in (`external_fresh`, as change_password does)
   when `password_hash` is empty.
6. **Generated username may violate USERNAME** (accounts.py ~588-592): strip
   leading non-alphanumerics; fall back to provider name; assert fullmatch.
7. **`more_ad_external_ids` leaks to visitors** (app.py ~539-545): hash every
   Meta id field through one list, including list fields.
8. **settings._landing_url** (~416-421): ValueError from urlsplit must raise the
   named ConfigError, not UnboundLocalError.
9. **executor ONCE_INDEX** (`studio/executor.py:57`, take this file too): if creating
   the unique index fails, refuse to start (raise) instead of logging.
Tests: one per item; the race can be tested by calling the gate/check/fail
sequence from two threads or by asserting a single transaction.
