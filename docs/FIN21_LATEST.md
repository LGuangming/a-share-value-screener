# FIN21 latest financial research data

Input: explicit FIN20 published local snapshot manifest SHA256 and canonical raw
archive. Verify every manifest-listed file, logical row hash and existing raw
reference before reading. No SQLite source database, network, PostgreSQL,
production Compose or archive metadata writes.

Latest period is max observed 2020+ report period per mainboard code pattern
at the FIN20 collection cutoff, even if the newest report is incomplete.
Multiple company types at that period are blocked, no older-period fallback.
The union with same-session mainboard price/basic codes preserves new securities
without a financial observation, and old financial codes without market data.
This is not a certified listing or tradability universe.

Output is seven ZSTD Parquet tables: financial_statements (one row per
code/API, selection and field decisions), statement_versions (all actual selected
source payloads with original version IDs), financial_metric_detail (28 metrics
with status and recipe inputs), financial_metrics (wide with metric status
columns), valuation_metrics (exact committed same-session close/market cap/
shares, provider PB/PE and independent cap-based ratios), financial_quality
(quality counts and checks), strategy_inputs (financial/valuation availability,
never buy signals). Parent equity uses explicit-type consensus/partial consensus
and carries field status and version IDs. Independent PB is current market cap
divided by latest observed reported parent equity, not close divided by historical
share-proxy BVPS. Market-cap and share units convert from ten thousand to yuan
and shares. No security-level fill. Market values are selected at the newest
observed commit <= snapshot cutoff, or an explicitly requested committed day.
All five committed dataset manifests and payload hashes are checked, with
price/basic grain checks. The newest observed marker failing verification blocks
the run; it does not prove independent calendar completeness. Finance versions
collected later than market day may be included, so this is NEVER a historical
as-of dataset.

Each output is read back and checked by bidirectional EXCEPT ALL; grain checks,
source rehash, and output file SHA256 are mandatory. New staging path only.
Full real-data runtime and memory acceptance remain necessary. Website database
comparison and production integration are later stages (FIN21-H/FIN24).
Do not delete FIN19 inputs until downstream reader acceptance and separate cleanup
authorization. No PIT certification.

Run after CI passes:

```bash
PY=/opt/ashare-research-runtime/bin/python
RAW=/opt/ashare/archive/a-share-backtest/01_raw_data
SNAP=/opt/ashare/archive/a-share-backtest/financial_snapshots/snapshot=2026-11-28-bbac0c9b8963ade0
APPROVE=bbac0c9b8963ade0f935a95123b54963abc2210214b536464a94cfb1baf50cd7
"$PY" -m unittest discover -s tests -p test_fin21_latest.py -v
/usr/bin/time -v "$PY" -u scripts/fin21_latest.py \
  --snapshot "$SNAP" --raw "$RAW" --approve "$APPROVE" \
  --output /opt/ashare/backtest/fin21-latest-staged-v1
```

Inspect manifest statistics, output hashes, selected market date, report period
distribution, missing market rows, metric coverage, and quality exceptions.
No automatic archive publication or live-site data change.
