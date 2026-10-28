#!/usr/bin/env bash
# Runs on the sandbox VM as root (called by deploy/deploy.sh from the staged release):
# packages (once), service user, release dir with a hash-pinned venv, DB backup + migrate,
# atomic switch of /opt/muse-studio-lite/current, unit + Caddyfile, restart, health check,
# rollback to the previous release if the new one does not come up.
set -euo pipefail
umask 027

REV=${1:?usage: remote-install.sh <git-rev>}
[[ $REV =~ ^[0-9a-f]{7,40}$ ]] || { echo "bad revision: $REV" >&2; exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BASE=/opt/muse-studio-lite
SVC=muse-studio-lite
SVC_USER=muse
DATA=/var/lib/muse-studio-lite
ETC=/etc/muse-studio-lite
KEEP=3

log() { printf '[remote-install] %s\n' "$*"; }

# -- packages (idempotent; HTTPS apt sources only) ---------------------------------------
need=()
for p in python3-venv ffmpeg sqlite3; do dpkg -s "$p" >/dev/null 2>&1 || need+=("$p"); done
if ! dpkg -s caddy >/dev/null 2>&1; then
  if [[ ! -f /etc/apt/sources.list.d/caddy-stable.list ]]; then
    log "adding the Caddy apt repository (Cloudsmith, HTTPS)"
    dpkg -s gpg >/dev/null 2>&1 && dpkg -s curl >/dev/null 2>&1 || {
      apt-get update -q; DEBIAN_FRONTEND=noninteractive apt-get install -y -q gpg curl; }
    curl -fsSL --proto '=https' https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
      | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -fsSL --proto '=https' https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
      > /etc/apt/sources.list.d/caddy-stable.list
    chmod 0644 /etc/apt/sources.list.d/caddy-stable.list
    if grep -q 'http://' /etc/apt/sources.list.d/caddy-stable.list; then
      echo "caddy apt source is not HTTPS" >&2; exit 1
    fi
  fi
  need+=(caddy)
fi
if ((${#need[@]})); then
  log "installing ${need[*]}"
  apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${need[@]}"
fi

# -- user and directories ----------------------------------------------------------------
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --no-create-home \
  --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 0755 -o root -g root "$BASE" "$BASE/releases"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC"
install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$DATA"
[[ -f $ETC/env ]] || { echo "missing $ETC/env (see docs/SANDBOX.md)" >&2; exit 1; }
chown root:"$SVC_USER" "$ETC/env"
chmod 0640 "$ETC/env"

# -- release: copy, venv from the hash-pinned lock -----------------------------------------
NAME=$(date -u +%Y%m%dT%H%M%SZ)-$REV
DEST=$BASE/releases/$NAME
log "building $DEST"
cp -r "$SRC" "$DEST.partial"
BUILD_TMP=$BASE/.build-tmp  # /tmp is noexec on this host
install -d -m 0700 "$BUILD_TMP"
python3 -m venv "$DEST.partial/.venv"
TMPDIR=$BUILD_TMP "$DEST.partial/.venv/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check \
  --require-hashes --no-deps -r "$DEST.partial/deploy/requirements.lock"
rm -rf "$BUILD_TMP"
chown -R root:"$SVC_USER" "$DEST.partial"
chmod -R u=rwX,g=rX,o= "$DEST.partial"
mv -T "$DEST.partial" "$DEST"

# -- DB backup + migrate (as the service user, before the switch) --------------------------
as_svc() { (cd "$DEST" && runuser -u "$SVC_USER" -- env -i PATH=/usr/bin:/bin \
  STUDIO_DATA_DIR="$DATA" PYTHONDONTWRITEBYTECODE=1 "$DEST/.venv/bin/python" -I -c "$1"); }
if [[ -f $DATA/studio.db ]]; then
  log "backing up the database"
  as_svc "
import sqlite3, time
src = sqlite3.connect('$DATA/studio.db')
dst = sqlite3.connect('$DATA/backup-%s.db' % time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
src.backup(dst); dst.close(); src.close()"
  ls -1t "$DATA"/backup-*.db 2>/dev/null | tail -n +4 | xargs -r rm -f --
fi
log "migrating"
as_svc "
import sys; sys.path.insert(0, '$DEST')
from pathlib import Path
from studio import db
c = db.connect(Path('$DATA') / 'studio.db'); db.init(c); c.close()"

# -- switch, units, restart ----------------------------------------------------------------
PREV=$(readlink -f "$BASE/current" 2>/dev/null || true)
ln -sfn "$DEST" "$BASE/current.new"
mv -T "$BASE/current.new" "$BASE/current"
install -m 0644 "$DEST/deploy/muse-studio-lite.service" /etc/systemd/system/$SVC.service
caddy validate --config "$DEST/deploy/Caddyfile" --adapter caddyfile >/dev/null 2>&1 \
  || { caddy validate --config "$DEST/deploy/Caddyfile" --adapter caddyfile; exit 1; }
install -m 0644 "$DEST/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --quiet "$SVC" caddy
systemctl restart "$SVC"

healthy() {
  for _ in $(seq 1 30); do
    if python3 -I -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)" \
        2>/dev/null; then return 0; fi
    sleep 1
  done
  return 1
}
if ! healthy; then
  echo "new release is not healthy" >&2
  journalctl -u "$SVC" -n 30 --no-pager >&2 || true
  if [[ -n $PREV && -d $PREV && $PREV != "$DEST" ]]; then
    echo "rolling back to $PREV" >&2
    ln -sfn "$PREV" "$BASE/current.new"; mv -T "$BASE/current.new" "$BASE/current"
    systemctl restart "$SVC"
  fi
  exit 1
fi
if systemctl is-active --quiet caddy; then systemctl reload caddy; else systemctl start caddy; fi

# -- prune old releases (never the current one) --------------------------------------------
CUR=$(readlink -f "$BASE/current")
ls -1dt "$BASE"/releases/*/ | sed 's:/$::' | grep -vxF "$CUR" | tail -n +$KEEP \
  | xargs -r rm -rf --
log "ok: $NAME is live"
