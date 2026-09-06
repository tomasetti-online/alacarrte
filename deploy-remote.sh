#!/usr/bin/env bash
# alacarrte remote deploy - runs on macpro.
# Usage: deploy-remote.sh <expected-short-sha>
# Pulls the repo clone to that commit, builds the image, recreates the
# container via the live compose, verifies env token count + LAN health.
set -u
REPO=/srv/docker/alacarrte-repo
LIVE=/srv/docker/alacarrte
EXPECTED=${1:-}
[ -n "$EXPECTED" ] || { echo NO_SHA_ARG; exit 1; }

cd "$REPO" || { echo NO_REPO; exit 1; }
git fetch origin 2>&1 | head -2
REMOTE=$(git rev-parse --short origin/main)
[ "$REMOTE" = "$EXPECTED" ] || { echo SHA_MISMATCH remote=$REMOTE expected=$EXPECTED; exit 1; }
git reset --hard origin/main >/dev/null
echo "BUILDING $REMOTE"

docker build -q -t alacarrte:latest "$REPO" || { echo BUILD_FAIL; exit 1; }
echo BUILD_OK

cd "$LIVE" || { echo NO_LIVE_COMPOSE; exit 1; }
docker compose up -d 2>&1 | tail -2

sleep 5
echo "== container =="
docker ps --filter name=alacarrte --format '{{.Status}}'
echo "== env token lines (must be 1) =="
docker inspect alacarrte | grep -c 'ALACARTTE_GOTIFY_TOKEN=gtfya'
echo "== lan health =="
curl -s -o /dev/null -w 'health=%{http_code}\n' http://localhost:3080/api/health
echo DEPLOY_DONE
