# FIN19 financial standardization — offline staging v1

Independent of the unmerged BT03 and BaoStock PRs. Inputs are the weekend collector
archive and the older financial batches under 01_raw_data. No network, token,
database server, Docker image change, schedule or production deployment.

## Run fixture tests
```bash
python3 -m unittest discover -s tests -p test_fin19_standardize.py -v
```

## VPS acceptance (only after CI review)

Use a detached checkout at the reviewed commit, not the live application checkout.
Verify enough free disk first: expanded JSON plus SQLite indexes and journal can
be several GB; real peak disk, memory and wall time are not yet measured. A new
output root is mandatory. Do not run concurrently with input archive writers.
```bash
df -h /opt/ashare/backtest
/usr/bin/time -v python3 scripts/fin19_standardize.py \
  --weekend /var/lib/ashare-collection-work/financial-weekend-20261128 \
  --old-raw /opt/ashare/archive/a-share-backtest/01_raw_data \
  --output /opt/ashare/backtest/fin19-staging-v2 \
  --asof 2026-11-28
```

The manifest is written last in a temporary sibling directory before renaming.
Failed staging directories are retained for inspection; never consume them.
Reruns require a new output path. Inputs are read only. All nine weekend APIs
and four older financial API families plus verified historical disclosure batches are included, including non-mainboard and
warmup data. Source pairs, hashes, size, counts and currency metadata are checked.
Weekend response-line and done-row counts must reconcile to the manifest; actual
collector encoding and old indicator batch completeness still require VPS acceptance.
Non-done responses are excluded from staging and remain in the original archive.
No attempt is made to reconstruct their rows.

## Ledger contract

- sources: file identities and reconciled input counts
- versions: one canonical JSON payload per API/hash, typed subset, flags, diagnostic ratios
- lineage: every accepted occurrence, including exact duplicates, with source and line offsets
- group_quality: statement groups by code, report period, report type, company type,
  end type and candidate version date; never coalesce different groups or select winners
- calendar / calendar_origin / statement_date_evidence: actual-date observations, origin and match status
- source_field_quality: per-file field availability (absent, null, invalid, valid numeric), counted per input occurrence rather than distinct reports
- missing_input: original missing.csv retained unchanged in meaning

An absent field is not zero. Source numeric strings remain in the payload but are
not coerced to numeric typed fields. Financial amounts are assumed to follow the
verified Tushare yuan convention. Group comparisons distinguish metadata-only,
non-null disagreement, schema/complementary differences, null population and
numeric representation differences. Non-null disagreement is not proof of an
erroneous report. Conflicting groups and flagged rows remain in the ledger; no
strategy-facing output is produced. Date matches are evidence, not verified
historical release dates. All rows and diagnostic metrics are research-only.

## Deferred gates

FIN19 follow-up: sector applicability, field-level coverage of all requested
columns, original/revised lineage, true conflict adjudication, cash-flow
reconciliation and multi-period ROE/TTM calculations. FIN20: typed Parquet
partition/export and manifest validation. FIN21/22: independent latest and
historical PIT selection. FIN23/24: BT03 and website integration.

No formal PIT certification, no financial-factor publication, no Tushare calls.

## V5 archive inventory follow-up (format version 2)

The existing disclosures directory is mandatory and validated against the BT03
period manifest contract (paired payload, report period, object key, source,
status, compressed size, SHA256, row counts, populated/missing actual dates).
Both old and weekend actual-date observations are retained, even when they
disagree. Shared date keys are counted, not proof of historical revision availability.
Field-quality counts distinguish absent payload keys from explicit nulls and
invalid values; absent does not itself prove the provider did not request a field.
Rebuild into a new output directory; do not overwrite the accepted format-v1 ledger.
The older stock_basic snapshot and incremental reader records are not yet merged.
The formal archive location remains /opt/ashare/archive; do not directly modify
.archive_meta, overwrite managed raw object keys, or publish Parquet before FIN20.

## V5C/V6 snapshot metric sidecar

Run only on the accepted format-v2 V5 ledger. No raw reread or network/provider
access. Use a new output directory; verify the input ledger SHA and size before
and after. The ledger and its manifest remain immutable.

```bash
python3 -m unittest discover -s tests -p 'test_fin19_metrics.py' -v
/usr/bin/time -v python3 -u scripts/fin19_metrics.py \
  --ledger /opt/ashare/backtest/fin19-staging-faaf086 \
  --output /opt/ashare/backtest/fin19-metrics-v1
```

The output contains metrics.sqlite3 and manifest.json. Four tables:
field_groups (per exact API/code/period/company type/end type/candidate date
field decisions and version IDs), period_selection (latest observed date,
no older fallback), metrics (typed value, unit, explicit failure state, input
IDs and formula version), provider_checks (calculated vs provider FCF/EBIT
including every conflicting provider variant).

Scope is report_type=1, 2018+ mainboard code patterns, not historical security
eligibility. Old raw fields lacked end_type. Unknown types are kept separate;
same-date known type is selected only if overlapping legacy fields agree.
Unknown-only types are diagnostic and not used for metrics. No field fill
from another version: formula fields must occur together in at least one
original payload. A conflict in provider-only FCF/EBIT does not null an
otherwise coherent underlying formula.

Amounts CNY, ratios decimal, BVPS CNY/share using reported period-end total
shares (not current shares). Q2/Q3/Q4 standalone flows = YTD minus previous
quarter YTD. TTM = current YTD + previous full year - prior-year comparable
YTD; year end uses annual amount directly. ROE uses parent TTM profit and
average of current/prior-year comparable parent equity, ROA uses consolidated
profit and average assets, turnover uses revenue and average assets. Nonpositive
denominators and missing inputs are not zero; growth from a nonpositive base
is not a percentage. FCF = CFO - cash paid for long-lived asset acquisition,
not necessarily provider FCF or FCFF. EBIT diagnostic proxy = profit before tax
+ finance-cost interest expense (fin_exp_int_exp), not financial-company int_exp.
Industry-specific measures (NCAV, liquidity, gross margin, FCF, EBIT proxy)
are limited to comp_type=1. No valuation multiples, ROIC or EBITDA
calculation pending separate accounting definitions and price/share lineage.
Provider comparison is currently FCF and EBIT only; ROE/ROA indicator
comparison is deferred until comparable definitions and periods are mapped.

All period selections use the ledger snapshot cutoff, including later
corrections to earlier periods. Candidate-date max is diagnostic, NOT the
historically known date. This sidecar must not be read by backtests or
production. Actual coverage, disk/time, statement-type distributions and
provider differences require VPS acceptance. V7 reconciliation, original
revision lineage and FIN20 Parquet publication remain separate gates.
