# Judge sandbox

Live at **https://muse.udun.eu**. The whole app is behind the judge login at `/login`:
user `judge`, password in the submission form (`STUDIO_REQUIRE_LOGIN=1`, the default with
`STUDIO_SANDBOX=1`). Without a session, pages redirect to `/login?next=…` (a same-site path
only, no open redirect) and the API answers 401 JSON. Public and read-only without a session:
`/healthz`, static assets, and what `studio verify --server` fetches — `/api/ledger-key`,
`/api/ledger-manifest`, `/api/approver-keys`. The receipt export `/api/receipts.jsonl` needs
the session (download it from the footer link once signed in).

## Accounts

`judge` is the **demo account**: seeded from `JUDGE_PASSWORD` at every start (its hash is
replaced when the variable changes, and the account is disabled when it is empty). Judges
share it, so on this account two-step sign-in stays off and cannot be turned on from the page,
the password cannot be changed from the page, and the account cannot be deleted: no judge can
lock the others out. The profile, the sessions list ("sign out all others" just sends the
others back to `/login`) and the data export work as for any account.

Other accounts register with Google or Facebook (trial, see *Registration with Google or Facebook*) or are created by the operator:

```sh
# on the VM; locally the same commands run as `studio account …`
cd /opt/muse-studio-lite/current
acct() { sudo -u muse env PYTHONDONTWRITEBYTECODE=1 STUDIO_DATA_DIR=/var/lib/muse-studio-lite \
  .venv/bin/python -m studio.cli account "$@"; }
acct create ola --display-name "Ola"
acct list
acct disable ola                        # its sessions stop working at once
acct enable ola
acct reset-password ola [--clear-2fa]   # every session ends; --clear-2fa: lost phone
```

Passwords are read from the terminal (or one line on stdin with `--password-stdin`), never
from the command line. An own account can try everything: `/account` → two-step sign-in
(QR code drawn on the server, ten one-time recovery codes), password change (signs out the
other sessions and revokes their signing keys), sessions, data export (JSON), deletion
(typed user name + password + code; receipts stay, the approver key fingerprint then leads
to a random account id only). The hourly reset does not touch accounts.

The deploy's `db.init` creates the `accounts`, `recovery_codes` and `account_sessions`
tables (idempotent); the first start after it seeds `judge` from `JUDGE_PASSWORD`. Nothing
else changes in existing tables.

| What | Setting |
| --- | --- |
| Model calls | real Muse Model API (`MODEL_API_KEY`), daily cap `STUDIO_DAILY_BUDGET_USD=8` (UTC day, rebuilt from the receipts after a restart) |
| Meta | `META_ADAPTER=mock` by default: campaign writes produce receipts with `adapter: mock`, status PAUSED, no Graph calls. With `STUDIO_SANDBOX_LIVE_META=1` and `META_ADAPTER=graph` the judge account creates real campaigns on the Muse ad account, always PAUSED and within `STUDIO_MAX_DAILY_BUDGET`; it cannot sign `campaign.activate`, `campaign.update`, `reply.post` or `comment.hide` (`demo_live_write`, before the nonce is consumed). Only the owner's own account can turn a campaign on |
| Google Ads | `GOOGLE_ADS_ADAPTER=mock` (default): Search campaign writes produce receipts with `adapter: mock`, status PAUSED, no Google calls; the sandbox never holds a Google credential |
| Kill switch | `STUDIO_READ_ONLY=1`: every write and model call is a 503, pages stay up |
| API down | `STUDIO_REPLAY=1`: recorded responses instead of live calls |
| Health | `GET /healthz` → `{"ok": true, "read_only": false}` (public, minimal; `HEAD` works on every page for uptime monitors) |

## Guards (`studio/sandbox.py`, enabled by `STUDIO_SANDBOX=1`)

