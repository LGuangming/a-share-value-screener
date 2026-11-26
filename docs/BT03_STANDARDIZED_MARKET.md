# BT03-A/B — offline standardized market data, initial completed years

This draft adds a standalone Python module, no change to the production Compose,
pipeline, PostgreSQL, schedule, registry or Web. Inputs are existing completed
2020–2025 manifests and verified monthly raw Parquet; all-market rows retained.
Code-based mainboard scope is a provisional classification. Required mainboard
joins and OHLCV validation block publication; other field-level issues are counted.
ST classification is preserved, not a certified historical eligibility decision.
Suspension events are stored separately including events without a bar; no
execution assumptions are derived from event absence. Factor-scaled close is
an intermediate feature, not a cash/dividend-adjusted return series. Raw OHLC
remains untouched. Values and market-cap unit conversion are explicit.

Each output month has content-addressed Parquet files and a manifest written
last. Existing identical input manifests are reused after output hash validation;
changed input requires a new output version. One host lock serializes builds.
Only published manifest files should be consumed, not directory globs.

After CI and separate image approval, create dedicated output and scratch
directories owned by 10002:10010. Use the approved immutable Worker image
with network none, read-only archive bind, independent writable output/scratch,
no environment credential file and 1 GiB/1 CPU limits. Override entrypoint with
/usr/local/bin/python3 and use:
  -m value_screener.backtest_dataset --archive /data/archive --output /out --scratch /scratch --start-year 2025 --end-year 2025 --month 1
Then inspect BT03_PARTITION and the output manifest before running all six years.
Do not deploy or schedule this as part of the live worker.

Not implemented: current-year incremental inputs, point-in-time financial
versions, historical universe validation, exact suspension semantics,
corporate actions and dividend cash accounting, formal backtests, result
publication, and actual VPS resource/coverage acceptance. No strategy
performance may be published from this dataset alone.

## Incremental market stage (2026+)

Use --incremental --start-year 2026 --end-year 2026, optionally --day YYYY-MM-DD.
Only committed day records with matching five source manifests and open calendar evidence
are read; do not consume monthly reader snapshots. Each completed day has its own
immutable output directory. ST columns remain NULL and the manifest explicitly blocks
formal strategy use; no current-name backfill. Historical annual partitions remain unchanged.
This is not a scheduler or a completeness proof against an independent calendar.

## Financial evidence gate

Run value_screener.backtest_financial_evidence with --archive and --output (a new
JSON file outside the archive). This offline audit verifies each frozen-universe
50-code financial batch and counts candidate disclosure rows, missing fields,
missing securities, periods and conflicting same-day versions. It also provides
a conservative latest-report selection function with strict prior-day disclosure
and no silent fallback on latest-version conflicts or missing core data. It does
not certify historical revision completeness, join financial statements to daily
bars, cross-check disclosure calendar, or publish financial factors. Indicator
rows lack actual disclosure and consolidated-report lineage and are not selected.

## BT03-C2/B2/F/D — later stages

Use only a separately accepted Worker image. This standalone Compose file is
not an overlay and does not inherit production DB connections or tokens.
Before running, create all required directories using uid 10002 gid 10010.
Set BACKTEST_IMAGE to the independently reviewed digest and BACKTEST_DIR to
/opt/ashare/backtest, ARCHIVE_DIR to /opt/ashare/archive. Execute from app:
    docker compose -f compose.backtest.yaml run --rm -T --no-deps backtest-worker market --start-year 2025 --end-year 2025 --month 1
    docker compose -f compose.backtest.yaml run --rm -T --no-deps backtest-worker financial-audit --report financial-audit-v1.json
    docker compose -f compose.backtest.yaml run --rm -T --no-deps backtest-worker financial --year 2025 --month 1
    docker compose -f compose.backtest.yaml run --rm -T --no-deps backtest-worker security --year 2025 --month 1
    docker compose -f compose.backtest.yaml run --rm -T --no-deps backtest-worker update --day 2026-11-25
    docker compose -f compose.backtest.yaml run --rm -T --no-deps backtest-worker status --year 2025 --report quality-2025-v1.json

Financial daily joins use publication+one calendar day as inclusive availability,
never scheduled publication dates. Actual publication must match verified calendar
evidence. NCAV is current assets less total liabilities in yuan, market cap
converted to yuan; positive NCAV ratio only. Income/cashflow are YTD not TTM;
no certified ROE. Unresolved dates and conflicts produce nulls, not zeros.
2018/2019 or 2026 disclosure calendars are not present in the observed archive,
so no coverage is implied for those report periods. All results are explicitly
not PIT certified; later source snapshots cannot reconstruct missing revisions.

The security layer records frozen listing dates separately from historical status.
2026+ ST remains unknown unless captured namechange evidence is present.
The optional online capture CLI is intentionally outside offline Compose:
value_screener.backtest_security_evidence capture --archive /data/archive
--evidence /evidence --day YYYY-MM-DD --budget 50 --confirm CAPTURE-NAMECHANGE.
Run only with explicit operator authorization, a separate official token,
read-only archive mount and writable evidence dir. Do not overlap other provider
tasks. This reads the selected committed day's mainboard universe, checkpoints
one code per capture day, and uses the existing rate-limited official client.
A single bounded run is not full coverage; cross-day captures are not silently
merged. Capture-day snapshots are research evidence, not historical certification.
Use security --capture-day YYYY-MM-DD after capture. Changed evidence requires a
new output version/directory rather than overwriting an accepted partition.

Status reads exact expected historical months or committed daily manifests and
checks output hashes, source identities, row counts, keys, and future-disclosure
constraints. It reports incomplete or corrupt partitions; even complete research
partitions do not imply formal backtest readiness. It is not an independent
exchange calendar check. The update command is an explicit single-day task,
not a registered scheduler. Add scheduling only after VPS acceptance.

VPS acceptance remains unexecuted: record /usr/bin/time -v or Docker stats,
free -h and disk delta, single-month first, then repeat to verify resume,
then full 2020–2025 and committed 2026 days. Confirm source archive hashes
unchanged and no production DB/registry/deployment operations. Do not publish
strategy performance until universe, revisions, corporate actions and actual
execution semantics have been certified. 
