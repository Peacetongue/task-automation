#!/usr/bin/env bash
# Smoke test: проверяет что корп.ассистент поднялся (hermes + whisper-shim +
# metrics-sidecar), metrics-эндпоинт живой, сеть `monitoring` на месте.
# НЕ трогает Telegram, НЕ пытается ходить в Jira/Confluence/GitLab (там нужен
# per-user PAT и корп.сеть).
set -u
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

FAILS=0
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

# ── 1. docker network `monitoring` существует ───────────────────────────────
echo "[1/5] docker network 'monitoring' exists"
if docker network inspect monitoring >/dev/null 2>&1; then
  pass "network 'monitoring' present"
else
  fail "network 'monitoring' missing — create it with:  docker network create monitoring"
fi

# ── 2. compose ps — все сервисы running ─────────────────────────────────────
echo "[2/5] docker compose ps"
PS_OUT="$(docker compose ps --format json 2>/dev/null || true)"
if [[ -z "$PS_OUT" ]]; then
  fail "docker compose ps returned empty (stack not running?)"
else
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

# ── 3. COMPANY Transcribe reachable (direct, no shim) ───────────────────────
echo "[3/5] COMPANY Transcribe reachable from hermes container"
if docker compose exec -T hermes python3 -c "import urllib.request as u; u.urlopen('http://ml-platform-big.company.loc:9204/docs', timeout=5)" 2>/dev/null; then
  pass "transcribe service reachable from hermes"
else
  fail "transcribe service unreachable (VPN? corp net?) — voice will not work"
fi

# ── 4. metrics-sidecar /healthz + /metrics ─────────────────────────────────
echo "[4/5] metrics-sidecar /healthz + /metrics"
if curl -fsS --max-time 5 http://localhost:8000/healthz >/dev/null; then
  pass "metrics-sidecar /healthz = 200"
else
  fail "metrics-sidecar /healthz unreachable on :8000"
fi
METRICS="$(curl -s --max-time 5 http://localhost:8000/metrics || true)"
if echo "$METRICS" | grep -q '^# HELP hermes_'; then
  pass "metrics endpoint exposes hermes_* metrics"
else
  fail "metrics endpoint doesn't have hermes_* families yet"
fi

# ── 5. Hermes container — running ──────────────────────────────────────────
echo "[5/5] hermes container running"
HERMES_STATE="$(docker inspect -f '{{.State.Status}}' task-automation-hermes-1 2>/dev/null || echo missing)"
if [[ "$HERMES_STATE" == "running" ]]; then
  pass "hermes container state=running"
else
  fail "hermes container state=$HERMES_STATE"
fi

echo
if [[ "$FAILS" -eq 0 ]]; then
  echo "ALL PASS"
  exit 0
else
  echo "FAILED: $FAILS check(s)"
  exit 1
fi
