# BT05-D — rolling held-out research and benchmark comparison

Use only completed BT03 research evidence, a checked trading calendar and
reviewed corporate-action assumptions. All results are engineering-only.
This module does not collect a benchmark, certify real historical returns,
publish signals or alter the production database.

## Experiment plan

Freeze all folds, candidate parameter profiles and selection criteria before
running. A suggested first experiment (not optimized settings):

    {
      "warmup":"2020-01-01",
      "folds":[
        {"train_start":"2021-01-01","train_end":"2022-12-31","test_start":"2023-01-01","test_end":"2023-12-31"},
        {"train_start":"2022-01-01","train_end":"2023-12-31","test_start":"2024-01-01","test_end":"2024-12-31"},
        {"train_start":"2023-01-01","train_end":"2024-12-31","test_start":"2025-01-01","test_end":"2025-12-31"}
      ],
      "signal_configs":[
        {},
        {"pullback_tolerance":0.01,"oversold_return":-0.08,"rsi_limit":35},
        {"pullback_tolerance":0.03,"oversold_return":-0.15,"rsi_limit":25},
        {"ma_fast":15,"ma_slow":60,"oversold_days":15}
      ],
      "execution_configs":[
        {"holding_sessions":5,"stop_loss_rate":0.03},
        {"holding_sessions":10,"stop_loss_rate":0.05},
        {"holding_sessions":20,"stop_loss_rate":0.07},
        {"holding_sessions":10,"stop_loss_rate":null}
      ],
      "min_trades":10,
      "max_drawdown":0.30
    }

These are explicit joint signal profiles, not an exhaustive factorial search
over every signal field. Each profile evaluates both strategies. Defaults
come from TechnicalConfig/BacktestConfig. Limit: 12 folds, 8 signal profiles,
12 execution profiles and 96 total training trials (folds × signal ×
execution). Example above is 48 trials, each evaluating two strategies.
Begin with a short accepted slice and small grid; full VPS runtime not measured.
Fold dates must advance and test windows must be disjoint. Past held-out
periods may become training in later folds, never in their own fold.

For each fold the runner generates BT04 signals only through the training
end date; warmup is causal. Each execution candidate starts with the same
cash unless the plan explicitly changes initial_cash (comparison should use
identical initial cash and position constraints). The selection is training
return descending among those meeting minimum closed trades and maximum
drawdown, then smaller drawdown and deterministic config ID. Freeze
selection.json before any held-out signal generation. Generate only selected
signal configurations on the held-out window, and run only the selected
strategy per configuration. No selection means no test for that strategy.
One selection per strategy per fold; no re-selection using held-out results.

## Benchmark input

Supply an operator-reviewed JSON with actual exchange-session levels:

    {
      "status":"OPERATOR_REVIEWED_BENCHMARK_NOT_CERTIFIED",
      "code":"000300.SH",
      "kind":"price_index",
      "source_ref":"<actual provider/export and provenance>",
      "levels":[{"date":"2023-01-03","close":1234.56}]
    }

The level shown is an invented schema example, not historical CSI 300 data.
Use e.g. CSI 300 price index or a verified total-return index, and correctly
label price_index / total_return_index. No fabricated levels, forward-fill,
or missing trading sessions. Full coverage across all held-out sessions
is required. Benchmark and strategy are normalized to 1 at the first
held-out session's close; the portfolio starts in cash, and earliest
execution is next open after its first in-window close signal. Report
return difference (not ratio), benchmark drawdown, annualized information
ratio based on aligned daily excess returns, and a zero-return cash
comparison. Benchmark is an index-level reference without fees; price
index excludes dividends, so it is not directly a total-return comparison.
Record actual index coverage/provenance separately; this program verifies
file integrity and alignment, not provider data accuracy.

Each held-out fold is an independent portfolio starting with fresh cash.
The aggregate reports selected/unselected folds and average excess return
over selected folds, plus the number of positive-excess folds; it is NOT
a continuous out-of-sample NAV or compound return. No selection is not a
zero-return investment. No benchmark series is synthesized.

## VPS run

Use a separately approved immutable image containing BT05-D and the same
BT03/evidence directories. The standalone Compose has network none, one CPU,
1 GiB memory cap, read-only source/evidence, writable separate scratch/results.

    export BACKTEST_DIR=/opt/ashare/backtest
    export BT04_EVIDENCE_DIR="$BACKTEST_DIR/evidence"
    export BT05_WALK_RESULT_DIR="$BACKTEST_DIR/04_backtest/walk-v1"
    export BT05_WALK_SCRATCH_DIR="$BACKTEST_DIR/tmp/walk-v1"
    install -d -o 10002 -g 10010 -m 0750 "$BT05_WALK_RESULT_DIR" "$BT05_WALK_SCRATCH_DIR"
    # Set BT05_IMAGE to the approved immutable worker image.
    docker compose -f compose.bt05-walk.yaml run --rm -T --no-deps walk-forward \
      --calendar /evidence/calendar.json --plan /evidence/walk-plan.json \
      --benchmark /evidence/benchmark.json --actions /evidence/actions-v1.json \
      --fees /evidence/fees-v1.json

Identical inputs/code/plan allow resume via existing BT04 shard and BT05
monthly checkpoints. Changed inputs/config/code require a new experiment
directory. Each fold stores training runs and selection, selected held-out
runs and daily benchmark comparisons. A blocked corporate action or corrupt
source stops the experiment rather than dropping failed trials. This sequential
implementation revalidates sources on each trial; resource usage and runtime
require actual VPS measurement.

Remaining: real benchmark acquisition and source validation, BT03 full
coverage, company-action capture/review, historical eligibility/fees, rights,
delayed/fractional shares, delisting and holding-period taxes. No live
historical research results or deployment from this change.
