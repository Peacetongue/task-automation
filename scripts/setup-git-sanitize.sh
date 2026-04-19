#!/usr/bin/env bash
# One-shot bootstrap for the sanitize filter used by `git add`.
# Run once after cloning (git filter config lives in .git/config, not the repo).
set -eu

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

git config --local filter.sanitize.clean "$PROJECT_ROOT/scripts/git-sanitize.sh"
git config --local filter.sanitize.smudge cat
git config --local filter.sanitize.required true

chmod +x "$PROJECT_ROOT/scripts/git-sanitize.sh"

echo "Configured filter.sanitize in $(pwd)/.git/config"
echo "Run 'git add --renormalize .' to re-apply to already-tracked files."
