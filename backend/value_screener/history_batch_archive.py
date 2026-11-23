"""Sequential, one-time historical OHLCV archive, newest completed year first.

Published yearly archives are verified and reused without Tushare requests.
An incomplete or unverifiable published archive is a hard failure: never
silently overwrite a published dataset or proceed to the next year.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import shutil

from .backtest_smoke import smoke
from .history_parquet import export_year
from .neon_archive import (
    StorageConfig, _remote_manifest, local_manifest, preflight,
    s3_client, upload_archive, verify_archive,
)
from .tushare_client import TushareClient


def validate_restored_data(root: Path, manifest: dict) -> None:
    """Cross-check the complete restored year, not just four smoke-test symbols."""
    import duckdb

    paths = [str(root / item["path"]) for item in manifest["partition_files"]]
    con = duckdb.connect()
    try:
        rows, unique_keys, sessions, invalid_bars = con.execute(
            """
            SELECT COUNT(*),
                   COUNT(DISTINCT ts_code || ':' || CAST(trade_date AS VARCHAR)),
                   COUNT(DISTINCT trade_date),
                   SUM(CASE WHEN open <= 0 OR high <= 0 OR low <= 0
                                      OR close <= 0 OR high < open OR high < close
                                      OR low > open OR low > close OR vol < 0
                                      OR amount < 0 OR ts_code IS NULL
                                      OR trade_date IS NULL
                                 THEN 1 ELSE 0 END)
              FROM read_parquet(?)
            """,
            [paths],
        ).fetchone()
    finally:
        con.close()
    if (rows != manifest["rows"] or unique_keys != rows
            or sessions != manifest["sessions"] or invalid_bars != 0):
        raise RuntimeError(
            f'Restored data failed full-year checks: year={manifest["year"]}, '
            f'rows={rows}, unique_keys={unique_keys}, sessions={sessions}, '
            f'invalid_bars={invalid_bars}'
        )
    print(
        "ARCHIVE_FULL_DATA_CHECK " + json.dumps({
            "year": manifest["year"], "rows": rows,
            "unique_keys": unique_keys, "sessions": sessions,
            "invalid_bars": invalid_bars, "result": "passed",
        }, sort_keys=True),
        flush=True,
    )


def batch_archive(start_year: int = 2025, end_year: int = 2020) -> None:
    if not (2010 <= end_year <= start_year < date.today().year):
        raise ValueError("A descending range of completed years is required")

    config = StorageConfig.from_environment()
    client = s3_client(config)
    preflight(client, config.bucket)
    tushare = None
    reports = Path("artifacts/manifests")
    reports.mkdir(parents=True, exist_ok=True)

    for year in range(start_year, end_year - 1, -1):
        print(f"ARCHIVE_YEAR_START year={year}", flush=True)

        existing = _remote_manifest(client, config.bucket, year)
        if existing is not None:
            manifest = verify_archive(client, config.bucket, year)
            (reports / f"{year}.json").write_bytes(existing[1])
            print(
                f"ARCHIVE_YEAR_STATUS year={year} state=already_verified "
                f"rows={manifest['rows']} no_tushare_calls=true",
                flush=True,
            )
            continue

        if tushare is None:
            tushare = TushareClient.from_environment()
        source_root = Path(f"artifacts/history-{year}")
        restored_root = Path(f"artifacts/restored-{year}")

        manifest = export_year(tushare, year, source_root)
        local_manifest(source_root, year)
        smoke(source_root)

        # The remote yearly manifest is committed last by upload_archive().
        upload_archive(client, config.bucket, year, source_root)
        restored = verify_archive(client, config.bucket, year, restored_root)
        local_manifest(restored_root, year)
        validate_restored_data(restored_root, restored)
        smoke(restored_root)

        (reports / f"{year}.json").write_bytes(
            (restored_root / "manifest.json").read_bytes()
        )
        print(
            f"ARCHIVE_YEAR_STATUS year={year} state=archived_restored_verified "
            f"rows={manifest['rows']} partitions=12",
            flush=True,
        )
        # Keep only metadata in GitHub Artifact. Full quotes remain private.
        shutil.rmtree(source_root)
        shutil.rmtree(restored_root)

    print(
        f"ARCHIVE_BATCH_STATUS state=all_years_verified "
        f"start_year={start_year} end_year={end_year}", flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequential private historical archive")
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2020)
    args = parser.parse_args()
    batch_archive(args.start_year, args.end_year)


if __name__ == "__main__":
    main()
