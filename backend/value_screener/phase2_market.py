"""Phase-2 historical market enrichment (research data, not final backtests).

Four full-market daily Tushare APIs, monthly private Parquet, remote SHA-256.
Month manifests are committed last. A completed year is committed last.
An existing verified month/year is reused with zero provider requests.
Historical ST and financial VIP are NOT silently inferred or treated as complete.
"""
from __future__ import annotations
from .archive_storage import duckdb_connection

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil

from .history_parquet import sessions_for_year
from .neon_archive import StorageConfig, preflight, s3_client, verify_object
from .tushare_client import TushareClient

from .archive_paths import RAW_PREFIX as PREFIX
DATASETS = {
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "stk_limit": ("ts_code", "trade_date", "up_limit", "down_limit"),
    "daily_basic": (
        "ts_code", "trade_date", "total_mv", "circ_mv", "total_share",
        "float_share", "free_share", "turnover_rate", "pb", "pe_ttm",
    ),
    "suspend_d": ("ts_code", "trade_date", "suspend_type", "suspend_timing"),
}
TEXT_FIELDS = {"ts_code", "suspend_type", "suspend_timing"}
CODE = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
DATE = re.compile(r"^20\d{6}$")
REQUIRED_POSITIVE = {
    "adj_factor": ("adj_factor",),
    "daily_basic": ("total_mv", "total_share"),
}
DAILY_MIN_ROWS = {"adj_factor": 2500, "stk_limit": 2500, "daily_basic": 2500}
API_CAP = {"adj_factor": 6000, "stk_limit": 5800,
           "daily_basic": 6000, "suspend_d": 5000}


def prefix(dataset: str, year: int) -> str:
    return f"{PREFIX}/{dataset}/year={year}"


def get_optional_json(s3, bucket: str, key: str):
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code in ("NoSuchKey", "404", "NotFound") or isinstance(exc, KeyError):
            return None
        raise
    body = response["Body"]
    try:
        raw = body.read()
    finally:
        body.close()
    return json.loads(raw)


def put_manifest(s3, bucket: str, key: str, payload: dict):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    s3.put_object(Bucket=bucket, Key=key, Body=raw,
                  Metadata={"sha256": sha}, ContentType="application/json")
    verify_object(s3, bucket, key, len(raw), sha)


def verify_month(s3, bucket: str, dataset: str, year: int, month: str):
    path = prefix(dataset, year)
    key = f"{path}/month={month}.manifest.json"
    manifest = get_optional_json(s3, bucket, key)
    if manifest is None:
        return None
    expected_data_key = f"{path}/month={month}.parquet"
    if (manifest.get("format_version") != 1
            or manifest.get("dataset") != dataset or manifest.get("year") != year
            or manifest.get("month") != month
            or manifest.get("object_key") != expected_data_key
            or manifest.get("fields") != list(DATASETS[dataset])
            or not isinstance(manifest.get("rows"), int)
            or manifest["rows"] < 0
            or not isinstance(manifest.get("sessions"), int)
            or manifest["sessions"] < 15
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256")))
            or not isinstance(manifest.get("parquet_bytes"), int)
            or manifest["parquet_bytes"] <= 0):
        raise RuntimeError(f"Invalid month manifest: {dataset} {year}-{month}")
    verify_object(s3, bucket, expected_data_key, manifest["parquet_bytes"],
                  manifest["sha256"])
    return manifest


def verify_year(s3, bucket: str, dataset: str, year: int):
    key = f"{prefix(dataset, year)}/manifest.json"
    doc = get_optional_json(s3, bucket, key)
    if doc is None:
        return None
    if (doc.get("format_version") != 1
            or doc.get("dataset") != dataset or doc.get("year") != year
            or not isinstance(doc.get("months"), list)
            or len(doc["months"]) != 12):
        raise RuntimeError(f"Invalid published year manifest: {dataset} {year}")
    complete = []
    for m in range(1, 13):
        verified = verify_month(s3, bucket, dataset, year, f"{m:02d}")
        if verified is None:
            raise RuntimeError(f"Published year has missing month {dataset}/{year}/{m}")
        complete.append(verified)
    if (doc["months"] != complete
            or doc.get("rows") != sum(x["rows"] for x in complete)
            or doc.get("sessions") != sum(x["sessions"] for x in complete)):
        raise RuntimeError(f"Published year totals do not reconcile: {dataset} {year}")
    return doc


