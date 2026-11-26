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
