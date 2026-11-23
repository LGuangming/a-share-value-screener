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
- Three independent GitHub Actions workflows: weekday market ingestion (19:30 Asia/Shanghai), seven-day financial refresh (23:00, opt-in), and Tushare-free NCAV recalculation triggered after successful data updates.
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

Market ingestion schedule is in [`.github/workflows/daily-scan.yml`](.github/workflows/daily-scan.yml); financial refresh and NCAV recalculation have their own workflows. The recalc job reads only Neon data, requires no Tushare token, and never uses reports disclosed on or after the market quote date.  Monday–Friday 11:30 UTC is 19:30 Asia/Shanghai year-round. Chinese holidays are checked via Tushare's SSE trading calendar. GitHub cron is **best-effort**, so scheduled runs may be delayed or skipped. Set up external monitoring or manually rerun on missed days. Only a successful complete scan becomes the website's latest published result.

First-time backfill (in local shell / an adequately provisioned runner):

```bash
python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
export PYTHONPATH=backend
export TUSHARE_TOKEN='YOUR_TOKEN'
export DATABASE_URL='postgresql://...'

python -m value_screener.cli init-db
python -m value_screener.cli bootstrap-prices --days 110
python -m value_screener.cli fetch-market
python -m value_screener.cli recalculate-ncav
python -m value_screener.cli refresh-financials --budget 40
python -m value_screener.cli recalculate-ncav
```

The `bootstrap-prices` job may require numerous API requests. Watch provider limits before increasing its run size. Its `daily_basic` history is sufficient for basic MA observation, not for a survivorship-bias-free backtest.

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
