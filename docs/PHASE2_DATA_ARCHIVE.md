# Phase 2: A-share historical research data enrichment

Status on 2026-11-23: market job started (run 45190745160);
disclosure + per-issuer finance job queued (run 45192040371).
These are *not* yet data-completeness successes. Inspect each GitHub
Actions job for terminal success and required markers.

## Phase 1 — immutable baseline

2020-2025 raw daily OHLCV has **7,095,173 rows**, 1,455 annual trading
sessions summed across six years, 72 Parquet monthly partitions and
130,304,336 compressed bytes. GitHub Actions run 45179417458 confirmed
each annual remote readback, SHA-256 and full-year uniqueness:
`ARCHIVE_BATCH_STATUS state=all_years_verified`.

## Phase 2 — datasets and access

Bounded live access probe:
[run 45189978535](https://github.com/LGuangming/a-share-value-screener/actions/runs/45189978535)

| API | Access | Archive route | Main caveat |
| --- | --- | --- | --- |
| adj_factor | available | monthly Parquet | Factor reference-date policy needed for backtest |
| stk_limit | available | monthly Parquet | IPO/no-limit days must not be invented |
| daily_basic | available | monthly Parquet | `total_mv` is in ten-thousand CNY, not CNY |
| suspend_d | available | monthly Parquet | No rows on a day can be legitimate |
| stock_basic L,D | available | frozen current snapshot | Not a historical name/ST event log |
| disclosure_date | available | quarter gzip JSONL | Missing actual dates; no full correction-event stream |
| balancesheet / income / cashflow / fina_indicator | available per stock | 50-code gzip JSONL shards | Vintage revisions may be incomplete |
| stock_st | rejected | **NOT** archived | Separate permission or licensed historical source required |
| balancesheet_vip/income_vip/cashflow_vip | rejected | Not used | Per-symbol fallback used instead |

Tushare source: https://tushare.pro/document/2

## Data contracts and storage

Storage: private Neon Object Storage, prefix `a-share-backtest/v1/`.
Never put source market quotes, issuer reports or S3 credentials into
a public GitHub repository.

Phase-2 market:
```
a-share-backtest/v1/phase2/
  universe/stock_basic_snapshot.json
  adj_factor/year=2025/month=01.parquet
  adj_factor/year=2025/month=01.manifest.json
  adj_factor/year=2025/manifest.json
  stk_limit/year=2025/...
  daily_basic/year=2025/...
  suspend_d/year=2025/...
```
Each monthly Parquet is verified by remote GET/SHA-256 and then
its month manifest is published. Year manifest is published only after all
12 months are verified; published years and months are verified and skipped
instead of refetched. Actual coverage depends on successful workflow completion.

Disclosure and financial source evidence:
```
a-share-backtest/v1/phase2/disclosures/period=20250331.jsonl.gz
a-share-backtest/v1/phase2/disclosures/period=20250331.manifest.json
a-share-backtest/v1/phase2/financial_snapshot/
  balancesheet/start=00000_count=050.jsonl.gz
  balancesheet/start=00000_count=050.manifest.json
  income/...
  cashflow/...
  fina_indicator/...
```
Financial snapshots are full raw historical API results **captured now**,
not a guaranteed time-machine of past database revisions. `f_ann_date`,
`ann_date`, `end_date`, `report_type` and `comp_type` must be retained.
Financial amounts are CNY yuan; daily_basic total_mv is CNY 10,000 units.
Do not aggregate or compare mismatched units.

## Acceptance gates

- `PHASE2_MARKET_BATCH_STATUS all_requested_years_verified` plus remote SHA
  for every year and dataset requested.
- `PHASE2_DISCLOSURE_BATCH_STATUS verified_periods=24` for 2020-2025.
- `PHASE2_FINANCIAL_RUN_STATUS` for all four shards, with all required
  50-code shard manifests. Source rows missing an actual disclosure date must
  remain flagged; not PIT-certified by default.
- `stock_st` is blocked as of this probe. DO NOT infer historical ST from
  *current* company names. Do not publish strategy return series as formal
  evidence while historical restrictions and correction-date lineage are missing.
- NCAV: `total_cur_assets - total_liab` versus historical total market value.
  When combining financial and market datasets:
  `total_mv (万元) * 10000 < NCAV (元)`.
  Use only financial versions already disclosed **before** the signal decision.
- Include delisted stocks to avoid survivorship bias. Today's frozen stock
  universe is not sufficient to certify past names and status changes.
- The 2019 warm-up price history, dividend/return accounting, historic ST and
  provider historical corrections remain distinct follow-up acceptance items.

## Manual rerun

Use Actions > **A-Share Phase 2 Market Enrichment** to rerun a failed market
part. Published verified months will be reused. Use
**A-Share Phase 2 Financial and Disclosure Enrichment** to retry failed
shards only when the prior run is terminal; do not start parallel jobs that
share one Tushare token. Every resumable archival unit has a stable SHA-256.

Market run: https://github.com/LGuangming/a-share-value-screener/actions/runs/45190745160

Financial run: https://github.com/LGuangming/a-share-value-screener/actions/runs/45192040371
