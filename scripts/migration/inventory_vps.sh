#!/usr/bin/env bash
# Run locally by the VPS administrator; no sudo, config edits or arbitrary args.
set -euo pipefail
[[ $# == 0 ]] || exit 64
export LC_ALL=C
printf '=== CPU / memory ===\n'
nproc
free -m
printf '=== filesystem ===\n'
df -hP / /opt/ashare
printf '=== approved paths ===\n'
for dir in /opt/ashare/archive /opt/ashare/test/archive /opt/ashare/migration /opt/ashare/test/tmp/duckdb; do
  if [[ -e "$dir" ]]; then
    [[ ! -L "$dir" ]] || { echo "UNSAFE SYMLINK $dir"; exit 1; }
    stat -c '%n uid=%u gid=%g mode=%a' -- "$dir"
    du -sx --block-size=1 -- "$dir" || exit 1
  else
    printf 'MISSING %s\n' "$dir"
  fi
done
printf 'Database detail requires a dedicated local read-only role; not inferred from container health.\n'
