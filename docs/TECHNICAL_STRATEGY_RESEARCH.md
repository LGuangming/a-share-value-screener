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
does not certify actual input quality, build the BT03 market/security manifest
adapter, change the standalone VPS Compose, publish daily signals, or run
2020-present real-data backtests. BT03 (PR #44) remains separate. Its historical
ST and listing evidence is not yet fully certified and the 2026 ST data can
be unknown. Corporate action cash/share accounting and historical fee
schedule remain blockers to validated performance. The existing execution
kernel is in-memory; full-market multi-year processing and peak memory need
separate measurement/optimization. Never publish engineering returns as
verified strategy performance.
