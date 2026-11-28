# FIN18-B BaoStock financial evidence pilot

Research-only, isolated probe. No production database, Tushare token, or source archive writes.

## Run offline checks

```bash
python3 scripts/fin18_b_baostock_probe.py --self-test
```

## Run on VPS

Use an isolated virtual environment with BaoStock installed. Run as a user who can read the weekend missing.csv, preferably in a temporary directory; never run with production database credentials.

```bash
timeout --kill-after=15s 10m python scripts/fin18_b_baostock_probe.py \
  --missing /var/lib/ashare-collection-work/financial-weekend-20261128/missing.csv \
  --out /tmp/fin18-b-$(date -u +%Y%m%dT%H%M%SZ) \
  --asof 2026-11-28 --per-reason 10
```

The new output directory contains plan.json, responses.jsonl and summary.json (summary may be absent if externally killed). The pilot samples up to ten distinct company-period pairs per missing-indicator reason, prioritizing 2020+ mainboard records, and queries profit metrics. Two controls query all six financial endpoints. Requests are sequential with at least one second between completed calls; three consecutive errors stop the pilot. Maximum 32 default requests, excluding login/logout and provider pagination.

A finite roeAvg including zero, matching code and period and plausible pubDate is a **candidate**, not a repaired Tushare ROE. No unit conversion, calculation equivalence, restatement or original publication history is assumed. Multiple returned rows are preserved and must be reconciled before use. Non-mainboard records remain unmodified. This pilot does not resolve missing statements or certify PIT. Do not extrapolate sample recovery to the full 1,666 missing indicator records.

Provider reference: https://baostock.com/mainContent?file=seasonProfit.md
