#!/usr/bin/env bash
# Deploy git HEAD to the judge sandbox VM: export HEAD -> rsync -> remote-install.sh
# (hash-pinned venv, DB backup + migrate, atomic switch, restart, health check, rollback).
#
#   MUSE_HOST=muse MUSE_SSH_OPTS="-p 1022 -i ~/.ssh/key" deploy/deploy.sh
#
# Only committed code is deployed; the VM's /etc/muse-studio-lite/env must already exist.
set -euo pipefail

HOST=${MUSE_HOST:-muse}
SSH="ssh ${MUSE_SSH_OPTS:-}"
STAGE_REMOTE=.cache/muse-release

cd "$(git rev-parse --show-toplevel)"
if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
  echo "note: uncommitted changes are not deployed (HEAD only)" >&2
fi
REV=$(git rev-parse --short=12 HEAD)
STAGE=$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/muse-deploy.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
git archive --format=tar HEAD | tar -x -C "$STAGE"

echo "deploying $REV to $HOST"
$SSH "$HOST" "mkdir -p $STAGE_REMOTE"
rsync -rlt --delete --chmod=Du=rwx,Dg=rx,Do=,Fu=rw,Fg=r,Fo= -e "$SSH" "$STAGE/" "$HOST:$STAGE_REMOTE/"
$SSH "$HOST" "sudo -n bash $STAGE_REMOTE/deploy/remote-install.sh $REV"
