#!/usr/bin/env bash
# Requires git and authenticated GitHub CLI. Creates a new private repository.
set -euo pipefail
cd "$(dirname "$0")"
REPO="LGuangming/a-share-value-screener"
command -v git >/dev/null || { echo 'Install Git first' >&2; exit 1; }
command -v gh >/dev/null || { echo 'Install GitHub CLI: https://cli.github.com/' >&2; exit 1; }
gh auth status
if [ ! -d .git ]; then git init -b main; fi
git add -A
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  git commit -m "feat: initialize A-share net-net screening MVP"
fi
gh repo create "$REPO" --private --source . --remote origin --push
printf 'Created and pushed: https://github.com/%s\n' "$REPO"
