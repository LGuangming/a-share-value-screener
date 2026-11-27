# BT05-C — dividend capture and reviewed action evidence

Official source: https://tushare.pro/document/2?doc_id=103 (dividend).
The documented endpoint accepts ex_date, has a 2,000-row response limit
and requires at least 2,000 points. Actual account entitlement and response
coverage must be tested by the operator. No API calls occur in CI.

## Capture

Use a new private evidence directory for each immutable capture series.
One request per selected exchange session, not one request per stock.
The calendar is independently supplied/checked; it must contain actual open
dates, not inferred weekdays. All provider rows are retained, including exact
duplicates, conflicting implementation records and empty responses.
A response at the row cap or with invalid keys/dates/fields fails closed.
Each day publishes a JSON payload and SHA256/size/count receipt last.
Re-run the same command to skip verified dates; a failed response is not
published, and orphan files are never silently overwritten. Capture status
is research-only, even when every requested date has a receipt.

Run the online CLI explicitly in a separately approved Python runtime with
the official TUSHARE_TOKEN already securely configured. Do not mount token
files in offline Compose or use the relay. Do not overlap other Tushare jobs.
The existing client limits calls to 100/minute; this capture sets retries=1
so the explicit budget is a hard cap on new request attempts. Default 50,
maximum 500. No scheduler, DB writes, GHCR pushes or automatic deployments.

    PYTHONPATH=backend python -m value_screener.bt05_dividend_capture capture \
      --root /opt/ashare/backtest/evidence/dividend-v1 \
      --calendar /opt/ashare/backtest/evidence/calendar.json \
      --start 2020-01-01 --end 2025-12-31 \
      --budget 50 --confirm CAPTURE-DIVIDEND

First run with --budget 1 over a known dividend date, verify returned fields
and official account access, then resume with a small approved budget.
Create the destination directory before executing. Historical date capture
is a current snapshot, not proof of historical revision completeness.
For a later refresh, use a new versioned root; do not overwrite evidence.

Offline audit (same dates and root, new report outside raw archive):

    PYTHONPATH=backend python -m value_screener.bt05_dividend_capture audit \
      --root /opt/ashare/backtest/evidence/dividend-v1 \
      --calendar /opt/ashare/backtest/evidence/calendar.json \
      --start 2020-01-01 --end 2025-12-31 \
      --report /opt/ashare/backtest/evidence/dividend-audit-v1.json

Audit reports missing sessions, row counts, empty responses and source hashes.
A zero-row response is not independently certified no-action evidence.

## Reconcile

Use the same verified BT03 market/security receipts and calendar as BT04.
Choose a warmup including at least the exchange session before the requested
interval (2020 beginning cannot include 2019 in existing BT03; start later
or extend source coverage). Reconciliation requires all requested dividend
session receipts and the corresponding BT03 data, and rechecks source hashes.
Raw mainboard codes, implemented records and observed factor changes are
matched by code and ex-date. Reconciliation checks preceding record date,
implementation announcement date, gross cash per old share, bonus/transfer
components, payment and share-listing dates, and theoretical ex-price/factor
ratio (0.5% relative tolerance; a heuristic, not formal certification).
Conflicting implementations, no matching record, missing prior/current price,
delayed shares and unmatched factor changes are blocked. All original provider
fields remain in the raw archive. Never derive cash or shares from factors.

    PYTHONPATH=backend python -m value_screener.bt05_dividend_reconcile build \
      --market /opt/ashare/backtest/02_standardized/market \
      --security /opt/ashare/backtest/02_standardized/tradeability \
      --raw /opt/ashare/backtest/evidence/dividend-v1 \
      --scratch /opt/ashare/backtest/tmp/bt05 \
      --calendar /opt/ashare/backtest/evidence/calendar.json \
      --warmup 2020-12-01 --start 2021-01-01 --end 2025-12-31 \
      --withholding 0.20 \
      --output /opt/ashare/backtest/evidence/dividend-candidates-v1.json

Withholding is a required explicit research scenario. 0.20 above is an example
assumption applied to gross cash; it is NOT certified historical dividend
tax or a recommendation. Neither gross nor provider net field proves an
investor-specific amount. Actual holding-period tax and later recovery
remain unimplemented. Factor ratio reconciliation uses gross cash.

## Review and BT05

The candidate file cannot be consumed by BT05. A human must check source
announcements and explicit net cash/share assumptions, then prepare a review
file with candidate file SHA256, reviewer identifier, and individually accepted
candidate IDs. Do not approve all records automatically. Example structure:

    {
      "status": "OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED",
      "candidate_sha256": "<sha256 of exact candidate file>",
      "reviewer": "<responsible reviewer>",
      "accepted_ids": ["<individually reviewed candidate id>"]
    }

Run:

    PYTHONPATH=backend python -m value_screener.bt05_dividend_reconcile review \
      --candidates /opt/ashare/backtest/evidence/dividend-candidates-v1.json \
      --review /opt/ashare/backtest/evidence/dividend-review-v1.json \
      --calendar /opt/ashare/backtest/evidence/calendar.json \
      --output /opt/ashare/backtest/evidence/actions-v1.json

Unapproved and unsupported events are retained as blocked events. Approved
ones become BT05-readable explicit research actions. Pass the new ledger
with --actions /evidence/actions-v1.json to BT05/BT05 grid, using a new
result directory. Output includes source/review hashes and no certification.
Raw source completeness and calendar are independent gates. A missing factor
change can conceal an action, and rights, delayed/fractional shares, delisting
settlements, holding-period dividend tax and historical revision coverage
remain outside this implementation. No actual provider capture or VPS run
has been performed by this PR. No real backtest performance can be claimed.
