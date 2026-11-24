# Historical ST evidence — phase 2 (independent sources)

Backfill 2025–2020 against immutable verified phase-1 OHLCV keys,
including delisted securities. No new OHLCV requests.

Three independent workflows:
- **A-Share Historical ST - BaoStock**: daily isST and tradestatus only.
  Independent concurrency group; no Tushare token or API calls. Bounded
  login retries and provider probe before the full collection.
- **A-Share Historical ST - Tushare namechange**: manual only. Shares
  a-share-backtest-history concurrency group with other Tushare jobs.
  Start after other Tushare tasks finish to avoid replacing queued runs.
- **A-Share Historical ST - Offline Merge**: manual only after both
  source archives finish. Reads verified raw batches, no provider calls.

Private Neon Object Storage:
`a-share-backtest/v1/phase2/historical_st/v2/range=2020-2025/universe=<sha256>/{baostock,namechange,merged}/`

Each source has its own immutable batch checkpoints (20 symbols),
monthly Parquet and annual/range manifests. Raw records are never uploaded
as public artifacts. Reuse the identical year range for resume. Verify
SHA-256 on restore. Each output row must match an original OHLCV key;
counts and unique keys must reconcile. Missing values are NULL, never 0.
Unsupported BaoStock symbols are explicit. Source-only data is preliminary.

Merged fields preserve both nullable flags, historical name and announcement
date. Both agree: both_agree; one usable source: baostock_only or
namechange_only; disagreement or contradictory name intervals: conflict,
NULL; neither usable: unknown, NULL. Do not use present-day names for
historical status. Effective and announcement dates differ; no guarantee
of historical database vintage completeness.

Acceptance: ST_PROVIDER_PREFLIGHT, ST_RAW_BATCH, ST_YEAR, and
ST_ARCHIVE_STATUS with source and resolution_counts. A completed archive
does not imply all flags are known. Review unknown/conflict rates and
source coverage before backtests.

Sources: https://baostock.com/ and https://tushare.pro/document/2
