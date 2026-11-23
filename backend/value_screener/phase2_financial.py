"""Versioned, private research snapshot of historical financial statements.

Collect per-symbol APIs (VIP access unavailable) from 2018 onwards. Batches
are checkpointed in Neon Object Storage; later reruns reuse exact archived
snapshots rather than rewriting prior data. Historical provider snapshots may
lack correction-event history and are NOT yet PIT-certified for backtests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json

from .neon_archive import StorageConfig, preflight, s3_client, verify_object
from .phase2_market import get_optional_json, put_manifest, PREFIX as MARKET_PREFIX
from .tushare_client import TushareClient

BASE = "a-share-backtest/v1/phase2/financial_snapshot"
START_DATE = "20180101"
END_DATE = "20261231"
FIELDS = {
    "balancesheet": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
        "total_cur_assets,total_liab,total_assets,money_cap,accounts_receiv,"
        "inventories,prepayment,oth_cur_assets,minority_int"
    ),
    "income": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,"
        "revenue,n_income,n_income_attr_p,operate_profit"
    ),
    "cashflow": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_cashflow_act"
    ),
    "fina_indicator": "ts_code,ann_date,end_date,roe,roe_dt,roa",
}


def universe_codes(s3, bucket):
    key = f"{MARKET_PREFIX}/universe/stock_basic_snapshot.json"
    snapshot = get_optional_json(s3, bucket, key)
    if (not snapshot or snapshot.get("status") != "current_snapshot_not_historical"
            or not isinstance(snapshot.get("records"), list)):
        raise RuntimeError("A verified frozen universe snapshot is required first")
    codes = sorted(set(x["ts_code"] for x in snapshot["records"]))
    return codes


def fetch_code(client, kind, code):
    """Use one 2018+ call, split full or capped results by year."""
    params = {"ts_code": code, "start_date": START_DATE, "end_date": END_DATE}
    rows = client.query(kind, params, FIELDS[kind])
    if len(rows) >= 100:
        # Some statement endpoints cap results at 100. Partition the
        # *announcement-date* query range to avoid hidden truncation.
        rows = []
        for year in range(2018, 2027):
            part = client.query(
                kind,
                {"ts_code": code, "start_date": f"{year}0101",
                 "end_date": f"{year}1231"},
                FIELDS[kind],
            )
            if len(part) >= 100:
                raise RuntimeError(f"Potential API truncation {kind} {year}")
            rows.extend(part)
    for row in rows:
        if row.get("ts_code") != code:
            raise RuntimeError(f"Unexpected code in {kind} API output")
        value = row.get("end_date")
        if not (isinstance(value, str) and len(value) == 8 and value.isdigit()):
            raise RuntimeError(f"Invalid financial period in {kind} API output")
        for col in ("ann_date", "f_ann_date"):
            if row.get(col) not in (None, ""):
                val = str(row[col])
                if len(val) != 8 or not val.isdigit():
                    raise RuntimeError(f"Malformed financial announcement date {kind}/{col}")
    # Deduplicate EXACT copies only; do not coalesce report versions.
    unique = {}
    for item in rows:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
        unique[key] = item
    return list(unique.values()), len(rows) - len(unique)


def archive_batch(s3, bucket, client, codes, start, count, kind):
    selected = codes[start:start + count]
    if not selected:
        raise ValueError("No securities in requested batch")
    path = f"{BASE}/{kind}/start={start:05d}_count={count:03d}"
    data_key = f"{path}.jsonl.gz"
    manifest_key = f"{path}.manifest.json"
    codes_digest = hashlib.sha256(
        json.dumps(selected, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    existing = get_optional_json(s3, bucket, manifest_key)
    if existing:
        if (existing.get("source") != "Tushare official per-symbol"
                or existing.get("kind") != kind
                or existing.get("codes_sha256") != codes_digest
                or existing.get("object_key") != data_key
                or existing.get("status") != "research_not_pit_certified"):
            raise RuntimeError("Financial batch changed after publication")
        verify_object(s3, bucket, data_key, existing["compressed_bytes"],
                      existing["sha256"])
        print(f"PHASE2_FINANCIAL kind={kind} start={start} "
              f"status=reused_verified rows={existing['rows']}", flush=True)
        return
    records = []
    missing_codes = []
    missing_pub = 0
    duplicate_exact = 0
    for index, code in enumerate(selected):
        rows, duplicates = fetch_code(client, kind, code)
        duplicate_exact += duplicates
        if not rows:
            missing_codes.append(code)
        for row in rows:
            if not row.get("f_ann_date"):
                missing_pub += 1
        records.extend(rows)
        print(f"PHASE2_FINANCIAL_CODE kind={kind} index={start+index} "
              f"rows={len(rows)}", flush=True)
    payload = b"".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8") + b"\n"
        for item in records
    )
    compressed = gzip.compress(payload, compresslevel=6, mtime=0)
    checksum = hashlib.sha256(compressed).hexdigest()
    s3.put_object(Bucket=bucket, Key=data_key, Body=compressed,
                  Metadata={"sha256": checksum}, ContentType="application/gzip")
    verify_object(s3, bucket, data_key, len(compressed), checksum)
    manifest = {
        "format_version": 1, "kind": kind, "start_index": start,
        "requested_codes": len(selected), "rows": len(records),
        "exact_duplicates_deduplicated": duplicate_exact,
        "missing_codes_count": len(missing_codes),
        "missing_codes": missing_codes,
        "missing_f_ann_date_rows": missing_pub,
        "object_key": data_key, "compressed_bytes": len(compressed),
        "sha256": checksum, "codes_sha256": codes_digest,
        "source": "Tushare official per-symbol",
        "source_ann_date_filter": {"start_date": START_DATE,
                                   "end_date": END_DATE},
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "currency": "CNY",
        "financial_amount_unit": "RMB yuan",
        "status": "research_not_pit_certified",
        "caution": ("Historical corrections may be missing. Do not use reports "
                    "before their actual publication date; never backfill a "
                    "newer restatement into an earlier decision date."),
    }
    put_manifest(s3, bucket, manifest_key, manifest)
    print(f"PHASE2_FINANCIAL kind={kind} start={start} "
          f"status=uploaded_verified rows={len(records)} "
          f"missing_symbols={len(missing_codes)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-codes", type=int, default=50)
    parser.add_argument("--kinds", default="balancesheet,income,cashflow,fina_indicator")
    args = parser.parse_args()
    if args.start_index < 0 or args.max_codes < 1 or args.max_codes > 250:
        parser.error("Invalid bounded financial archive batch")
    kinds = [v.strip() for v in args.kinds.split(",")]
    if not kinds or any(k not in FIELDS for k in kinds) or len(set(kinds)) != len(kinds):
        parser.error("Unknown financial data type")
    config = StorageConfig.from_environment()
    s3 = s3_client(config)
    preflight(s3, config.bucket)
    codes = universe_codes(s3, config.bucket)
    if args.start_index >= len(codes):
        parser.error("Batch start beyond stock universe")
    client = TushareClient.from_environment()
    for kind in kinds:
        archive_batch(s3, config.bucket, client,
                      codes, args.start_index, args.max_codes, kind)
    print(f"PHASE2_FINANCIAL_BATCH_STATUS verified_kinds={len(kinds)} "
          f"start_index={args.start_index} codes={min(args.max_codes, len(codes)-args.start_index)} "
          f"total_universe={len(codes)}", flush=True)


if __name__ == "__main__":
    main()
