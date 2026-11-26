# Local on-demand maintenance (1Panel)

Four manually invoked tasks complement the three existing scheduled market, financial and maintenance tasks. No new timers, cloud writes or deployment are installed by this change.

| Worker task | Arguments | Behavior |
|---|---|---|
| history-prices | --days 200 --min-sessions 50 --confirm RUN-LOCAL-MAINTENANCE | Existing compact PostgreSQL backfill, skips sufficiently cached days; official Tushare; recalculates NCAV only on success |
| history-archive | --start-year 2025 --end-year 2020 | Verify every completed annual archive by SHA-256, no provider or DB credentials |
| history-archive | --archive-mode archive --start-year 2025 --end-year 2025 --confirm RUN-LOCAL-MAINTENANCE | Reuse verified years; collect missing completed years only, temporary scratch directory; verify before manifest publication |
| historical-st | --source merged --start-year 2025 --end-year 2020 --confirm RUN-LOCAL-MAINTENANCE | Restore verified BaoStock + namechange batches, merge without provider calls; fail if either source is missing |
| historical-st | --source baostock / namechange / audit (one per invocation) with the same range and confirmation | Provider collection or BaoStock coverage audit; existing batch resume and verification |
| financial-diagnosis | no arguments | Read-only PostgreSQL and archive; no provider credentials |

The default history-archive operation is verification, not collection. Explicit confirmation is required for all ST modes because even offline merge/audit may publish manifests. The historical ST namespace depends on the exact completed-year range and frozen OHLCV universe; use the original 2020–2025 range to reuse migrated evidence. Unknown and conflicting ST values remain explicit. Live provider reachability, memory consumption and full data parity must be verified on an isolated VPS copy before authorizing real collection. Current-year incremental archives remain handled by market; the annual archive task accepts completed years only. Compact backfill retains the existing stock universe refresh and does not rebuild full OHLCV archives.

## 1Panel scripts

Use host Bash and keep these jobs disabled by default (manual Run only). Set timeout appropriate for the range, no automatic retry. Replace the task/arguments in the example with one row above. Do not store credentials in the panel.

```bash
set -euo pipefail
cd /opt/ashare/app
exec /usr/bin/flock -w 7200 /run/lock/ashare-production-pipeline.lock \
  /usr/bin/docker compose -p ashare --env-file ../config/.env \
  -f compose.yaml -f compose.loopback.yaml --profile worker \
  run -T --rm --no-deps worker financial-diagnosis
```

The host lock is shared with existing systemd Worker services. The Worker also holds the archive pipeline lock, rejecting concurrent direct invocations. Do not override entrypoint or mount paths. To test use the isolated test Compose project and test overlay, never production credentials; its guard intentionally rejects provider credentials, so offline diagnosis, annual verification and offline ST merge can be tested there, while real provider checks require separately approved isolated egress arrangements. Never schedule bulk historical imports automatically.

Deployment requires a fresh Minimal Worker image built from this revision after normal CI/security acceptance, explicit publication and digest pinning. The legacy pinned reviewed-pair publication does not automatically publish the new revision. Existing production Web image, database, archive and scheduler are unchanged. Keep cloud rollback workflows disabled until the corresponding on-demand functions are accepted.

Production publication now uses the explicit `[publish-production]` main-push opt-in. Both accepted Web and Minimal Worker images are transferred from the same read-only Docker CI run, verified and published as a pair to the formal private packages. No PR or ordinary push publishes; no deployment is triggered. The former test packages and their archived evidence remain unchanged. See [production image publication](PRODUCTION_IMAGES.md). Do not opt in until merge and publication are separately authorized.
