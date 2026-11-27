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

## Corporate action evidence and limited accounting

Without --actions the original factor-change gate remains. With --actions,
the engine accepts a narrow explicit research evidence contract. Each event
must identify its source, record date (previous exchange session), ex-date,
payment date, net cash per old share, share multiplier and observed factors
before/after. Bonus shares must be available for trading on the ex-date and
the resulting holding must be integral. No cash or shares inferred from
factor changes. Ex-date entitlement belongs only to the prior close holder.
Receivables count in NAV, not spendable cash; payment is credited at close
of the first session on/after pay date, including after sale. Shares adjust
cost per share without changing total entry cost. Stops and realized trade
P&L include net dividend entitlement, and payment does not double-count P&L.
Monthly checkpoints preserve outstanding receivables. Unsupported, fractional,
delayed share listing, missing or mismatched evidence blocks affected holdings.
Rights subscriptions, tax recovery by holding period, delayed shares and
delisting settlements are NOT implemented. Missing bars can still conceal
events; input completeness is an independent gate. The ledger is manually
reviewed research evidence, not a verified full-market corporate-action feed.

Example schema (synthetic illustration, not actual security data):

    {
      "status":"OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED",
      "from":"2025-01-01",
      "through":"2025-12-31",
      "events":[{
        "code":"000001.SZ",
        "kind":"cash_bonus_same_day",
        "record":"2025-06-09",
        "ex":"2025-06-10",
        "pay":"2025-06-12",
        "shares_tradable_on":"2025-06-10",
        "cash_per_old_share_net":0.5,
        "share_multiplier":2.0,
        "factor_before":1.0,
        "factor_after":2.0,
        "source_ref":"synthetic example; replace with reviewed source"
      }]
    }

Use --actions /evidence/actions.json. A cash-only event uses kind cash_only
and share_multiplier 1. Unsupported events must retain code, ex, kind and
source_ref and will block holding/entry on that day. Dates outside the run
are not executed. Never assert complete coverage with an empty ledger unless
the period truly has no relevant events. BT03/BT04 eligibility remains provisional.

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
The separate bt05_grid module now automates execution-parameter sweeps on
one fixed BT04 signal set. Default 3 holding periods (5/10/20) × 4 stops
(3%/5%/7%/none) = 12 configurations, capped at 24. Development and test
intervals must be disjoint. Each configuration starts with identical cash.
Filter training candidates by at least 10 closed trades and maximum 30%
drawdown (configurable), rank training total return descending, then smaller
drawdown and config ID. Freeze one selected configuration per strategy in
selection.json before running held-out results; no test-based reselection.
If no configuration meets gates, the strategy has no selection. Held-out
portfolios start with cash/no positions; their returns cannot be appended
to training NAV. This is a single fixed split, not rolling walk-forward.
Signal-parameter sweeps require separate BT04 runs and are not yet automated.

Example with the same read-only Compose mounts, using a NEW grid output dir:

    docker compose -f compose.bt05.yaml run --rm -T --no-deps \\
      --entrypoint /usr/local/bin/python3 portfolio-research \\
      -m value_screener.bt05_grid --market /market --security /security \\
      --signals /signals --scratch /scratch --output /results \\
      --calendar /evidence/calendar.json --actions /evidence/actions.json \\
      --train-start 2021-01-01 --train-end 2023-12-31 \\
      --test-start 2024-01-01 --test-end 2025-12-31

Requires BT04 coverage including the full training and test periods.
The grid retains independent run/checkpoint receipts in train/ and test/.
An unsupported action or input error stops the grid; do not discard failures
or silently change the sample. Grid summary reports held-out metrics only
for the frozen selected configuration for each strategy.

Acceptance first on short constant-factor fixture/real reviewed slice,
compare BT04 bounded and BT05 daily fills, cash and equity; then verify
restart across a month and year boundary, record wall time, cgroup peak and disk
usage, and extend only after company-action evidence and historical-data gates.
CI exercises synthetic prices and real Parquet format, not actual A-share returns.
