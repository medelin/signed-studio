# Judge sandbox

Live at **https://muse.udun.eu**. The whole app is behind the judge login at `/login`:
user `judge`, password in the submission form (`STUDIO_REQUIRE_LOGIN=1`, the default with
`STUDIO_SANDBOX=1`). Without a session, pages redirect to `/login?next=…` (a same-site path
only, no open redirect) and the API answers 401 JSON. Public and read-only without a session:
`/healthz`, static assets, and what `studio verify --server` fetches — `/api/ledger-key`,
`/api/ledger-manifest`, `/api/approver-keys`. The receipt export `/api/receipts.jsonl` needs
the session (download it from the footer link once signed in).

| What | Setting |
| --- | --- |
| Model calls | real Muse Model API (`MODEL_API_KEY`), daily cap `STUDIO_DAILY_BUDGET_USD=8` (UTC day, rebuilt from the receipts after a restart) |
| Meta | `META_ADAPTER=mock`: campaign writes produce receipts with `adapter: mock`, status PAUSED, no Graph calls; the sandbox never holds a Meta token |
| Kill switch | `STUDIO_READ_ONLY=1`: every write and model call is a 503, pages stay up |
| API down | `STUDIO_REPLAY=1`: recorded responses instead of live calls |
| Health | `GET /healthz` → `{"ok": true, "read_only": false}` (public, minimal; `HEAD` works on every page for uptime monitors) |

## Guards (`studio/sandbox.py`, enabled by `STUDIO_SANDBOX=1`)

- **Judge-only writes.** Every request other than `GET`/`HEAD`/`OPTIONS` (except `/login` and
  `/logout`) needs a valid judge session, checked before the body is read: 401
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
- **Login** (`studio/auth.py`, `studio/app.py`): one generic error message; the attempt is
  counted per client prefix before the body is read (5 per 15 min, then 429 with
  `Retry-After`) plus a short, capped global pause after 20 failures in 5 minutes; session
  cookie `HttpOnly; Secure; SameSite=Strict`, revoked server side on sign-out.
- **CSRF**: state-changing requests with `Sec-Fetch-Site` other than `same-origin`/`none` are
  403; a double-submit token (HttpOnly, SameSite=Strict cookie) must come back as the `csrf`
  field of the login and sign-out forms and as `X-CSRF-Token` on any other form-typed body
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
