# Independent technical strategy research — 0.1.1

Scope: MA20/50 trend pullback and short-term oversold reversal, no production
database changes, no schedule, no daily publication, no dependency on NCAV.

## Signal rules

- Point-in-time mainboard eligibility must be explicitly true, with at least
  120 exchange sessions since listing (configurable). Unknown eligibility,
  missing or invalid factor/amount, nontrading bars and calendar gaps do not
  produce a signal. Gaps reset the rolling state; no fill-forward.
- Technical OHLC uses raw OHLC times its historical adjustment factor;
  execution always uses separate raw bars and raw limits. The factor series
  must itself be verified; it does not account for dividend cash or shares.
- MA20/50: fast > slow, close > fast, fast higher than 5 sessions ago,
  one of the prior 5 bars intersects its fast MA ±2%, and current close
  exceeds previous adjusted high. Ranking: fast/slow minus one descending.
- Reversal: on the PREVIOUS bar, 10-session return <= -10%, RSI14 < 30,
  MA20 bias < -8%; today close exceeds previous high and volume exceeds the
  prior 5-session mean. Ranking: magnitude of prior 10-session decline.
- Both: 20-session mean turnover amount >= CNY 50m, known historical
  eligibility. The above are initial hypotheses, not optimized parameters.
- RSI14 and ATR14 use Wilder smoothing on available continuous observations.
  ATR14 percentage is an available diagnostic, not an entry criterion.
- Ties break by code. Signals repeat if independent qualifying days occur;
  the portfolio cannot open duplicate simultaneous holdings.

## Isolated research runner

The standard-library CLI consumes an operator-prepared UTF-8 JSONL extract
ordered by ts_code, ascending session, with the following columns:
ts_code, trade_date (ISO), open, high, low, close, volume, up_limit, down_limit,
suspended (explicit boolean), adj_factor, amount_yuan, eligible (boolean or
null), listing_sessions (integer or null), session (nonnegative exchange
calendar index shared by all stocks).
Price limits must be raw, finite positive prices or null (unknown blocks
strict execution). Amount is in yuan, not Tushare's thousands of yuan.
Eligibility is based solely on information available BEFORE that day's open;
must not be inferred from today's current stock list or later announcements.
The extract should include a warm-up interval and all market sessions.
Do not use unverified BT03 evidence as certified historical eligibility.

    PYTHONPATH=backend python -m value_screener.technical_research --input /research/observations.jsonl --output /research/results/signals-v1.json --start 2025-01-01 --end 2025-12-31

The result is new-file-only, records input SHA256, parameters, dates, factors,
signal counts and ranking. Optional --config accepts TechnicalConfig fields.
No network, provider tokens or production DB required. No production
publication is permitted.

For bounded engineering execution only, add --engineering-backtest
--holding 5 (or 10/20), --stop 0.03 (or 0.05/0.07), and a suitable
--max-rows (default 250000). Default capital CNY100000, five positions, existing
kernel fee assumptions. Orders based on close T attempt the next market open;
entry checks open-known historical eligibility, raw limit and suspension,
stop loss is a close-time trigger attempting the next open (not an intraday
guaranteed fill); blocked exits retry. Opening position sizing uses current
open or last known close, never today's close.

## Acceptance gates

