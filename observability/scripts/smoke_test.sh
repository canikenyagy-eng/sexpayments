#!/usr/bin/env bash
# Smoke-tests the observability stack after `docker compose up -d`.
# Exits non-zero if any target is down or a key metric is missing.
#
# Usage:
#   ./scripts/smoke_test.sh
#   PROMETHEUS=http://prometheus.internal:9090 ./scripts/smoke_test.sh

set -euo pipefail

PROMETHEUS="${PROMETHEUS:-http://localhost:9090}"
ALERTMANAGER="${ALERTMANAGER:-http://localhost:9093}"

red()    { printf "\033[31m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }

fail=0

echo "=== Prometheus targets ==="
targets="$(curl -fsS "$PROMETHEUS/api/v1/targets" | jq -r '.data.activeTargets[] | "\(.labels.job)\t\(.health)\t\(.lastError)"')"
echo "$targets"
while IFS=$'\t' read -r job health err; do
  if [ "$health" = "up" ]; then
    green "  OK   $job"
  else
    red "  DOWN $job — $err"
    fail=1
  fi
done <<< "$targets"
echo

echo "=== Key metrics present ==="
check_metric() {
  local name="$1"
  local result
  result="$(curl -fsS "$PROMETHEUS/api/v1/query" --data-urlencode "query=$name" | jq -r '.data.result | length')"
  if [ "$result" -gt 0 ]; then
    green "  OK   $name ($result series)"
  else
    yellow "  MISS $name (no series yet — may need traffic / time)"
  fi
}

check_metric "up"
check_metric "node_load1"
check_metric "container_cpu_usage_seconds_total"
check_metric "pg_up"
check_metric "redis_up"
check_metric "redis_key_size"
check_metric "celery_worker_up"
check_metric "http_requests_total"  # backend — needs Week 2 image
echo

echo "=== Alertmanager ==="
amstatus="$(curl -fsS "$ALERTMANAGER/api/v2/status" | jq -r '.cluster.status // "unknown"')"
if [ "$amstatus" = "ready" ]; then
  green "  Alertmanager status: $amstatus"
else
  red "  Alertmanager status: $amstatus"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  green "All checks passed."
else
  red "Some checks failed. See above."
  exit 1
fi
