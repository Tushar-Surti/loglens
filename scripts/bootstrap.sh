#!/usr/bin/env bash
# Cold start: build, launch, wait for health, backfill history, train models.
#
#   ./scripts/bootstrap.sh              # 6 hours of history
#   ./scripts/bootstrap.sh 24           # 24 hours (slower)
#
# Safe to re-run: the backfill upserts, so windows are updated, not duplicated.

set -euo pipefail

HOURS="${1:-6}"
COMPOSE="docker compose"
API="http://localhost:8000"

cyan() { printf '\033[36m%s\033[0m\n' "$1"; }
green() { printf '\033[32m%s\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1"; }

cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  red "Docker is not running. Start Docker Desktop and try again."
  exit 1
fi

if [[ ! -f .env ]]; then
  cyan "Creating .env from .env.example"
  cp .env.example .env
fi

cyan "==> Building images (first run pulls Spark and Kafka; expect a few minutes)"
$COMPOSE build

cyan "==> Starting the platform"
$COMPOSE up -d

cyan "==> Waiting for the API"
for attempt in $(seq 1 60); do
  if curl -sf "$API/api/health" >/dev/null 2>&1; then
    green "    API is up"
    break
  fi
  if [[ $attempt -eq 60 ]]; then
    red "    API did not come up in time — check: docker compose logs api"
    exit 1
  fi
  sleep 3
done

cyan "==> Backfilling ${HOURS}h of history (metrics, sessions, incidents)"
BACKFILL_HOURS="$HOURS" $COMPOSE --profile seed run --rm backfill

cyan "==> Training the anomaly models on that history"
$COMPOSE --profile seed run --rm train

cyan "==> Waiting for the streaming pipeline to produce a live window"
for attempt in $(seq 1 40); do
  lag=$(curl -sf "$API/api/system/health" | python -c "import sys,json;print(json.load(sys.stdin).get('pipeline_lag_seconds') or 9999)" 2>/dev/null || echo 9999)
  if python -c "import sys; sys.exit(0 if float('$lag') < 240 else 1)" 2>/dev/null; then
    green "    pipeline is live (lag ${lag}s)"
    break
  fi
  sleep 5
done

echo
green "LogLens is ready."
echo "  Dashboard   http://localhost:3000"
echo "  API docs    http://localhost:8000/docs"
echo "  Grafana     http://localhost:3001   (admin / loglens)"
echo "  Spark UI    http://localhost:9090"
echo
echo "Try it: click Simulate in the top bar, or run"
echo "  curl -X POST $API/api/system/scenarios -H 'Content-Type: application/json' -d '{\"type\":\"ddos_burst\"}'"