This PR implements strategy logic, parameters, independent factor computation,
unit-test fixtures and a normalized-input research runner. It intentionally
does not certify actual input quality, certify BT03 market/security evidence, publish daily signals, or run
2020-present real-data backtests. BT03 (PR #44) remains separate. Its historical
ST and listing evidence is not yet fully certified and the 2026 ST data can
be unknown. Corporate action cash/share accounting and historical fee
schedule remain blockers to validated performance. The existing execution
kernel is in-memory; full-market multi-year processing and peak memory need
separate measurement/optimization. Never publish engineering returns as
verified strategy performance.


## BT04 — direct BT03 Parquet integration (research only)

The new value_screener.bt04_research entrypoint consumes BT03 version-1
published receipts under 02_standardized/market and 02_standardized/tradeability.
No full-market JSONL input or per-stock price copy is required. Completed years
use monthly receipts; 2026+ uses exact daily receipts. It verifies SHA256,
file sizes where present, partition dates, row counts, unique keys, market/security
key parity, security receipt's market hash, suspension event file/count and
selected dates against the supplied calendar. It does not revalidate BT03 raw
source manifests; accept BT03 source verification separately. Use immutable
accepted inputs; output hashes and input/code/config fingerprints control reuse.

Calendar is an explicit operator-provided JSON object with ISO dates:
{"from":"2024-01-01","to":"2025-12-31","sessions":["...actual sorted open dates..."]}.
The dates must come from the independently checked SSE/SZSE trading calendar,
not weekdays or inferred price availability. A missing session, missing receipt,
invalid key or hash fails closed. The file hash is recorded; the adapter does
not independently certify the calendar source.

Eligibility defaults to strict_uncertified_blocked: existing BT03 evidence
cannot certify historical eligibility, hence no entry signals. --provisional
explicitly enables engineering research on historical NORMAL evidence with
prior-date announcement, accepted resolution, listing snapshot dates and valid
limits. This is not historical universe certification. 2026+ ST evidence is
currently blocked (including uncertified namechange candidates); unknown is not
normal. Any same-day suspend/resume event conservatively excludes that bar.
Observed traded bars since warmup provide a lower bound for listing age, so
start warmup sufficiently early; the engine resets rolling factors across market
calendar gaps. Amount conversion: Tushare thousands of CNY times 1000.

### Standalone VPS commands

Use a separately built and accepted immutable image containing this PR's code.
The existing BT03 image alone does not contain the BT04 module. Mount BT03
market/security and calendar/config read-only; dedicated result/scratch writable.
Do not include compose.bt04.yaml in production Compose.

Example host paths (adjust if the BT03 acceptance root differs):

    export BACKTEST_DIR=/opt/ashare/backtest
    export BT04_EVIDENCE_DIR="$BACKTEST_DIR/evidence"
    export BT04_RESULT_DIR="$BACKTEST_DIR/03_technical/acceptance-2025-v1"
    export BT04_SCRATCH_DIR="$BACKTEST_DIR/tmp/bt04"
    install -d -o 10002 -g 10010 -m 0750 "$BT04_RESULT_DIR" "$BT04_SCRATCH_DIR"
    # Set BT04_IMAGE to the separately approved immutable image digest before running.
    # Place calendar.json in BT04_EVIDENCE_DIR with verified coverage.
    docker compose -f compose.bt04.yaml run --rm -T --no-deps technical-research --calendar /evidence/calendar.json --warmup 2024-01-01 --start 2025-01-01 --end 2025-12-31 --shards 8 --provisional

Requires all BT03 market/security receipts for the entire warmup plus research
interval. Start with a short accepted interval if historical data is incomplete.
The above command is an example, not a claim those partitions exist.

Optional --shard 0 computes one of eight deterministic code shards. Resume
by invoking the identical command; matching verified shards are reused,
changed inputs/config/code require a new result directory. A crash after data
but before receipt leaves an orphan and fails closed; inspect it rather than
automatically deleting it. Each receipt records elapsed time and Linux process
peak RSS (not cgroup peak). Run without --shard to finish/verify all shards and
produce summary.json. Shards can be sequentially resumed; one output lock
prevents concurrent writers. DuckDB uses one thread, 384MB memory budget
and disk spill; the Compose cap is 1GiB and one CPU.

For a single bounded engineering portfolio window, append:

    --execute-start 2025-01-01 --execute-end 2025-02-28 --holding 10 --stop 0.05

Requires --provisional and all shards. The default/max row cap is 250000;
shorten the interval if preflight rejects it. Across all shards, rank each
strategy globally by score/code, then execute one independent shared-cash
portfolio per strategy. Do not sum shard portfolios or concatenate independent
window returns: positions and cash do not carry between windows. Full-period
out-of-core portfolio execution, certified corporate-action accounting, fees,
historical eligibility and verified performance publication are separate gates.

### Acceptance

Real DuckDB/Parquet CI fixtures exercise both source layers, shard reuse,
strict/provisional gating, one globally ranked portfolio, cap rejection and
corruption rejection. VPS acceptance must separately record elapsed wall time,
peak RSS, container peak memory and scratch/result size for one short interval,
then extend to full-year and all available data. CI fixture success is not
a VPS benchmark or historical strategy performance.
