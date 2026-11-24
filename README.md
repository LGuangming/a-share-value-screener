# 价值雷达 · A-share Net-Net Screener

[中文部署指南](README.zh-CN.md)

A deployable MVP for low-priced A-share Net-Net / distressed-value research. The app uses **Next.js on Vercel**, **PostgreSQL on Supabase**, and **GitHub Actions + Python** for scheduled Tushare data ingestion. The first release is a **research screener**, **not** automated trading software.

## Current features (v0.1)

- Public-market low-price (`close <= ¥10`) and loose PB prefilter (`PB <= 1.2`, or unknown PB, subject to later financial checks).
- Excludes named ST stocks, STAR Market, ChiNext and Beijing Exchange in initial research universe. Not a complete regulatory or risk-warning exclusion mechanism.
- Validates **NCAV = consolidated current assets − total liabilities** and **NCAV / total *market capitalization* >= 1.25**; uses total market value, not free-float capitalization.
- Explicit conversion: Tushare `daily_basic.total_mv` is **10,000 CNY**; statements are **CNY**.
- Stress-test heuristic: NCAV − minority interest − 20% of receivables − 30% of inventory − 50% of prepayments. This is not liquidation valuation. Missing amounts remain unknown, not zero.
- Labels operating cash flow, profitability, leverage, receivables/inventory concentration, stale reports and data gaps.
- Uses actual reported **disclosure dates** (`f_ann_date` then `ann_date`) and a conservative one-calendar-day lag to block same-day after-close look-ahead in historical trading-day screens.
- Research-only MA20 / MA50 observation signals requiring >=50 completed price sessions, with no order routing.
- Three independent GitHub Actions workflows: weekday market ingestion (19:30 Asia/Shanghai), seven-day financial refresh (08:17, opt-in), and Tushare-free NCAV recalculation triggered after successful data updates.
- Read-only Vercel server rendering; no synthetic rankings and no market-provider secrets in browser code.
- Responsive dashboard, per-company analysis, signal list and methodology documentation. **Backtest page is intentionally labeled unimplemented.**

## Architecture

```
Tushare Pro  →  Python GitHub Actions → Supabase PostgreSQL
                                          ↑
Next.js website (Vercel) ← read-only PostgreSQL account
```

## 1. Create and configure Supabase

1. Create a new Supabase project.
2. In SQL Editor, execute [`database/schema.sql`](database/schema.sql) once; alternatively set up Python and run `PYTHONPATH=backend python -m value_screener.cli init-db` with a `DATABASE_URL`.
3. Use Supabase's PostgreSQL connection pooler if your runtime lacks IPv6 support. Add SSL (`sslmode=require`).
4. Use one database credential with write privilege **only** in GitHub Actions (`DATABASE_URL`). Set up a separate database user with `SELECT` only for Vercel (`DATABASE_READ_URL`). The SQL file includes commented GRANT guidance. Do not expose either credential as `NEXT_PUBLIC_*`.
5. Prefer Vercel Deployment Protection if this will be a private research site. No user-account authentication is implemented in v0.1.

> Supabase project pooling, role provisioning and network support depend on your project settings. Configure them in Supabase, not in this codebase.

## 2. Get a Tushare Pro token

Register at <https://tushare.pro> and confirm your account's quota/access for:

- `trade_cal`, `stock_basic`, `daily_basic` for stock universe, trading calendar and close/PB/total market capitalization;
- `balancesheet`, `income`, `cashflow` for point-in-time financial data.

The daily financial-fetch budget defaults to **30 companies** (up to three API calls each). Full A-share financial coverage is **not instantaneous** on ordinary per-ticker APIs: it is progressively backfilled within account limits, and missing companies remain marked as `DATA_GAP`. For full-market production coverage, obtain a provider plan with high-throughput/bulk statement access and implement a vendor-approved bulk-backfill job.