def validate_row(dataset: str, record: dict, day: str):
    if not CODE.fullmatch(str(record.get("ts_code") or "")):
        raise RuntimeError(f"Invalid symbol in {dataset} {day}")
    if str(record.get("trade_date")) != day or not DATE.fullmatch(day):
        raise RuntimeError(f"Invalid trade_date in {dataset} {day}")
    for col in REQUIRED_POSITIVE.get(dataset, ()):
        # A sparse genuine source NULL is not a zero or an estimate.
        # Keep missing valuation inputs nullable; report coverage per day.
        if dataset == "daily_basic" and record.get(col) in (None, ""):
            continue
        try:
            val = float(record.get(col))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Missing numeric {dataset}.{col} on {day}") from exc
        if not math.isfinite(val) or val <= 0:
            raise RuntimeError(f"Invalid numeric {dataset}.{col} on {day}")
    if dataset == "stk_limit":
        # Official source may have no price limit for IPO/relisting sessions
        # or incomplete exchange data. Preserve zero/NULL without inventing
        # a valid limit; downstream tradeability must reject these rows.
        numbers = {}
        for col in ("up_limit", "down_limit"):
            raw = record.get(col)
            if raw in (None, ""):
                numbers[col] = None
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Malformed price limit {col} on {day}") from exc
            if not math.isfinite(number) or number < 0:
                raise RuntimeError(f"Invalid price limit {col} on {day}")
            numbers[col] = number
        up, down = numbers["up_limit"], numbers["down_limit"]
        if up is not None and down is not None and up > 0 and down > 0 and up < down:
            raise RuntimeError(f"Crossed limit quotes {day}")
    if dataset == "suspend_d" and record.get("suspend_type") not in ("S", "R"):
        raise RuntimeError(f"Invalid suspension type {day}")
    return tuple(None if record.get(k) == "" and k in REQUIRED_POSITIVE.get(dataset, ())
                 else record.get(k) for k in DATASETS[dataset])


def valuation_quality(day: str, row_count: int, missing_mv: list[str], missing_share: list[str]) -> dict:
    """Describe input availability separately from the integrity of the raw archive."""
    affected = set(missing_mv) | set(missing_share)
    unavailable = len(affected)
    if row_count <= 0 or unavailable > row_count:
        raise RuntimeError(f"Invalid valuation coverage counts on {day}")
    return {
        "trade_date": day, "rows": row_count,
        "missing_total_mv_rows": len(missing_mv),
        "missing_total_share_rows": len(missing_share),
        "unusable_valuation_rows": unavailable,
        "usable_valuation_rows": row_count - unavailable,
        "missing_fraction": unavailable / row_count,
        "quality_status": ("NO_USABLE_VALUATION" if unavailable == row_count else
                           "ELEVATED_MISSINGNESS" if unavailable * 100 > row_count else
                           "PARTIAL" if unavailable else "COMPLETE"),
        "missing_total_mv_codes": sorted(missing_mv),
        "missing_total_share_codes": sorted(missing_share),
    }


