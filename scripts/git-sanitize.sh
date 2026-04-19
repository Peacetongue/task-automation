#!/usr/bin/env bash
# Clean filter for `git add`: redact company-internal names before they
# land in the index. Runs automatically when .gitattributes marks a file
# with `filter=sanitize` (see .gitattributes). Stdin → stdout.
#
# Working tree stays untouched (the smudge filter is identity) — only
# the staged/committed version is rewritten.
#
# Bootstrap on a fresh clone:
#   bash scripts/setup-git-sanitize.sh
set -eu

# LC_ALL=C — treat input as bytes so binary files (PDF etc.) don't trip
# "illegal byte sequence" in BSD sed.
exec env LC_ALL=C sed \
  -e 's/biocad/company/g' \
  -e 's/Biocad/Company/g' \
  -e 's/BIOCAD/COMPANY/g'
