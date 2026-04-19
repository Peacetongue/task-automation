#!/usr/bin/env bash
# Пересобрать TELEGRAM_ALLOWED_USERS в .env из config/team.yaml.
# Запускать после любой правки roster'а. Затем:
#   docker compose restart hermes
set -eu

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

TEAM_YAML="$PROJECT_ROOT/config/team.yaml"
ENV_FILE="$PROJECT_ROOT/.env"

if [[ ! -f "$TEAM_YAML" ]]; then
  echo "FAIL: $TEAM_YAML not found. cp config/team.yaml.example config/team.yaml and fill it in."
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "FAIL: .env not found. cp .env.example .env first."
  exit 1
fi

# Выдернуть все telegram_id (line-based, без зависимостей от PyYAML —
# macOS-ный системный python3 его не содержит, а ставить pip ради одной
# операции — излишне). Полагаемся на плоский формат team.yaml:
# `  - telegram_id: 602736458`
IDS="$(sed -n 's/^[[:space:]]*-*[[:space:]]*telegram_id:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$TEAM_YAML" | paste -sd ',' -)"

if [[ -z "$IDS" ]]; then
  echo "FAIL: no telegram_id entries found in $TEAM_YAML"
  exit 1
fi

# Переписать строку TELEGRAM_ALLOWED_USERS= через temp-файл (портативный sed).
TMP="$(mktemp)"
awk -v new="TA_TELEGRAM_ALLOWED_USERS=$IDS" '
  BEGIN { replaced = 0 }
  /^TA_TELEGRAM_ALLOWED_USERS=/ { print new; replaced = 1; next }
  { print }
  END { if (!replaced) print new }
' "$ENV_FILE" > "$TMP"
mv "$TMP" "$ENV_FILE"

echo "✓ TA_TELEGRAM_ALLOWED_USERS синхронизирован: $IDS"
echo "  Для применения: docker compose restart hermes"
