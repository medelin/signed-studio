#!/usr/bin/env bash
# Signed Studio, PayPal edition: install or update the app on a host, as root, from an
# unpacked release (this repo at <git-rev>). It installs the app only: its own user
# (muse-pay), /opt/muse-pay, /var/lib/muse-pay, /etc/muse-pay/env and the muse-pay unit
# on 127.0.0.1:8100. It never writes a web-server configuration and never touches another
# service: the reverse proxy (Apache, nginx or Caddy) is set up by hand once, from
# deploy/PAY-DEPLOY.md. Not deploy/remote-install.sh (that one replaces Caddy's config).
#
#   sudo deploy/pay-install.sh <git-rev>
#
# Steps: service user and directories, release dir with a hash-pinned venv, DB backup +
# migrate, atomic switch of /opt/muse-pay/current, unit, restart, health check on port
# 8100, rollback to the previous release if the new one does not come up.
set -euo pipefail
umask 027

REV=${1:?usage: pay-install.sh <git-rev>}
[[ $REV =~ ^[0-9a-f]{7,40}$ ]] || { echo "bad revision: $REV" >&2; exit 2; }
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Local secrets and state must never ride along into a release (outside tests/).
found=$(cd "$SRC" && find . \( -path ./.git -o -path ./.venv -o -path ./tests \) -prune -o \
  -type f \( -name .env -o -name sandbox.env -o -name '*.db' -o -name '*.key' \
  -o -name 'receipts*.jsonl' \) -print)
if [[ -n $found ]]; then
  echo "refusing to install: the source directory holds local secrets or state:" >&2
  printf '  %s\n' $found >&2
  exit 1
fi
[[ $(id -u) -eq 0 ]] || { echo "run as root" >&2; exit 2; }
BASE=/opt/muse-pay
SVC=muse-pay
SVC_USER=muse-pay
DATA=/var/lib/muse-pay
ETC=/etc/muse-pay
PORT=8100
KEEP=3

log() { printf '[pay-install] %s\n' "$*"; }

command -v python3 >/dev/null || { echo "python3 (3.12+) is needed" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
  || { echo "python3 3.12+ is needed" >&2; exit 1; }
python3 -c 'import venv' 2>/dev/null || { echo "python3-venv is needed" >&2; exit 1; }
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q . \
    && ! systemctl is-active --quiet "$SVC"; then
  echo "port $PORT is taken by another service; pick another (unit + proxy)" >&2; exit 1
fi

# -- user and directories ----------------------------------------------------------------
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir /nonexistent --no-create-home \
  --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 0755 -o root -g root "$BASE" "$BASE/releases"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC"
install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$DATA"
[[ -f $ETC/env ]] || { echo "missing $ETC/env: copy deploy/pay.env.example and fill it" >&2
                       exit 1; }
if grep -q '^PAYPAL_ENV=' "$ETC/env" && ! grep -Eq '^PAYPAL_ENV=(sandbox)?[[:space:]]*$' "$ETC/env"
then echo "PAYPAL_ENV must be sandbox (the app would refuse to start)" >&2; exit 1; fi
chown root:"$SVC_USER" "$ETC/env"
chmod 0640 "$ETC/env"

# -- release: copy, venv from the hash-pinned lock -----------------------------------------
NAME=$(date -u +%Y%m%dT%H%M%SZ)-$REV
DEST=$BASE/releases/$NAME
log "building $DEST"
cp -r "$SRC" "$DEST.partial"
rm -rf "$DEST.partial/.git" "$DEST.partial/.venv"
BUILD_TMP=$BASE/.build-tmp  # some hosts mount /tmp noexec
install -d -m 0700 "$BUILD_TMP"
python3 -m venv "$DEST.partial/.venv"
TMPDIR=$BUILD_TMP "$DEST.partial/.venv/bin/pip" install --quiet --no-cache-dir \
  --disable-pip-version-check --require-hashes --no-deps -r "$DEST.partial/deploy/requirements.lock"
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

# -- switch, unit, restart (no web-server change) -----------------------------------------
PREV=$(readlink -f "$BASE/current" 2>/dev/null || true)
ln -sfn "$DEST" "$BASE/current.new"
mv -T "$BASE/current.new" "$BASE/current"
install -m 0644 "$DEST/deploy/muse-pay.service" /etc/systemd/system/$SVC.service
systemctl daemon-reload
systemctl enable --quiet "$SVC"
systemctl restart "$SVC"

healthy() {
  for _ in $(seq 1 30); do
    if python3 -I -c "import urllib.request as u; u.urlopen('http://127.0.0.1:$PORT/healthz', timeout=2)" \
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

CUR=$(readlink -f "$BASE/current")
ls -1dt "$BASE"/releases/*/ | sed 's:/$::' | grep -vxF "$CUR" | tail -n +$KEEP \
  | xargs -r rm -rf --
log "ok: $NAME is live on 127.0.0.1:$PORT (the reverse proxy is yours: deploy/PAY-DEPLOY.md)"