- **Signed-in writes.** Every request other than `GET`/`HEAD`/`OPTIONS` (except `/login`,
  `/login/2fa` and `/logout`) needs a valid session (the judge's or an operator-created
  account's), checked before the body is read: 401
  `login_required` otherwise. The routes' own guard (same-origin, session, kill switch) still
  runs behind it.
- **Limits** per client prefix (IPv4 /32, IPv6 /64; the address is the rightmost
  `X-Forwarded-For` entry, trusted only from `TRUSTED_PROXY`): 600 requests/min, 30
  writes/min; per judge session 30 writes/min and 300/h. Over the limit: 429 with
  `Retry-After`. Tables are bounded (LRU). Login has its own limiter (attempt reserved
  before the body is read, short capped global pause). Override with
  `STUDIO_IP_REQUESTS_PER_MIN`, `STUDIO_IP_WRITES_PER_MIN`, `STUDIO_SESSION_WRITES_PER_MIN`,
  `STUDIO_SESSION_WRITES_PER_HOUR`.
- **Body limits.** Caddy and the app both cap bodies: 33 MB voice brief, 7 MB report
  screenshot, 128 KB (Caddy) / 64 KB (app) for everything else; 413 beyond.
- **Hourly reset** (`STUDIO_RESET_INTERVAL_S=3600`). Demo data (briefs, creatives,
  campaigns, comments, leads, tasks, reports, pending approvals) is wiped and re-seeded with
  the synthetic fixtures. Kept: receipts and runs (the chain stays verifiable), consumed
  nonces (no replays), approver keys (old signatures still verify).
  - only while `STUDIO_SANDBOX=1`;
  - the first check on a database without a marker writes the marker only, so a fresh
    deployment or a first visitor never wipes anything;
  - the marker is written as the last statement of the reset transaction: a failed reset
    keeps the old marker and is retried a minute later;
  - a due reset waits while a judge is writing (2 minutes of quiet), at most half an hour;
  - a marker in the future (clock jump) is replaced, not trusted.
- **Login** (`studio/auth.py`, `studio/app.py`, `studio/accounts.py`): argon2id hashes; one
  generic error message; the attempt is counted per client prefix before the body is read
  (5 per 15 min, then 429 with `Retry-After`) plus a short, capped global pause after 20
  failures in 5 minutes; the second step (`/login/2fa`) counts against the same limit, and a
  correct password for a two-step account does not reset it; a new session on every login;
  session cookie `HttpOnly; Secure; SameSite=Strict`, revoked server side on sign-out.
- **CSRF**: state-changing requests with `Sec-Fetch-Site` other than `same-origin`/`none` are
  403; a double-submit token (HttpOnly, SameSite=Strict cookie) must come back as the `csrf`
  field of the login, second-step, sign-out and `/account/*` forms and as `X-CSRF-Token` on
  any other form-typed body
  (urlencoded, multipart, text/plain); the routes' `Origin` check stays. JSON and raw uploads
  need a CORS preflight, which is never granted.
- **Startup refuses** `STUDIO_REQUIRE_LOGIN=1` without `JUDGE_PASSWORD`, and a sandbox without `JUDGE_PASSWORD` + `SESSION_SECRET` (≥ 32 random
  characters, unrelated to the password), with `META_ADAPTER=graph`, or with
  `COOKIE_SECURE=0`.

## How our sandbox runs

A small Debian 13 VM (2 GB RAM), CIS-hardened, outside any other infrastructure: inbound only
80/443 (SSH on a jump host only), outbound only DNS, NTP and 443.

| Piece | Where |
| --- | --- |
| Caddy (official apt repo) | `/etc/caddy/Caddyfile` ← `deploy/Caddyfile`; TLS from Let's Encrypt; HSTS, CSP (identical to the app's), body limits |
| App | systemd `muse-studio-lite.service` ← `deploy/muse-studio-lite.service`; user `muse`; uvicorn on `127.0.0.1:8000`, one worker |
| Code | `/opt/muse-studio-lite/releases/<utc>-<rev>/` (root:muse, read-only to the service) with its own hash-pinned `.venv`; `/opt/muse-studio-lite/current` → live release |
| Data | `/var/lib/muse-studio-lite` (0700 muse): `studio.db`, `ledger.key`, `ledger.pub`, last 3 `backup-*.db` |
| Secrets | `/etc/muse-studio-lite/env` (root:muse 0640), see `deploy/sandbox.env.example` |

