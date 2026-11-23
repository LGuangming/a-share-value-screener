-- A-share value screener v0.1. PostgreSQL schema for Neon or Supabase.
-- Source financials are in CNY yuan; Tushare daily_basic.total_mv is in 万元.
-- On Supabase, revoke client-side anon/authenticated access if those roles exist.
-- On Neon, these Supabase-specific roles usually do not exist.

create table if not exists stocks (
  ts_code text primary key,
  name text not null,
  industry text,
  market text,
  list_date date,
  is_st boolean not null default false,
  updated_at timestamptz not null default now()
);

create table if not exists daily_prices (
  ts_code text not null references stocks(ts_code),
  trade_date date not null,
  close numeric(16,4) not null check(close > 0),
  pb numeric(14,5),
  turnover_rate numeric(14,5),
  market_cap_yuan numeric(24,2) not null check(market_cap_yuan > 0),
  raw jsonb not null,
  primary key (ts_code, trade_date)
);
create index if not exists daily_prices_date_idx on daily_prices (trade_date desc);

-- Compact unadjusted historical closes for the 20/50-day technical signals.
-- A separate table avoids duplicating full daily_basic raw JSON for each date.
create table if not exists price_history (
  ts_code text not null references stocks(ts_code),
  trade_date date not null,
  close numeric(16,4) not null check(close > 0),
  primary key (ts_code, trade_date)
);
create index if not exists price_history_date_idx on price_history (trade_date desc);
create table if not exists price_history_fetches (
  trade_date date primary key,
  rows_saved integer not null check(rows_saved > 0),
  fetched_at timestamptz not null default now()
);

create table if not exists financial_reports (
  ts_code text not null references stocks(ts_code),
  kind text not null check(kind in ('balancesheet','income','cashflow')),
  end_date date not null,
  disclosed_at date not null,
  report_type text not null,
  metrics jsonb not null,
  raw jsonb not null,
  ingested_at timestamptz not null default now(),
  primary key (ts_code, kind, end_date, disclosed_at, report_type)
);
create index if not exists financial_asof_idx on financial_reports
  (ts_code,kind,disclosed_at desc,end_date desc);

create table if not exists financial_fetch_log (
  ts_code text primary key references stocks(ts_code),
  attempted_at timestamptz not null default now(),
  succeeded_at timestamptz,
  last_error text
);

create table if not exists job_runs (
  run_id uuid primary key,
  job_type text not null,
  trade_date date not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null check(status in ('running','success','skipped','failed')),
  metrics jsonb not null default '{}'::jsonb,
  error_text text
);
create index if not exists job_runs_status_idx on job_runs (job_type,status,finished_at desc);

create table if not exists screen_results (
  trade_date date not null,
  ts_code text not null references stocks(ts_code),
  run_id uuid not null references job_runs(run_id),
  strategy text not null default 'netnet_v1',
  price numeric(16,4),
  pb numeric(14,5),
  market_cap_yuan numeric(24,2),
  ncav_yuan numeric(24,2),
  ncav_coverage numeric(16,6),
  stress_ncav_yuan numeric(24,2),
  stress_coverage numeric(16,6),
  liability_ratio numeric(16,6),
  report_date date,
  disclosed_at date,
  operating_cf_yuan numeric(24,2),
  net_profit_yuan numeric(24,2),
  is_candidate boolean not null default false,
  status text not null,
  signal text not null,
  flags jsonb not null default '[]'::jsonb,
  assessment text not null,
  created_at timestamptz not null default now(),
  primary key (trade_date,ts_code,strategy)
);
create index if not exists screen_results_candidates_idx on screen_results
  (trade_date desc,is_candidate,ncav_coverage desc);

-- Database access: create a dedicated read-only role for Vercel (see below).
-- Neon does not have Supabase's 'anon' / 'authenticated' roles.
-- When using Supabase instead of Neon, revoke client access separately in
-- its SQL Editor if those roles are present; do not run Supabase-only SQL on Neon.
-- Supabase-only commands (uncomment only on Supabase):
-- REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
-- REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- Optional read-only role; create only after selecting a secure password and
-- granting via the PostgreSQL SQL editor. Never put the password into source control.
-- CREATE ROLE screener_reader LOGIN PASSWORD '...';
-- GRANT USAGE ON SCHEMA public TO screener_reader;
-- GRANT SELECT ON stocks,daily_prices,financial_reports,job_runs,screen_results TO screener_reader;
