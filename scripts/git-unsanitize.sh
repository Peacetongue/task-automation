#!/usr/bin/env bash
# Smudge filter for `git checkout`: restores company-internal names in the
# working tree (reverse of scripts/git-sanitize.sh). See .gitattributes +
# scripts/setup-git-sanitize.sh for wiring.
#
# Stdin → stdout. LC_ALL=C to stay binary-safe on arbitrary file contents.
set -eu
exec env LC_ALL=C sed \
  -e 's/company/company/g' \
  -e 's/Company/Company/g' \
  -e 's/COMPANY/COMPANY/g'