The unit runs with `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `PrivateDevices`,
`NoNewPrivileges`, an empty capability set, a `@system-service` syscall filter,
`MemoryMax=768M`, `TasksMax=128`, `Restart=on-failure`; only the data dir is writable.

### Redeploy

```sh
MUSE_HOST=muse MUSE_SSH_OPTS="-p 1022 -i ~/.ssh/<key>" deploy/deploy.sh
```

`deploy.sh` exports the committed `HEAD` (`git archive`), rsyncs it to the VM and runs
`deploy/remote-install.sh` as root there: packages on first run, service user, a new
release directory with a venv from `deploy/requirements.lock` (`--require-hashes`), a SQLite
backup, `db.init` migration as the service user, atomic switch of `current`, unit and
Caddyfile install (`caddy validate` first), restart, health check on `/healthz`, rollback to
the previous release if it does not come up, and pruning to three releases.

Operations on the VM:

```sh
sudo systemctl status muse-studio-lite caddy
sudo journalctl -u muse-studio-lite -f
# kill switch: set STUDIO_READ_ONLY=1 (or STUDIO_REPLAY=1) in /etc/muse-studio-lite/env, then
sudo systemctl restart muse-studio-lite
```

`requirements.lock` is regenerated with
`uv pip compile pyproject.toml --generate-hashes --python-version 3.13 --python-platform x86_64-unknown-linux-gnu --no-header -o deploy/requirements.lock`.

## Run it yourself on a clean VPS (docker compose)

Needs Docker with Compose v2 and a DNS name whose A/AAAA records point at the VPS; ports
80 and 443 open.

```sh
git clone https://github.com/medelin/muse-studio-lite && cd muse-studio-lite
cp deploy/sandbox.env.example sandbox.env && chmod 600 sandbox.env
$EDITOR sandbox.env        # MODEL_API_KEY, JUDGE_PASSWORD, SESSION_SECRET (commands inside)
STUDIO_DOMAIN=studio.example.org docker compose up -d --build
curl -sI https://studio.example.org/healthz
```

`docker-compose.yml` runs the app (read-only root filesystem, no capabilities, data in the
`studio-data` volume, hash-pinned dependencies from `deploy/Dockerfile`) behind Caddy
(`Caddyfile`, same headers and limits as ours), with `STUDIO_SANDBOX=1`,
`META_ADAPTER=mock` and `TRUSTED_PROXY` pinned to Caddy's fixed address on the compose
network. Without a real `MODEL_API_KEY`, set `STUDIO_REPLAY=1` to serve recorded responses.

## Insights snapshots

`studio insights-pull` reads an objective-aware insights snapshot of every campaign the
configured adapter created (read-only on Meta; the mock serves synthetic insights) and
writes one `insights.snapshot` receipt each, plus fresh recommendations. The hourly
`deploy/muse-studio-insights.timer` runs it; it is not enabled by `remote-install.sh`
(enable it with `systemctl enable --now muse-studio-insights.timer`). A recommendation
never executes by itself: a budget change or a pause is a new approval signed on `/report`.
`studio leads-poll` does the same for instant-form leads.


## E-mail notifications

`studio/notify.py` tells the owner by e-mail when something needs them: an approval waiting,
a campaign created (paused), launched or paused, new leads, new recommendations, and the
security events of `/account` (password changed, two-step sign-in on or off, a new sign-in,
export, deletion). It is off unless a transport is configured. Put these in
`/etc/muse-studio-lite/env` (the timers `studio insights-pull` and `studio leads-poll` read
the same file, so they mail too):

| Variable | Meaning |
| --- | --- |
| `STUDIO_MAIL_TRANSPORT` | `jmap` (primary: JMAP, RFC 8620/8621, over HTTPS 443, so it works behind an egress filter that allows only DNS/NTP/443), `smtp` (submission over implicit TLS) or `mock` (mails kept in memory, no connection; tests, demo). Unset: `jmap` if `STUDIO_JMAP_SESSION_URL` is set, else `smtp`/`mock` if `STUDIO_SMTP_HOST` is set, else notifications are off |
| `STUDIO_JMAP_SESSION_URL` | e.g. `https://mail.nume.pl/jmap/session` (https only) |
| `STUDIO_MAIL_USER`, `STUDIO_MAIL_PASSWORD` | HTTP Basic (JMAP) or AUTH login (SMTP) with an app password; both required; never logged, never in `repr`, never in a receipt (`STUDIO_SMTP_USER`/`_PASSWORD` still read as a fallback) |
| `STUDIO_SMTP_HOST`, `STUDIO_SMTP_PORT` | for `smtp` only; port default `465` |
| `STUDIO_MAIL_FROM` | default `Muse Studio Lite <muse@udun.eu>`; for JMAP it must equal the address of one of the mailbox's identities |
| `STUDIO_PUBLIC_URL` | the site's origin, e.g. `https://muse.udun.eu` (https; no path); without it mails carry no link |