def collect_month(client, dataset: str, year: int, month: str, days: list[str],
                  root: Path):
    import duckdb

    root.mkdir(parents=True, exist_ok=True)
    csv_file = root / f"month={month}.csv"
    pq_file = root / f"month={month}.parquet"
    fields = DATASETS[dataset]
    total = 0
    days_nonempty = 0
    unknown_limit_rows = 0
    missing_total_mv_rows = 0
    missing_total_share_rows = 0
    unusable_valuation_rows = 0
    valuation_gap_days = []
    with csv_file.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(fields)
        for day in days:
            records = client.query(dataset, {"trade_date": day},
                                   ",".join(fields))
            if dataset in DAILY_MIN_ROWS and len(records) < DAILY_MIN_ROWS[dataset]:
                raise RuntimeError(f"Incomplete {dataset} {day}: {len(records)} rows")
            if len(records) >= API_CAP[dataset]:
                raise RuntimeError(f"{dataset} {day}: possible provider row truncation")
            seen = set()
            day_missing_mv = []
            day_missing_share = []
            for record in records:
                values = validate_row(dataset, record, day)
                if dataset == "daily_basic":
                    missing_mv = record.get("total_mv") in (None, "")
                    missing_share = record.get("total_share") in (None, "")
                    if missing_mv:
                        day_missing_mv.append(values[0])
                    if missing_share:
                        day_missing_share.append(values[0])
                if dataset == "stk_limit":
                    if any(record.get(col) in (None, "", 0, 0.0)
                           for col in ("up_limit", "down_limit")):
                        unknown_limit_rows += 1
                key = (values[0], values[1]) + (
                    (values[2],) if dataset == "suspend_d" else ()
                )
                if key in seen:
                    raise RuntimeError(f"Duplicated data key: {dataset} {day}")
                seen.add(key)
                writer.writerow(values)
            if dataset == "daily_basic":
                # NULL inputs remain NULL. The raw partition is verifiable even
                # if some or all of the rows are unusable for valuation signals.
                # Keep the 1% threshold as a diagnostic, never a completeness claim.
                quality = valuation_quality(day, len(records), day_missing_mv, day_missing_share)
                missing_total_mv_rows += quality["missing_total_mv_rows"]
                missing_total_share_rows += quality["missing_total_share_rows"]
                unusable_valuation_rows += quality["unusable_valuation_rows"]
                if quality["unusable_valuation_rows"]:
                    valuation_gap_days.append(quality)
                    print(f"PHASE2_VALUATION_GAP date={day} "
                          f"missing_total_mv={quality['missing_total_mv_rows']} "
                          f"missing_total_share={quality['missing_total_share_rows']} "
                          f"unusable_rows={quality['unusable_valuation_rows']} "
                          f"status={quality['quality_status']}", flush=True)
            total += len(records)
            days_nonempty += int(bool(records))
            print(f"PHASE2_DAY dataset={dataset} date={day} rows={len(records)}",
                  flush=True)
    con = duckdb_connection()
    csv_path = str(csv_file).replace("'", "''")
    pq_path = str(pq_file).replace("'", "''")
    select = []
    for field in fields:
        if field == "trade_date":
            select.append("CAST(strptime(trade_date,'%Y%m%d') AS DATE) AS trade_date")
        elif field in TEXT_FIELDS:
            select.append(f"CAST({field} AS VARCHAR) AS {field}")
        else:
            select.append(f"CAST({field} AS DOUBLE) AS {field}")
    try:
        con.execute(
            f"COPY (SELECT {', '.join(select)} FROM "
            f"read_csv_auto('{csv_path}',header=true,all_varchar=true) "
            f"ORDER BY trade_date, ts_code) TO '{pq_path}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        count = con.execute("SELECT COUNT(*) FROM read_parquet(?)",
                            [str(pq_file)]).fetchone()[0]
        if dataset == "daily_basic":
            actual = con.execute(
                "SELECT COUNT(*) FILTER (WHERE total_mv IS NULL), "
                "COUNT(*) FILTER (WHERE total_share IS NULL), "
                "COUNT(*) FILTER (WHERE total_mv IS NULL OR total_share IS NULL) "
                "FROM read_parquet(?)", [str(pq_file)]
            ).fetchone()
            if actual != (missing_total_mv_rows, missing_total_share_rows, unusable_valuation_rows):
                raise RuntimeError(f"Parquet valuation NULL counts mismatch: {year}/{month}")
    finally:
        con.close()
    if count != total:
        raise RuntimeError(f"Parquet row count mismatch: {dataset}/{year}/{month}")
    sha = hashlib.sha256(pq_file.read_bytes()).hexdigest()
    csv_file.unlink()
    return {
        "format_version": 1, "dataset": dataset, "source": "Tushare official",
        "year": year, "month": month, "fields": list(fields),
        "rows": count, "sessions": len(days),
        "nonempty_sessions": days_nonempty,
        "unusable_limit_rows": unknown_limit_rows if dataset == "stk_limit" else None,
        "missing_total_mv_rows": missing_total_mv_rows if dataset == "daily_basic" else None,
        "missing_total_share_rows": missing_total_share_rows if dataset == "daily_basic" else None,
        "unusable_valuation_rows": unusable_valuation_rows if dataset == "daily_basic" else None,
        "valuation_null_policy": "preserve_null_report_coverage_exclude_unusable_inputs" if dataset == "daily_basic" else None,
        "valuation_gap_days": valuation_gap_days if dataset == "daily_basic" else None,
        "usable_valuation_rows": count - unusable_valuation_rows if dataset == "daily_basic" else None,
        "valuation_quality_status": (
            "NO_USABLE_VALUATION" if count == unusable_valuation_rows else
            "PARTIAL" if unusable_valuation_rows else "COMPLETE"
        ) if dataset == "daily_basic" else None,
        "min_session": days[0], "max_session": days[-1],
        "sha256": sha, "parquet_bytes": pq_file.stat().st_size,
        "object_key": f"{prefix(dataset, year)}/month={month}.parquet",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
    }


