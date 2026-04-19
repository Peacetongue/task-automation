#!/usr/bin/env bash
# One-shot bootstrap для git-фильтра "company↔company".
#
# Что он делает:
#   clean (при `git add`):  company → company — тем, что попадает в индекс/commit
#   smudge (при checkout):  company → company — тем, что ложится в working tree
#
# Результат: в публичном репо на GitHub всегда "company", у разработчика и
# на prod-сервере в рабочем дереве всегда "company". Round-trip прозрачный.
#
# Запуск: один раз после `git clone` (git-фильтры живут в .git/config,
# который не клонится).

set -eu

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

git config --local filter.sanitize.clean  "$PROJECT_ROOT/scripts/git-sanitize.sh"
git config --local filter.sanitize.smudge "$PROJECT_ROOT/scripts/git-unsanitize.sh"
git config --local filter.sanitize.required true

chmod +x "$PROJECT_ROOT/scripts/git-sanitize.sh" \
         "$PROJECT_ROOT/scripts/git-unsanitize.sh"

echo "✓ Filters configured in $(pwd)/.git/config"
echo
echo "Чтобы применить smudge к уже клонированным файлам (вернуть 'company'"
echo "в working tree), сделай ОДНО из:"
echo "  git rm --cached -r . && git reset --hard HEAD"
echo "или"
echo "  git checkout-index -f -a"