### Registration with Google or Facebook

| Variable | Meaning |
|---|---|
| `GOOGLE_LOGIN_CLIENT_ID`, `GOOGLE_LOGIN_CLIENT_SECRET` | an OAuth web client (Google Cloud → Credentials); redirect URI `<STUDIO_PUBLIC_URL>/auth/google/callback`; scopes `openid email profile` |
| `FACEBOOK_LOGIN_CLIENT_ID`, `FACEBOOK_LOGIN_CLIENT_SECRET` | a Meta app with the Facebook Login use case (`email`, `public_profile`); valid OAuth redirect URI `<STUDIO_PUBLIC_URL>/auth/facebook/callback` |

A provider is offered on `/login` when both of its variables are set; set `STUDIO_PUBLIC_URL` too, so the redirect URI never comes from the request's Host header. A new identity gets a **trial** account with no password: on a live adapter it signs exactly what `judge` signs (paused `campaign.create` to the operator's landing host) until `studio account promote <user>`. An e-mail match never links an existing account; a signed-in owner links a provider from the account page. While a Google app is in *Testing* or a Meta app in *Development* mode, only its test users / app roles can sign in.

### Billing (Stripe) and the brand scan

| Variable | Meaning |
|---|---|
| `STRIPE_SECRET_KEY` | a test key (`sk_test_`/`rk_test_`); a live key is refused unless `STUDIO_BILLING_LIVE=1` |
| `STRIPE_WEBHOOK_SECRET` | the `whsec_` secret of the endpoint `<STUDIO_PUBLIC_URL>/webhooks/stripe` (events: `checkout.session.completed`, `customer.subscription.created/updated/deleted`, `invoice.paid`, `invoice.payment_failed`, `invoice.upcoming`) |
| `STRIPE_PRICE_ID` | the monthly price (`price_…`) Checkout sells |
| `STRIPE_PRICE_3M`, `STRIPE_PRICE_6M`, `STRIPE_PRICE_12M` | optional: recurring prices billed every 3, 6, 12 months upfront (`interval=month`, `interval_count` 3/6/12); unset = plan hidden. The "months free" badge is computed from the amounts |
| `STRIPE_PRICE_ONCE` | optional: a one-time price for one month (30 days) of access paid once (Checkout `mode=payment`, invoice created) |
| `STRIPE_SPEND_PRICE_ID` | optional: the metered price of the ad-spend fee (1 minor unit per unit, on a Billing Meter); added to every subscription Checkout |
| `STRIPE_SPEND_METER_EVENT` | the meter's event name (default `muse_ad_spend`) |
| `STUDIO_SPEND_FEE_PCT` | the ad-spend fee in percent (default `5`, `0` = none); the pages state it only when the metered price is set and it is above 0 |
| `STUDIO_PRICE_DISPLAY` | optional: the monthly amount shown when Stripe's price cannot be read (e.g. `£9`) |
| `STUDIO_FAIR_USE_RUNS` | campaign runs a month the plans cover (default 60; shown on /pricing and /terms) |
| `STUDIO_SELLER_NAME`, `STUDIO_SELLER_NUMBER`, `STUDIO_SELLER_ADDRESS` | the seller on /pricing and /terms (default name "Dwarven Stronghold Ltd"; a missing number or address is left out) |
| `STUDIO_BRAND_SCAN` | `1`: before drafting, read the advertiser's website named in the brief (`studio.brand_scan`, at most 4 same-site pages, https and public addresses only) |

