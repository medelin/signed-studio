# Deploying Signed Studio (PayPal edition)

The app is one Python process (uvicorn) on **127.0.0.1:8100**, behind a reverse proxy that
serves its own subdomain over HTTPS (here `pay.example.org`). Everything it owns has its
own name, so it can share a host with another service without touching it:

| What | Value |
|---|---|
| systemd unit | `muse-pay` (`deploy/muse-pay.service`) |
| service user | `muse-pay` (system user, no shell, no home) |
| code | `/opt/muse-pay/releases/<time>-<rev>`, `/opt/muse-pay/current` → the live one |
| data (SQLite, ledger key, assets) | `/var/lib/muse-pay` (0700) |
| secrets | `/etc/muse-pay/env` (root:muse-pay 0640), from `deploy/pay.env.example` |
| port | `127.0.0.1:8100` only; never on a public interface |

`deploy/pay-install.sh` installs and updates **the app only**. It never writes a
web-server configuration, never reloads a proxy and never touches another unit. Do **not**
use `deploy/remote-install.sh` for this edition: it installs the base project's whole
`Caddyfile` as `/etc/caddy/Caddyfile` and health-checks port 8000, so on a shared host it
would replace the other site's proxy configuration.

Two targets are described: **A** an Apache 2.4 shared host where you have root (or an
admin who adds one vhost), **B** a fresh small VM. The app side is the same for both.

## 0. Before either

- A DNS name for the subdomain (an `A`/`AAAA` record to the host).
- Python 3.12+ with `venv` on the host (`python3 --version`; Debian/Ubuntu:
  `apt install python3-venv`). `ffmpeg` only if you enable video ads.
- The PayPal **sandbox** REST app's client id and secret (developer.paypal.com → Apps &
  Credentials → Sandbox), with **Vault ("Save payment methods")** enabled for Day-3
  agentic payments. Never a live app: `PAYPAL_ENV` other than `sandbox` stops the app.
- A free port: `ss -ltn | grep 8100` must be empty. If 8100 is taken, change it in
  `deploy/muse-pay.service`, the proxy file and the installer's `PORT`.

## 1. The app (both targets)

On your machine:

```bash
git archive --format=tar.gz --prefix=muse-pay/ -o /tmp/muse-pay.tgz HEAD
scp /tmp/muse-pay.tgz host:/tmp/
```

On the host, as root:

```bash
install -d -m 0750 /etc/muse-pay
tar -xzf /tmp/muse-pay.tgz -C /root/
cp /root/muse-pay/deploy/pay.env.example /etc/muse-pay/env
chmod 0600 /etc/muse-pay/env
$EDITOR /etc/muse-pay/env      # MODEL_API_KEY, JUDGE_PASSWORD, SESSION_SECRET,
                               # STUDIO_PUBLIC_URL=https://pay.example.org,
                               # PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET (sandbox)
/root/muse-pay/deploy/pay-install.sh "$(git -C <your clone> rev-parse --short HEAD)"
```

The installer creates the user and directories, builds a venv from the hash-pinned
`deploy/requirements.lock`, backs up and migrates the database, switches
`/opt/muse-pay/current` atomically, installs and restarts `muse-pay`, and checks
`http://127.0.0.1:8100/healthz`. If the new release does not come up it rolls back to the
previous one. Check it yourself:

```bash
systemctl status muse-pay --no-pager
curl -s http://127.0.0.1:8100/healthz
```

`TRUSTED_PROXY=127.0.0.1` in the env file means the app believes `X-Forwarded-For` only
from a proxy on the same host. `COOKIE_SECURE=1` needs HTTPS in front.

## 2A. Apache on a shared host

Enable the modules once (`a2enmod proxy proxy_http headers rewrite ssl`), then add one
virtual host from `deploy/apache/signed-studio-pay.conf`:

```bash
cp /root/muse-pay/deploy/apache/signed-studio-pay.conf /etc/apache2/sites-available/
sed -i 's/pay.example.org/pay.YOUR-DOMAIN/g' /etc/apache2/sites-available/signed-studio-pay.conf
certbot certonly --webroot -w /var/www/html -d pay.YOUR-DOMAIN   # or certbot --apache
a2ensite signed-studio-pay
apachectl configtest && systemctl reload apache2
```

`apachectl configtest` checks the whole Apache configuration before the reload, so a
typo cannot take the other sites down. The vhost proxies everything to 127.0.0.1:8100,
keeps the `Host` header and sets `X-Forwarded-Proto: https`. On a host without root
(cPanel and similar), this edition cannot run: it needs a long-running process and a
systemd unit. Use target B instead.

## 2B. A fresh small VM

1 vCPU, 1 GB RAM and 10 GB disk are enough (SQLite, one worker). Debian 12 or Ubuntu
24.04:

```bash
apt update && apt install -y python3-venv nginx certbot python3-certbot-nginx sqlite3
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable
cp /root/muse-pay/deploy/nginx/signed-studio-pay.conf /etc/nginx/sites-available/
sed -i 's/pay.example.org/pay.YOUR-DOMAIN/g' /etc/nginx/sites-available/signed-studio-pay.conf
ln -s ../sites-available/signed-studio-pay.conf /etc/nginx/sites-enabled/
certbot certonly --nginx -d pay.YOUR-DOMAIN
nginx -t && systemctl reload nginx
```

Caddy works as well; the whole site is two lines in its own file (import it from the main
Caddyfile rather than replacing that file):

```
pay.YOUR-DOMAIN {
    reverse_proxy 127.0.0.1:8100
}
```

Run `caddy validate --config /etc/caddy/Caddyfile` before `systemctl reload caddy`.

## 3. After the proxy is up

```bash
curl -sI https://pay.YOUR-DOMAIN/healthz          # 200 over HTTPS
curl -s https://pay.YOUR-DOMAIN/api/ledger-key    # the ledger's public key
```

- **PayPal webhook** (not done yet, Day 4): register `https://pay.YOUR-DOMAIN/webhooks/paypal`
  with PayPal (`POST /v1/notifications/webhooks`, sandbox), put the returned id in
  `PAYPAL_WEBHOOK_ID`, `systemctl restart muse-pay`. The webhook path needs no session; the
  proxy must pass the body unchanged (both files above do).
- **Judge account**: `JUDGE_PASSWORD` in the env file is the `judge` login.
- **Saved PayPal (Vault)**: PayPal returns the buyer to `STUDIO_PUBLIC_URL` +
  `/account/paypal/vault/return`, so `STUDIO_PUBLIC_URL` must be the public HTTPS address.

## Updates and rollback

Re-run the installer from a newer unpacked release. It keeps three releases;
`ls -l /opt/muse-pay/current` shows the live one. To roll back by hand:

```bash
ln -sfn /opt/muse-pay/releases/<previous> /opt/muse-pay/current.new
mv -T /opt/muse-pay/current.new /opt/muse-pay/current
systemctl restart muse-pay
```

Database backups (`/var/lib/muse-pay/backup-*.db`, the last three) are taken before every
migration.

## Removing it

```bash
systemctl disable --now muse-pay
rm /etc/systemd/system/muse-pay.service && systemctl daemon-reload
a2dissite signed-studio-pay && systemctl reload apache2     # or remove the nginx/Caddy site
rm -rf /opt/muse-pay /etc/muse-pay /var/lib/muse-pay && userdel muse-pay
```