Provider links: [daily_basic](https://tushare.pro/document/2?doc_id=32), [balancesheet](https://tushare.pro/document/2?doc_id=36), [income](https://tushare.pro/document/2?doc_id=33), [cashflow](https://tushare.pro/document/2?doc_id=44).

## 3. Set GitHub repository secrets

In GitHub → Repository → Settings → Secrets and variables → Actions:

- `TUSHARE_TOKEN`: Tushare Pro token
- `DATABASE_URL`: Postgres connection string for ingestion

Market ingestion schedule is in [`.github/workflows/daily-scan.yml`](.github/workflows/daily-scan.yml); financial refresh and NCAV recalculation have their own workflows. Financial refresh runs at 00:17 UTC (08:17 Asia/Shanghai) daily after opt-in. All three workflows have `queue: max` and `cancel-in-progress: false` for sequential execution. NCAV recalculation is published only if the upstream successful database ingest marks `recalc_needed=true`: market inputs changed or lack a published snapshot, or stored financials usable **before** the latest quote date changed. Identical re-fetches and reports disclosed on/after the latest quote date never republish older sessions. The recalc job reads only Neon data, requires no Tushare token, and never uses reports disclosed on or after the market quote date.  Monday–Friday 11:30 UTC is 19:30 Asia/Shanghai year-round. Chinese holidays are checked via Tushare's SSE trading calendar. GitHub cron is **best-effort**, so scheduled runs may be delayed or skipped. Set up external monitoring or manually rerun on missed days. Only a successful complete scan becomes the website's latest published result.

First-time backfill (in local shell / an adequately provisioned runner):

```bash
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
export PYTHONPATH=backend
export TUSHARE_TOKEN='YOUR_TOKEN'
export DATABASE_URL='postgresql://...'

python -m value_screener.cli init-db
python -m value_screener.cli bootstrap-prices --days 160 --min-sessions 50
python -m value_screener.cli fetch-market
python -m value_screener.cli recalculate-ncav
python -m value_screener.cli refresh-financials --budget 40
python -m value_screener.cli recalculate-ncav
```

The dedicated [A-Share Recent Price History Backfill](.github/workflows/recent-price-history.yml) initializes a compact `price_history` table of unadjusted closing prices and `price_history_fetches` completion markers, then backfills the last 160 calendar days and recalculates NCAV. Historical sessions already fully cached in Neon are skipped. This is a one-time workflow with manual retry, not an extra permanent daily schedule. The 20/50-day signals use a deduplicated union of compact history and normal daily prices. Historical `daily_basic.close` is unadjusted and does not constitute a survivorship-bias-free backtest.

For unattended recurring scans, keep the default financial budget modest and monitor `financial_data_gap`, `financial_successes` and `listed_stocks` in `job_runs.metrics`. Access to the financial APIs may require additional Tushare points.

## 4. Deploy to Vercel

1. Create a Vercel project and import this GitHub repository.
2. Select Next.js / root directory `/`.
3. Set **server-side** environment variable `DATABASE_READ_URL` (with a SELECT-only role).
4. Deploy. Vercel does **not** need the Tushare token and performs no bulk ingestion.
5. Enable deployment protection if the content is for personal/internal research.

Local preview (no DB configured → intentional empty state):

```bash
npm install
npm run dev
# http://localhost:3000
```

## 5. Test

```bash
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
npm run typecheck
npm run build
```

The first command requires only Python's standard library. Node dependencies require access to the npm registry. Both test/build are configured in GitHub Actions CI.

## What the daily job does

1. Confirm whether the target China date is a trading session.
2. Download listed-stock metadata and same-day `daily_basic` quotes; verify a minimum quote-coverage threshold and refuse a mostly empty snapshot.
3. Select low-priced, low-PB companies for incremental per-company financial statement fetches, subject to a rate/budget cap; preserve all report periods and disclosure dates.
4. Compute conservative point-in-time Net-Net and stressed NCAV using statements disclosed **before** the price session's date (a one-day lag avoids unknowable same-day publication times). Missing fields are flagged, never coerced to zero.
5. Compute observation signals where historical prices are sufficient.
6. Atomically replace the day's `netnet_v1` results; mark job `success` only **after** publishing. UI reads only the latest completed job.

## Important limitations before trading

- This MVP is an **incomplete-universe, progressive financial-data backfill** until the source has been populated for all qualifying companies. Data coverage is visible in the UI.
- The provider may not provide every balance-sheet line item (such as minority interest or prepayments) for every issuer. Do **not** use stressed values without reconciling those gaps.
- Current financial rules do not yet verify restricted cash, debt maturities, contingent liabilities, related-party balances, audit opinion, litigation or valuation of real-estate inventories. There is no LLM-generated expert opinion in v0.1; assessments are deterministic rule-based text.
- Screens exclude current ST by name only. A production-grade historical risk model should use official current/historical risk-warning and delisting events.
- Backtesting is **not implemented**. Future validation must include delisted securities, point-in-time historic constituent universes, corporate actions/adjusted prices, T+1, suspensions, price limits, spreads/slippage, and signal execution rules. Technical observations are not verified buy/sell recommendations.
- Tushare permission/data-license terms and securities-investment compliance must be reviewed before publicly redistributing vendor data or publishing actionable advice.

## Source and development status

Source code can be deployed once the repository, Supabase and GitHub secrets are configured. No price, balance sheet, or screener result is fabricated in the production UI. For detailed accounting methodology, see `/methodology` in the site and pure rules in `backend/value_screener/rules.py`.

### Development backlog

- [ ] Bulk point-in-time financial ingestion with official announcement data cross-check
- [ ] Balance-sheet quality checks: restricted cash, receivables aging, legal exposure, audit opinions
- [ ] Adjusted-price historical data and survivor-bias-controlled backtests
- [ ] Per-stock buy/sell signal backtesting, position sizing and execution constraints
- [ ] Secure login, user watchlists, strategy parameter presets and notifications
- [ ] Source-licensed data/redistribution review and incident alerting

## Health check / monitoring

Vercel exposes `GET /api/health`: returns HTTP 200 when the latest successful trading-day snapshot is within 5 calendar days, otherwise HTTP 503. Holidays can legitimately cause stale flags; use the SSE calendar for production external monitoring. The health endpoint never returns credentials.

## Creating the separate GitHub repository

If no tool on the connected GitHub account is permitted to create new repositories, the two included upload scripts can do it **on your own computer** using an authenticated GitHub CLI. The repository is intentionally set **private** and named `LGuangming/a-share-value-screener`.

**Windows PowerShell** (run from the extracted project directory):

```powershell
gh auth login
.\publish-to-github.ps1
```

**macOS/Linux**:

```bash
gh auth login
bash publish-to-github.sh
```

The scripts create the local initial commit and execute `gh repo create ... --private --source . --remote origin --push`. They will **not** write into any pre-existing repository. Install [GitHub CLI](https://cli.github.com/) and [Git](https://git-scm.com/) first. If PowerShell execution policy blocks the script, run its commands one by one rather than disabling system security policies globally.

### Optional: accelerate financial data bootstrap with Tushare VIP

If your Tushare plan permits `balancesheet_vip`, `income_vip`, and `cashflow_vip`, use a bulk quarterly import rather than waiting for the 30-company daily budget:

```bash
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260930
PYTHONPATH=backend python -m value_screener.cli bootstrap-financials --period 20260630
PYTHONPATH=backend python -m value_screener.cli recalculate-ncav
```

The importer checks returned coverage and logs shortfalls; some endpoints/permissions may truncate or reject the data. It does **not** silently claim all A shares were retrieved. Only listed tickers with valid consolidated `report_type=1` statements are ingested. Perform additional earlier quarters if needed. The daily task continues with bounded per-company incremental refresh.

Stock detail charts now merge the compact `price_history` cache with `daily_prices` (daily quotes take precedence on duplicate dates) for up to 120 sessions. The backfill covers a total of 160 calendar days by default, skipping already-complete sessions. The backfill grants SELECT on `price_history` to an existing `screener_reader` role; custom read-only roles need an explicit grant.

## Multi-strategy backtest architecture (first implementation)

The live daily screener remains **Net-Net only**. A versioned strategy catalog
(`strategies/catalog.json`) now drives the strategy center (`/strategies`),
parameter draft detail pages, and a multi-strategy backtest laboratory.
Planned strategies are clearly marked and never masquerade as live signals.

The standalone capacity audit checks current Neon storage **read-only**. The
historical 2025 market prototype fetches all completed market sessions into
monthly ZSTD Parquet partitions, verifies counts and SHA-256 and runs an
**engineering-only** 5/10/20-session portfolio kernel smoke test. No historical
market rows are inserted into Neon. Because this repository is public, GitHub publishes **only the manifest** as a 7-day temporary artifact; the full Parquet data remains on the runner unless a private bucket is configured. Long-term private S3-compatible storage
needs explicit `OBJECT_STORE_*` credentials plus `BACKTEST_ARCHIVE_UPLOAD=true`
and should first pass real capacity and compression checks.

Detailed contracts, limitations, verification requirements and staged rollout:
[docs/MULTI_STRATEGY_BACKTEST.md](docs/MULTI_STRATEGY_BACKTEST.md).

### Private Neon Object Storage integration (manual activation)

The Parquet history workflow now has three explicitly manual operations:
`check` (small PUT/GET/SHA-256/DELETE), `archive` (preflight, fetch,
compress, local tests, private upload, remote HEAD+GET checksum, publish
manifest last), and `restore` (download + SHA-256 verify each of 12 months
and re-run the engineering backtest smoke test). The writer forces a
Neon branch-scoped HTTPS endpoint and S3 path-style access. Storage credentials
are server-side GitHub Actions secrets only. Archive/restore cannot be declared
operational until a PRIVATE Neon bucket and scoped credentials are connected
and both live workflows complete. See
[docs/MULTI_STRATEGY_BACKTEST.md](docs/MULTI_STRATEGY_BACKTEST.md).


## Interactive historical market chart (Lightweight Charts)

Stock detail pages use TradingView Lightweight Charts™ for unadjusted historical
prices. The initial view uses the existing Neon compact close history. Selecting
a completed archive year (2020–2025) reads the existing monthly Parquet files
directly, without repartitioning, uploading new objects or making Tushare requests.

The year reader validates the published manifest, byte sizes, SHA-256 hashes,
row counts and OHLC bounds, then returns only the requested ticker. The server
caches successful ticker/year queries for 24 hours; a cold query reads twelve
monthly partitions. Weekly/monthly bars are aggregated in the browser. K-line
and volume controls are available only where the underlying data supplies OHLCV.
Trade marker types are reserved for actual strategy or backtest events.

To enable archived candles in a Vercel environment, configure server-side
environment variables (never prefix these with NEXT_PUBLIC_):
- NEON_STORAGE_ENDPOINT — existing branch-scoped Neon HTTPS storage endpoint
- NEON_STORAGE_BUCKET — existing private archive bucket
- NEON_STORAGE_REGION — optional, derived from endpoint when absent
- NEON_STORAGE_PRIVATE_CONFIRMED — true only after confirming the bucket is private
- NEON_STORAGE_READ_ACCESS_KEY_ID — a separate read-only S3 credential
- NEON_STORAGE_READ_SECRET_ACCESS_KEY — its secret, not the ingestion write key

Use the same already-published archive prefix, a-share-backtest/v1/prices.
Do not copy writer credentials to the frontend. If the read-only variables are
missing or the archive cannot be verified, the historical-year selection reports
unavailability; the existing recent close chart remains independent. No 2026
OHLCV is fabricated from recent close-only history.
