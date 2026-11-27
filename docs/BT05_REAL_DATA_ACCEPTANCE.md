# BT05-E — real-data acceptance runbook

This is a preparation/acceptance workflow, not a claim that the VPS contains
complete data. Complete CI for the exact revision before executing. Use a
separately approved source checkout/runtime, with DuckDB dependencies for
offline steps. Do not run against production DB, use a relay, or overlap
other Tushare tasks. No credentials in command lines, logs or GitHub.

## Reference data: first small online probe

Tushare trade_cal (https://tushare.pro/document/2?doc_id=26) and index_daily
(https://tushare.pro/document/2?doc_id=95) require account entitlement.
Calendar rows are collected for SSE and SZSE including closed dates.
Index close and previous close are retained. Default codes: 000300.SH,
000905.SH, 000852.SH, all price indexes, not total-return indexes.
Annual slices keep requests small; each slice is checksummed and resumed.
2020–2025 with all three indexes needs 30 new API requests if none exist.
This is a request count estimate, not a measured runtime.
 
    export BT=/opt/ashare/backtest
    mkdir -p "$BT/evidence/reference-v1"
    export PYTHONPATH=backend
    python -m value_screener.bt05_reference_capture capture \
      --root "$BT/evidence/reference-v1" --start 2020-01-01 --end 2025-12-31 \
      --budget 1 --confirm CAPTURE-REFERENCE

Use an already securely configured official token. Verify the first receipt;
then repeat with --budget 30, never while another provider job is active.
If account denies access, stop and report. No fallback to untrusted relay.

    python -m value_screener.bt05_reference_capture build \
      --root "$BT/evidence/reference-v1" --start 2020-01-01 --end 2025-12-31 \
      --output "$BT/evidence/reference-candidate-v1.json"

Build rejects missing civil calendar dates, disagreement between exchanges,
missing/extra index dates, and corrupted receipts. The two exchange calendars
are from the same provider: independently check against exchange publications.
Review index levels against a trusted source. Prepare reference-review-v1.json:
 
    {
      "status":"OPERATOR_REVIEWED_REFERENCE_NOT_CERTIFIED",
      "candidate_sha256":"<exact candidate file SHA256>",
      "reviewer":"<responsible reviewer>",
      "independent_calendar_source":"<actual exchange evidence>",
      "index_source_check":"<actual reviewed index data provenance>"
    }

Only after that real review:

    mkdir -p "$BT/evidence/reference-reviewed-v1"
    python -m value_screener.bt05_reference_capture review \
      --candidate "$BT/evidence/reference-candidate-v1.json" \
      --review "$BT/evidence/reference-review-v1.json" \
      --output "$BT/evidence/reference-reviewed-v1"

Use the resulting calendar.json and benchmark-000300.SH.json, etc. The
export directory must be empty. These remain operator-reviewed research files,
not provider-certified histories.

## Offline prerequisites and first preflight

Run BT03 market and security builds for the entire warmup/execution interval
using the accepted BT03 branch/runtime. Verify raw source coverage separately
with BT03 quality audit. Do not assume all months exist. Financial partitions
are not required for these two technical strategies. Follow
BT05_DIVIDEND_EVIDENCE.md for raw capture, reconciliation and explicit review.
Use a short first real window (e.g. January 2025 with warmup from July 2024),
subject to actual BT03 coverage and reviewed calendar/actions.

    mkdir -p "$BT/tmp/bt05-preflight" "$BT/manifests"
    python -m value_screener.bt05_preflight \
      --market "$BT/02_standardized/market" \
      --security "$BT/02_standardized/tradeability" \
      --dividend "$BT/evidence/dividend-v1" \
      --scratch "$BT/tmp/bt05-preflight" \
      --calendar "$BT/evidence/reference-reviewed-v1/calendar.json" \
      --benchmark "$BT/evidence/reference-reviewed-v1/benchmark-000300.SH.json" \
      --actions "$BT/evidence/actions-v1.json" \
      --warmup 2024-07-01 --start 2025-01-01 --end 2025-01-31 \
      --output "$BT/manifests/bt05-preflight-202501-v1.json"

The report contains input receipts, session and mainboard row counts,
provisional eligibility evidence count, factor gaps, dividend coverage,
supported/blocked action counts, benchmark coverage and preflight process RSS.
Nonzero exit 2 means a research preflight blocker; inspect the report rather
than changing missing data to zero. A passing result is not a formal
backtest certification. Blocked events can still stop an affected holding.

## Actual execution/resource acceptance

After preflight, run BT04 with exactly the same warmup, start/end and
reviewed calendar, explicit --provisional. Then BT05 using its output and
reviewed action ledger, separately approved fee assumptions and immutable
image. See BT05_CONTINUOUS_RESEARCH.md. Record wall clock, peak cgroup
memory (not merely Python ru_maxrss), CPU usage, scratch/result sizes,
completed sessions, cash/position reconciliation and stale marks.
Use Docker stats or cgroup metrics on the VPS during the actual execution.
No fabricated measurement. Resume identical input once, compare summary
and checkpoint hashes. Run longer only after acceptance of the short slice.
Actual historical source coverage, tradeability, rights, fractional/delayed
shares, holding-period tax, fees and delisting remain certification blockers.
Run the walk-forward grid after the short slice succeeds; no publishing
or live daily signals until independently accepted.

The chat connector can update GitHub and read CI but cannot execute these
commands on the actual VPS or access a Tushare token. The operator must run
the above commands in the existing isolated acceptance environment; paths
are examples based on the BT03 layout, not verified filesystem locations.
