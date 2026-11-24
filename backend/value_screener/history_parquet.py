"""Fetch one historical calendar year into locally compressed Parquet partitions.

This module deliberately does not touch Neon. Verified dataset checksums can
later be uploaded to a private S3-compatible object bucket. Without storage
credentials the GitHub artifact is TEMPORARY and must not be treated as archival.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re

from .tushare_client import TushareClient

FIELDS = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]


def valid_daily(row: dict, day: str) -> tuple | None:
    code = str(row.get("ts_code") or "")
    if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        return None
    if str(row.get("trade_date")) != day:
        return None
    try:
        values = tuple(float(row[k]) for k in FIELDS[2:])
    except (KeyError, ValueError, TypeError):
        return None
    from math import isfinite
    if not all(isfinite(v) for v in values):
        return None
    op, hi, lo, close, vol, amount = values
    if min(op, hi, lo, close) <= 0 or hi < max(op, close) or lo > min(op, close):
        return None
    if vol < 0 or amount < 0:
        return None
    return (code, day, *values)


def sessions_for_year(client: TushareClient, year: int) -> list[str]:
    sessions = client.query("trade_cal", {
        "exchange": "SSE",
        "start_date": f"{year}0101",
        "end_date": f"{year}1231"
    }, "cal_date,is_open")
    if not sessions:
        raise RuntimeError(f"No trade calendar rows for {year}")
    days = sorted({str(r["cal_date"]) for r in sessions
                   if str(r.get("is_open")) == "1"
                   and re.fullmatch(r"\d{8}", str(r.get("cal_date")))})
    if not 180 <= len(days) <= 260:
        raise RuntimeError(f"Unexpected SSE session count {len(days)} for {year}")
    return days


def export_year(client: TushareClient, year: int, root: Path,
                min_quotes: int = 3000) -> dict:
    import duckdb  # optional dependency in the one-off backtest job only

    if not 2010 <= year < date.today().year:
        raise ValueError("Export only completed historical years (2010 through last year)")
    if not 1000 <= min_quotes <= 6000:
        raise ValueError("min_quotes must be between 1000 and 6000")
    days = sessions_for_year(client, year)
    base = root / "daily_prices" / f"year={year}"
    base.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    manifest = {
        "format_version": 1, "source": "Tushare official daily OHLCV",
        "adjustment": "unadjusted", "dataset": "a_share_daily",
        "year": year, "expected_sessions": len(days),
        "sessions": 0, "rows": 0, "csv_bytes": 0, "parquet_bytes": 0,
        "partition_files": [],
        "suitability": "Engineering/data-size validation only; not a survivorship-safe Net-Net return series",
    }
    by_month: dict[str,list[str]] = {}
    for day in days:
        by_month.setdefault(day[:6], []).append(day)
    try:
        for month, month_days in sorted(by_month.items()):
            month_name = month[-2:]
            csv_file = base / f"month={month_name}.tmp.csv"
            parquet_file = base / f"month={month_name}.parquet"
            rows_written = 0
            with csv_file.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(FIELDS)
                for day in month_days:
                    rows = client.query("daily", {"trade_date": day}, ",".join(FIELDS))
                    seen = set()
                    valid_rows = []
                    for record in rows:
                        item = valid_daily(record, day)
                        if item and item[0] not in seen:
                            valid_rows.append(item)
                            seen.add(item[0])
                    if len(valid_rows) < min_quotes:
                        raise RuntimeError(
                            f"{day}: incomplete daily batch, {len(valid_rows)} valid rows "
                            f"(< {min_quotes}); export not complete"
                        )
                    if len(rows) >= 6000:
                        raise RuntimeError(f"{day}: provider result reached 6000-row cap; investigate pagination")
                    writer.writerows(valid_rows)
                    rows_written += len(valid_rows)
                    manifest["sessions"] += 1
                    manifest["rows"] += len(valid_rows)
                    print(f"HISTORY_DAY {day} count={len(valid_rows)}", flush=True)
            # Stable ISO dates, typed numeric columns, and year/month partition.
            csv_sql = str(csv_file).replace("'", "''")
            parquet_sql = str(parquet_file).replace("'", "''")
            con.execute(f"""
                COPY (
                    SELECT ts_code,
                           CAST(strptime(trade_date,'%Y%m%d') AS DATE) AS trade_date,
                           CAST(open AS DOUBLE) AS open,
                           CAST(high AS DOUBLE) AS high,
                           CAST(low AS DOUBLE) AS low,
                           CAST(close AS DOUBLE) AS close,
                           CAST(vol AS DOUBLE) AS vol,
                           CAST(amount AS DOUBLE) AS amount
                    FROM read_csv_auto('{csv_sql}', header=true, all_varchar=true)
                    ORDER BY ts_code, trade_date
                ) TO '{parquet_sql}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """)
            count = con.execute("SELECT COUNT(*) FROM read_parquet(?)",
                                [str(parquet_file)]).fetchone()[0]
            if count != rows_written:
                raise RuntimeError(f"{month}: Parquet verification {count} != {rows_written}")
            csv_bytes = csv_file.stat().st_size
            pq_bytes = parquet_file.stat().st_size
            sha = hashlib.sha256(parquet_file.read_bytes()).hexdigest()
            manifest["csv_bytes"] += csv_bytes
            manifest["parquet_bytes"] += pq_bytes
            manifest["partition_files"].append({
                "path": str(parquet_file.relative_to(root)),
                "month": month_name, "rows": count,
                "sessions": len(month_days), "bytes": pq_bytes, "sha256": sha,
                "min_session": month_days[0], "max_session": month_days[-1],
            })
            csv_file.unlink()
            print(f"HISTORY_MONTH {month} sessions={len(month_days)} rows={count} "
                  f"parquet_bytes={pq_bytes} csv_bytes={csv_bytes}", flush=True)
        if manifest["sessions"] != manifest["expected_sessions"]:
            raise RuntimeError("Not all expected sessions were exported")
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        print("HISTORY_ARCHIVE_SUMMARY " + json.dumps({
            "year":year,"sessions":manifest["sessions"],"rows":manifest["rows"],
            "parquet_bytes":manifest["parquet_bytes"],"csv_bytes":manifest["csv_bytes"],
            "partitions":len(manifest["partition_files"]),
            "ratio":round(manifest["parquet_bytes"]/max(manifest["csv_bytes"],1),4),
            "storage":"LOCAL_ONLY_UNTIL_EXPLICIT_PRIVATE_ARCHIVE_UPLOAD",
        },ensure_ascii=False),flush=True)
        return manifest
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Yearly official historical OHLCV to Parquet")
    parser.add_argument("--year",type=int,default=2025)
    parser.add_argument("--out",default="artifacts/history-2025")
    parser.add_argument("--min-quotes",type=int,default=3000)
    args=parser.parse_args()
    root=Path(args.out)
    export_year(TushareClient.from_environment(),args.year,root,args.min_quotes)
    print("HISTORY_EXPORT_STATUS verified_local_only",flush=True)


if __name__=="__main__":
    main()