def archive_year(s3, bucket, client, dataset: str, year: int):
    existing = verify_year(s3, bucket, dataset, year)
    if existing:
        print(f"PHASE2_YEAR year={year} dataset={dataset} "
              f"status=already_verified rows={existing['rows']}", flush=True)
        return existing
    sessions = sessions_for_year(client, year)
    by_month = {}
    for d in sessions:
        by_month.setdefault(d[4:6], []).append(d)
    if len(by_month) != 12:
        raise RuntimeError(f"Unexpected trading calendar for {year}")
    parts = []
    for month, days in sorted(by_month.items()):
        verified = verify_month(s3, bucket, dataset, year, month)
        if verified:
            if verified["sessions"] != len(days):
                raise RuntimeError(f"Calendar mismatch with remote month: {year}/{month}")
            parts.append(verified)
            print(f"PHASE2_MONTH year={year} dataset={dataset} month={month} "
                  "status=reused_verified", flush=True)
            continue
        root = Path(f"artifacts/phase2-temp/{dataset}/{year}")
        item = collect_month(client, dataset, year, month, days, root)
        key = item["object_key"]
        s3.upload_file(
            str(root / f"month={month}.parquet"), bucket, key,
            ExtraArgs={"Metadata": {"sha256": item["sha256"]},
                       "ContentType": "application/octet-stream"},
        )
        verify_object(s3, bucket, key, item["parquet_bytes"], item["sha256"])
        put_manifest(s3, bucket, f"{prefix(dataset, year)}/month={month}.manifest.json",
                     item)
        parts.append(verify_month(s3, bucket, dataset, year, month))
        print(f"PHASE2_MONTH year={year} dataset={dataset} month={month} "
              f"status=uploaded_verified rows={item['rows']}", flush=True)
        shutil.rmtree(root)
    doc = {
        "format_version": 1, "dataset": dataset, "year": year,
        "rows": sum(p["rows"] for p in parts),
        "sessions": sum(p["sessions"] for p in parts),
        "months": parts,
        "research_only": True,
        "coverage_caveat": "Historical ST and financial PIT revisions are separate and not included",
    }
    if doc["sessions"] != len(sessions):
        raise RuntimeError(f"Annual calendar coverage mismatch: {dataset}/{year}")
    put_manifest(s3, bucket, f"{prefix(dataset, year)}/manifest.json", doc)
    result = verify_year(s3, bucket, dataset, year)
    print(f"PHASE2_YEAR year={year} dataset={dataset} "
          f"status=archived_verified rows={result['rows']}", flush=True)
    return result


def snapshot_universe(s3, bucket, client):
    """Today's listed and delisted sets, not a historical point-in-time name table."""
    key = f"{PREFIX}/stock_basic/stock_basic_snapshot.json"
    existing = get_optional_json(s3, bucket, key)
    if existing:
        if existing.get("status") != "current_snapshot_not_historical":
            raise RuntimeError("Invalid published universe snapshot")
        print(f"PHASE2_UNIVERSE status=already_saved rows={existing['rows']}",
              flush=True)
        return
    fields = "ts_code,name,market,list_status,list_date,delist_date"
    all_rows = []
    excluded = []
    for status in ("L", "D"):
        rows = client.query("stock_basic", {"exchange": "", "list_status": status},
                            fields)
        if not rows:
            raise RuntimeError(f"Missing stock_basic status={status}")
        for row in rows:
            if not CODE.fullmatch(str(row.get("ts_code") or "")):
                excluded.append(row)
            else:
                all_rows.append(row)
    # A few legacy/unrecognized codes can exist in the provider's delisted
    # records. Preserve them privately rather than silently dropping them.
    if len(excluded) > 100:
        raise RuntimeError("Too many unrecognized stock symbols in source universe")
    codes = [row["ts_code"] for row in all_rows]
    if len(set(codes)) != len(codes):
        raise RuntimeError("Duplicate universe codes across statuses")
    doc = {
        "format_version": 1, "dataset": "stock_basic_snapshot",
        "source": "Tushare official", "rows": len(all_rows),
        "excluded_unrecognized_rows": len(excluded),
        "excluded_unrecognized_records": excluded,
        "status": "current_snapshot_not_historical",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "records": all_rows,
    }
    put_manifest(s3, bucket, key, doc)
    print(f"PHASE2_UNIVERSE status=saved rows={len(all_rows)} excluded={len(excluded)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2020)
    parser.add_argument("--datasets", default="adj_factor,stk_limit,daily_basic,suspend_d")
    args = parser.parse_args()
    if not (2010 <= args.end_year <= args.start_year < date.today().year):
        parser.error("Expected descending completed years")
    datasets = [d.strip() for d in args.datasets.split(",")]
    if not datasets or len(set(datasets)) != len(datasets) or any(d not in DATASETS for d in datasets):
        parser.error("Unknown or repeated dataset requested")
    config = StorageConfig.from_environment()
    s3 = s3_client(config)
    preflight(s3, config.bucket)
    client = TushareClient.from_environment()
    snapshot_universe(s3, config.bucket, client)
    for year in range(args.start_year, args.end_year - 1, -1):
        for dataset in datasets:
            archive_year(s3, config.bucket, client, dataset, year)
    print("PHASE2_MARKET_BATCH_STATUS all_requested_years_verified", flush=True)


if __name__ == "__main__":
    main()
