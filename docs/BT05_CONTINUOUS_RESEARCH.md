# BT05 — continuous offline engineering backtest

BT05 consumes an existing complete BT04 provisional run (run.json, summary.json,
all shard receipts/data), its exact calendar and corresponding BT03 version-1
market/security receipts. It is deliberately not a certified historical return
engine. No production schedule, database writes or website publication.

## Continuous execution

- Signals are staged in a disk-backed temporary DuckDB database. Verify each
  shard's hash, count, strategy, date, code and shard assignment; globally rank
  by score descending then code, independent of shard.
- Read one market/security partition at a time (monthly before 2026, daily
  afterwards), in trading-day/code order, and use a single portfolio per
  strategy. There is no total historical row cap. A single day's bars and
  monthly ranking/records are retained; live holdings, listing-age lower
  bounds, pending stop/exit orders, cash and previous-day signals carry across
  month/year boundaries. Beginning of execution starts with cash; warmup
  calculates prior listing-age evidence but never buys before start.
- Signal on day T, buy at next open with eligibility known before open.
  Stop-loss is close-triggered, next-open attempted; blocked exits retry.
  No forced liquidation at end. Absent bar uses last mark and increments stale
  count; raw slippage prices are bounded by the raw exchange limit.
- Daily equity, fills/trades, fees, cumulative return, annualized return
  (252 sessions), max drawdown, win rate, PF, annualized Sharpe (zero risk-free),
  traded notional, stale marks and blocked order counts. No benchmark or
  risk-free time series yet.
- Each completed calendar month stores daily records and all resume state
  in one exclusive JSON receipt. Receipt hash links to previous month.
  Verify exact input/config/code identity and checkpoint chain on restart;
  replay only an unfinished month. Change config or data: new output directory.
  Summary is published only after completing and rechecking source identities.
  Checkpoint integrity assumes accepted immutable read-only sources; hashes
  are not signatures or protection against a privileged adversary.

## Fees

Default BacktestConfig fees are explicit engineering assumptions, not verified
historical taxes. Optional --fees input supports date-effective commission,
minimum commission, sell tax, bilateral transfer fee and slippage.

JSON schema (example placeholder rates; not historical legal rates):
{
  "status": "OPERATOR_ASSUMPTIONS_NOT_CERTIFIED",
  "through": "2025-12-31",
  "periods": [
    {"start":"2020-01-01","commission_rate":0.0003,"min_commission":5,
     "sell_tax_rate":0.0005,"transfer_rate":0.00001,"slippage_rate":0.0005}
  ]
}

Periods must be sorted, unique and cover the requested dates. Source hash and
assumed rates are persisted. Do not claim the example is a historical schedule.

## Corporate action gate (not accounting)

A held security's changed/invalid adjustment factor blocks the engineering run
before execution for that day. No inferred share multiplier, synthetic dividend,
or realized return adjustment from factors. Earlier checkpoints remain usable
only for the identical run and data. A real multi-year run can stop at the first
such event. Full accounting needs reviewed ex-date/record-date/payment-date,
cash distributions, share/rights events and delisting settlement evidence;
the factor gate alone cannot detect all actions (especially missing bars).
This is a formal backtest blocker, not an implemented corporate action ledger.
BT03 and BT04 evidence/eligibility are still provisional.

## VPS acceptance

Use the separate compose.bt05.yaml with a reviewed image containing BT05.
BT03 and BT04 inputs and evidence are read-only, network none, one CPU, 1 GiB
container cap, DuckDB 384 MB, one thread, disk scratch. These are configurations,
not measured peak usage.

    export BACKTEST_DIR=/opt/ashare/backtest
    export BT04_RESULT_DIR="$BACKTEST_DIR/03_technical/acceptance-2025-v1"
    export BT04_EVIDENCE_DIR="$BACKTEST_DIR/evidence"
    export BT05_RESULT_DIR="$BACKTEST_DIR/04_backtest/acceptance-2025-v1"
    export BT05_SCRATCH_DIR="$BACKTEST_DIR/tmp/bt05"
    install -d -o 10002 -g 10010 -m 0750 "$BT05_RESULT_DIR" "$BT05_SCRATCH_DIR"
    # Set BT05_IMAGE to the separately reviewed immutable Worker image.
    docker compose -f compose.bt05.yaml run --rm -T --no-deps portfolio-research --calendar /evidence/calendar.json --start 2025-01-01 --end 2025-12-31

For another execution configuration, use --config /evidence/execution.json
(BacktestConfig fields e.g. {"holding_sessions":5,"stop_loss_rate":0.03}),
and a NEW BT05_RESULT_DIR. Changing signal parameters requires a new BT04 run.
Repeated independent windows start with fresh cash and cannot be chained to
claim a continuous return. Compare development and held-out intervals separately;
freeze signal/portfolio parameters before examining held-out outcomes.
Automated parameter sweeps and sample-out comparison reports are not implemented.

Acceptance first on short constant-factor fixture/real reviewed slice,
compare BT04 bounded and BT05 daily fills, cash and equity; then verify
restart across a month and year boundary, record wall time, cgroup peak and disk
usage, and extend only after corporate-action and historical-data gates.
CI exercises synthetic prices and real Parquet format, not actual A-share returns.