All three Stripe variables or none. Stripe issues every invoice (the seller's name, address and "not registered for VAT" footer live in the Stripe invoice settings); the studio stores ids, status and dates, and mails the owner when a subscription starts or ends and when a payment succeeds or fails. With a test key a subscription never lifts a trial account: a test card must not unlock the operator's live ad account.

Before Checkout the owner picks a plan, says whether they buy as a private person or for a company, and ticks the payment consent (and, as a private person, the immediate-start acknowledgement that limits the 14-day right to cancel); a `billing.consent` receipt records the plan, the Terms date and digest. `invoice.upcoming` mails a renewal reminder once per renewal of a 3-, 6- or 12-month plan: set Stripe's "upcoming renewal events" to 7 days, as the Terms promise.

The ad-spend fee: `studio billing report-usage [--day YYYY-MM-DD]` (default yesterday, UTC; `deploy/muse-studio-billing-usage.timer` runs it daily, not enabled by `remote-install.sh`) sums, per account with an active subscription, that day's spend of the campaigns the account signed (from the daily spend each insights snapshot now stores), computes the fee in Stripe's minor units of the subscription's currency and sends one meter event (`identifier` = `account_id:day`, sent once). Spend in another currency is not converted: it is skipped and recorded as `currency_mismatch`, so keep the ad account's currency and the price's currency the same. The fee for the one-off month is not invoiced yet.

JMAP flow: `GET` the session (accountId from `primaryAccounts` for `urn:ietf:params:jmap:submission`), one setup call (`Identity/get` to pick the identity whose e-mail is the from address, `Mailbox/query` role `sent`; cached per process), then one `POST` per mail with `Email/set` (created in Sent, `$seen`) and `EmailSubmission/set` (`onSuccessUpdateEmail` drops `$draft`). No redirects are followed with the credentials; timeout 10 s.

Sending happens on a background thread with a 10 s timeout, after the response; a failure is
logged (exception class and a short transport code only) and receipted, never raised. Leads and
recommendations are at most one mail per account per ten minutes (counts add up). Each
account chooses campaigns and leads on `/account#notifications`; security mails cannot be
turned off; the shared `judge` account never sends mail. A mail body holds a count and a
link, never a lead's name or contact.

## Google Ads (operator machine only)

Real Google Ads writes need `GOOGLE_ADS_ADAPTER=rest` and never run in the sandbox
(`STUDIO_SANDBOX=1` stays on the mock). The adapter uses the Google Ads REST API v23 and an
OAuth refresh token; keep these in `.env` (mode 600), never in the repository.

| Variable | Meaning |
| --- | --- |
| `GOOGLE_ADS_ADAPTER` | `mock` (default) or `rest` |
| `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN` | OAuth client and refresh token; the access token is fetched from `https://oauth2.googleapis.com/token` and kept in memory |
| `GOOGLE_ADS_CUSTOMER_ID` | the account the campaign is created in (ten digits, dashes allowed) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | the manager account, sent as the `login-customer-id` header (optional) |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | optional: the `developer-token` header is sent only when this is set (access can be managed per Google Cloud project) |
| `GOOGLE_ADS_ACCOUNT_CURRENCY` | optional ISO 4217 code replacing the currency read from the customer resource (the mock's currency follows `STUDIO_ACCOUNT_CURRENCY` unless this is set) |

With `rest` the process refuses to start without the client id, secret, refresh token and
customer id. The daily ceiling `STUDIO_MAX_DAILY_BUDGET` applies to the Google budget in
the Google account's currency.
