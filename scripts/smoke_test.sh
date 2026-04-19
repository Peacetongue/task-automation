#!/usr/bin/env bash
# Smoke test: verifies the task-automation stack is up and the Vikunja API
# round-trip (create + list) works. Idempotent — each run creates a new task
# with an epoch-based suffix, does NOT touch Telegram, does NOT clean up.
#
# Usage:  bash scripts/smoke_test.sh
# Env:    reads .env from the project root (same dir as docker-compose.yml).
#
# Exits 0 on "ALL PASS", 1 otherwise.
set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

ENV_FILE="$PROJECT_ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
else
  echo "FAIL: .env not found at $ENV_FILE — copy .env.example and fill it in"
  exit 1
fi

: "${VIKUNJA_API_TOKEN:?VIKUNJA_API_TOKEN missing in .env}"
: "${VIKUNJA_DEFAULT_PROJECT_ID:=1}"
VIKUNJA_HOST_URL="${VIKUNJA_PUBLIC_URL%/}"   # strip trailing slash
VIKUNJA_HOST_URL="${VIKUNJA_HOST_URL:-http://localhost:3456}"

FAILS=0
EPOCH="$(date +%s)"
TITLE="smoke-test-$EPOCH"

pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; FAILS=$((FAILS+1)); }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "FAIL: prerequisite '$1' is not installed"
    exit 1
  fi
}
need docker
need curl
need jq

# ── 1. compose ps ────────────────────────────────────────────────────────────
echo "[1/6] docker compose ps"
PS_OUT="$(docker compose ps --format json 2>/dev/null || true)"
if [[ -z "$PS_OUT" ]]; then
  fail "docker compose ps returned empty (stack not running?)"
else
  # compose v2 emits one JSON per line OR a JSON array; jq -s handles both.
  BAD="$(printf '%s\n' "$PS_OUT" | jq -rs '
    (if type=="array" then . else [.[]] end)
    | .[]
    | select(
        (.State // .Status // "") | ascii_downcase
        | (contains("running") or contains("healthy")) | not
      )
    | (.Service // .Name) + "=" + (.Status // .State // "unknown")
  ' 2>/dev/null || echo "parse-error")"
  if [[ -z "$BAD" ]]; then
    pass "all services running/healthy"
  else
    fail "not healthy: $BAD"
  fi
fi

# ── 2. whisper-shim /healthz ────────────────────────────────────────────────
echo "[2/6] whisper-shim /healthz"
if curl -fsS --max-time 5 http://localhost:9000/healthz >/dev/null; then
  pass "whisper-shim /healthz = 200"
else
  fail "whisper-shim /healthz unreachable on :9000"
fi

# ── 3. Vikunja /api/v1/info ─────────────────────────────────────────────────
echo "[3/6] vikunja /api/v1/info"
if curl -fsS --max-time 5 "$VIKUNJA_HOST_URL/api/v1/info" >/dev/null; then
  pass "vikunja API reachable ($VIKUNJA_HOST_URL)"
else
  fail "vikunja API unreachable at $VIKUNJA_HOST_URL"
fi

# ── 4. Hermes container is running (HTTP-порт не обязателен — gateway может
#      работать без dashboard, только через Telegram long-polling)
echo "[4/6] hermes container running"
HERMES_STATE="$(docker inspect -f '{{.State.Status}}' task-automation-hermes-1 2>/dev/null || echo missing)"
if [[ "$HERMES_STATE" == "running" ]]; then
  pass "hermes container state=running"
else
  fail "hermes container state=$HERMES_STATE"
fi

# ── 5. Create task via PUT, fallback to POST on 405 ─────────────────────────
echo "[5/6] Vikunja: create task '$TITLE'"
BODY="$(jq -nc --arg t "$TITLE" --arg d "Created by smoke_test.sh at $EPOCH" \
        '{title:$t, description:$d, priority:2}')"
PUT_URL="$VIKUNJA_HOST_URL/api/v1/projects/$VIKUNJA_DEFAULT_PROJECT_ID/tasks"
CREATE_RESP="$(curl -s -w '\n%{http_code}' --max-time 10 \
  -X PUT "$PUT_URL" \
  -H "Authorization: Bearer $VIKUNJA_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$BODY" || true)"
HTTP_CODE="$(printf '%s\n' "$CREATE_RESP" | tail -n1)"
BODY_RESP="$(printf '%s\n' "$CREATE_RESP" | sed '$d')"

if [[ "$HTTP_CODE" == "405" ]]; then
  echo "    PUT got 405, falling back to POST /api/v1/tasks"
  BODY="$(jq -nc --arg t "$TITLE" --arg d "Created by smoke_test.sh at $EPOCH" \
          --argjson pid "$VIKUNJA_DEFAULT_PROJECT_ID" \
          '{title:$t, description:$d, priority:2, project_id:$pid}')"
  CREATE_RESP="$(curl -s -w '\n%{http_code}' --max-time 10 \
    -X POST "$VIKUNJA_HOST_URL/api/v1/tasks" \
    -H "Authorization: Bearer $VIKUNJA_API_TOKEN" \
    -H 'Content-Type: application/json' \
    -d "$BODY" || true)"
  HTTP_CODE="$(printf '%s\n' "$CREATE_RESP" | tail -n1)"
  BODY_RESP="$(printf '%s\n' "$CREATE_RESP" | sed '$d')"
fi

TASK_ID=""
if [[ "$HTTP_CODE" =~ ^2 ]]; then
  TASK_ID="$(printf '%s' "$BODY_RESP" | jq -r '.id // empty' 2>/dev/null || true)"
  if [[ -n "$TASK_ID" ]]; then
    pass "created task id=$TASK_ID (HTTP $HTTP_CODE)"
  else
    fail "2xx but no .id in response: $BODY_RESP"
  fi
else
  fail "create task HTTP $HTTP_CODE: $BODY_RESP"
fi

# ── 6. List tasks and find our title ────────────────────────────────────────
echo "[6/6] Vikunja: list tasks, look for '$TITLE'"
LIST="$(curl -s --max-time 10 \
  "$VIKUNJA_HOST_URL/api/v1/projects/$VIKUNJA_DEFAULT_PROJECT_ID/tasks" \
  -H "Authorization: Bearer $VIKUNJA_API_TOKEN" || true)"
if printf '%s' "$LIST" | jq -e --arg t "$TITLE" 'map(select(.title==$t)) | length > 0' >/dev/null 2>&1; then
  pass "task '$TITLE' visible in project $VIKUNJA_DEFAULT_PROJECT_ID"
else
  fail "task '$TITLE' not found in project $VIKUNJA_DEFAULT_PROJECT_ID listing"
fi

echo
if [[ "$FAILS" -eq 0 ]]; then
  echo "ALL PASS"
  exit 0
else
  echo "FAILED: $FAILS check(s)"
  exit 1
fi
