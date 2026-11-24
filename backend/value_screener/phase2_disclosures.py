"""Archive financial report disclosure-calendar evidence without backdating releases.

Use official Tushare disclosure_date by reporting period. This dataset is
supplementary evidence and DOES NOT establish a complete correction-event stream.
Private source rows live only in Neon Object Storage, never as GitHub artifacts.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from .neon_archive import StorageConfig, preflight, s3_client, verify_object
from .phase2_market import get_optional_json, put_manifest
from .tushare_client import TushareClient

from .archive_paths import RAW_PREFIX
PREFIX = f"{RAW_PREFIX}/disclosures"
FIELDS = ("ts_code", "end_date", "actual_date", "ann_date", "pre_date")


def reporting_periods(start_year=2020, end_year=2025):
    if not (2010 <= start_year <= end_year <= 2025):
        raise ValueError("Invalid reporting period span")
    return [f"{y}{md}" for y in range(start_year, end_year + 1)
            for md in ("0331", "0630", "0930", "1231")]


def archive_disclosures(s3, bucket, client, periods):
    for period in periods:
        key = f"{PREFIX}/period={period}.jsonl.gz"
        manifest_key = f"{PREFIX}/period={period}.manifest.json"
        existing = get_optional_json(s3, bucket, manifest_key)
        if existing:
            if (existing.get("source") != "Tushare official disclosure_date"
                    or existing.get("report_period") != period
                    or existing.get("object_key") != key
                    or existing.get("status") != "source_evidence_only"):
                raise RuntimeError(f"Unexpected preexisting disclosure manifest {period}")
            verify_object(s3, bucket, key,
                          existing["compressed_bytes"], existing["sha256"])
            print(f"PHASE2_DISCLOSURE period={period} status=reused_verified "
                  f"rows={existing['rows']}", flush=True)
            continue
        rows = client.query("disclosure_date", {"end_date": period},
                            ",".join(FIELDS))
        if len(rows) < 1500 or len(rows) >= 6000:
            raise RuntimeError(f"Abnormal disclosure_date batch size {period}: {len(rows)}")
        valid_actual = 0
        for row in rows:
            if str(row.get("end_date")) != period or not row.get("ts_code"):
                raise RuntimeError(f"Invalid disclosure row for {period}")
            date = row.get("actual_date")
            if date not in (None, ""):
                if len(str(date)) != 8 or not str(date).isdigit():
                    raise RuntimeError(f"Invalid actual_date for {period}")
                valid_actual += 1
        payload = b"".join(
            json.dumps(row, ensure_ascii=False,
                       separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
            for row in rows
        )
        compressed = gzip.compress(payload, compresslevel=6, mtime=0)
        checksum = hashlib.sha256(compressed).hexdigest()
        s3.put_object(Bucket=bucket, Key=key, Body=compressed,
                      Metadata={"sha256": checksum},
                      ContentType="application/gzip")
        verify_object(s3, bucket, key, len(compressed), checksum)
        info = {
            "format_version": 1,
            "source": "Tushare official disclosure_date",
            "report_period": period,
            "object_key": key,
            "sha256": checksum,
            "compressed_bytes": len(compressed),
            "rows": len(rows),
            "actual_date_populated": valid_actual,
            "actual_date_missing": len(rows) - valid_actual,
            "status": "source_evidence_only",
            "disclaimer": "Not a complete original announcement/correction event history",
        }
        put_manifest(s3, bucket, manifest_key, info)
        print(f"PHASE2_DISCLOSURE period={period} status=uploaded_verified "
              f"rows={len(rows)} actual_date_populated={valid_actual}", flush=True)
    print(f"PHASE2_DISCLOSURE_BATCH_STATUS verified_periods={len(periods)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    periods = reporting_periods(args.start_year, args.end_year)
    config = StorageConfig.from_environment()
    s3 = s3_client(config)
    preflight(s3, config.bucket)
    archive_disclosures(s3, config.bucket,
                        TushareClient.from_environment(), periods)


if __name__ == "__main__":
    main()
