# Historical ST evidence — phase 2

Backfill 2025–2020 using BaoStock daily isST and Tushare namechange
(full per-symbol history, without date filters). The verified phase-1 OHLCV
keys define the historical population, including delisted securities.
No quotes need to be fetched again. No changes to the original price objects.

Private Neon Object Storage:
`a-share-backtest/v1/phase2/historical_st/v1/range=2020-2025/universe=<sha256>/`

- Raw compressed batches: 20 symbols, original namechange records including
  name, effective dates, announcement dates and change reasons; BaoStock
  daily date, isST and tradestatus. Unsupported symbols are recorded explicitly.
- Monthly Parquet: one row per original OHLCV key, including each source's
  nullable flag, resolved nullable flag, provenance, historical name and
  announcement date where unambiguous.
- Both sources agree: both_agree. One usable source: baostock_only or
  namechange_only. Disagreement or contradictory name intervals: conflict,
  NULL. Neither source usable: unknown, NULL.
- Effective dates describe historical status, not necessarily information
  available at a prior signal decision. Use announcement dates separately
  where needed; no claim of vintage completeness. Missing names are not normal.
- Source rows are not published in GitHub or uploaded as public Actions artifacts.
- Raw batches and annual partitions are SHA-256 verified against private
  storage; rerun with identical year range to resume verified batches.
- Annual row counts and unique keys must reconcile to the immutable OHLCV.
  Coverage and conflicts must be reviewed before backtests; strict non-ST
  eligibility requires is_st=0 with an explicit acceptable source policy.
  A successful archive is not a claim that all flags are known.

Workflow: A-Share Historical ST Backfill. First verifies rules and actual
provider access. Probe-only checks access without a full backfill. Check
ST_PROVIDER_PREFLIGHT, ST_RAW_BATCH, ST_YEAR resolution_counts and
ST_ARCHIVE_STATUS all_requested_years_verified_with_explicit_unknowns.
If the runner times out, manually rerun with the same 2025–2020 range.
Do not run competing Tushare jobs simultaneously.

Sources: https://baostock.com/ and https://tushare.pro/document/2
