#!/usr/bin/env bash
# Deploys one commit of Mentor on the notebook (D84, D89). Run by GitHub Actions through the
# deploy key, which is locked to this script (the commit SHA arrives in SSH_ORIGINAL_COMMAND),
# or by hand as `sudo -u deploy /opt/mentor/deploy.sh <sha>` (a rollback, for instance).
#
# pull the images → migrate → up → wait for /health; if that fails, the previous commit goes
# back up and the script fails. Migrations stay compatible with the previous release, so the
# rollback never downgrades the database. Keeps only the images of the current and the previous
# commit.
set -euo pipefail

DIR=${MENTOR_DIR:-/opt/mentor}
HEALTH=${MENTOR_HEALTH:-http://127.0.0.1:8100/health}
IMAGES='^ghcr\.io/us0p/mentor(-worker)?:'

tag="${SSH_ORIGINAL_COMMAND:-${1:-}}"
if [[ ! $tag =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: deploy.sh <full commit sha>" >&2
  exit 2
fi

cd "$DIR"
exec 9>"$DIR/.deploy.lock"
flock -n 9 || { echo "another deploy is running" >&2; exit 1; }

grep -q '^MENTOR_TAG=' .env || echo 'MENTOR_TAG=' >>.env
current=$(sed -n 's/^MENTOR_TAG=//p' .env)

healthy() {
  for _ in $(seq 60); do
    curl -fsS -o /dev/null --max-time 4 "$HEALTH" && return 0
    sleep 2
  done
  return 1
}

# Every step chained with &&: `set -e` doesn't apply inside a function called from `if`.
# `up <tag> migrate` runs the migrations first; a rollback doesn't (the older code doesn't know
# the newer revisions, and they stay compatible with it).
up() {
  echo "==> $1"
  sed -i "s/^MENTOR_TAG=.*/MENTOR_TAG=$1/" .env &&
    docker compose pull --quiet db app worker &&
    if [[ ${2:-} == migrate ]]; then docker compose run --rm migrate; fi &&
    docker compose up -d --remove-orphans db app worker &&
    healthy
}

if up "$tag" migrate; then
  if [[ -n $current && $current != "$tag" ]]; then
    echo "$current" >previous_tag
  fi
  keep=$tag
  [[ -f previous_tag ]] && keep="$keep|$(cat previous_tag)"
  docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E "$IMAGES" |
    grep -vE ":($keep)\$" | xargs -r docker image rm || true
  docker image prune -f >/dev/null
  echo "deployed $tag"
  exit 0
fi

echo "deploy of $tag failed" >&2
docker compose logs --tail 50 app worker >&2 || true
if [[ -n $current && $current != "$tag" ]]; then
  if up "$current"; then
    echo "rolled back to $current" >&2
  else
    echo "ROLLBACK FAILED: the app may be down" >&2
  fi
fi
exit 1
