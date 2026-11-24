"""Verify consolidated objects from the new namespace only. Never delete legacy data."""
from __future__ import annotations
import argparse
import tempfile
from pathlib import Path
from .archive_paths import RAW_PREFIX
from .neon_archive import StorageConfig, s3_client, preflight, verify_object, verify_archive
from .phase2_market import get_optional_json
from .phase2_financial import universe_codes
from .history_batch_archive import validate_restored_data
from .backtest_smoke import smoke

GROUPS = ("daily_prices", "adj_factor", "daily_basic", "stk_limit", "suspend_d",
          "stock_basic", "financial", "disclosures", "historical_st")


def verify_group(s3, bucket, group):
    catalog = get_optional_json(s3, bucket, f"{RAW_PREFIX}/dataset_catalog.json")
    if not catalog or catalog.get("status") != "consolidated_verified" or catalog.get("raw_prefix") != RAW_PREFIX + "/":
        raise RuntimeError("Missing validated raw catalog")
    entries = [g for g in catalog["datasets"] if g["dataset"] == group]
    if len(entries) != 1:
        raise RuntimeError("Invalid catalog group")
    g = entries[0]
    receipt = get_optional_json(s3, bucket, g["inventory"])
    if not receipt or receipt["status"] != "copied_and_verified" or receipt["dataset"] != group or len(receipt["entries"]) != g["objects"]:
        raise RuntimeError("Invalid group receipt")
    seen = set()
    for e in receipt["entries"]:
        key = e["object_key"]
        if not key.startswith(f"{RAW_PREFIX}/{group}/") or key in seen:
            raise RuntimeError("Unexpected destination in receipt")
        seen.add(key)
        verify_object(s3, bucket, key, e["bytes"], e["sha256"])
    print(f"RAW_CUTOVER_GROUP dataset={group} objects={len(seen)} status=verified_new_namespace", flush=True)


def check_backtest(s3, bucket):
    codes = universe_codes(s3, bucket)
    if len(codes) < 5924:
        raise RuntimeError("Frozen universe unexpectedly smaller")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        doc = verify_archive(s3, bucket, 2025, root)
        validate_restored_data(root, doc)
        smoke(root)
    print(f"RAW_CUTOVER_BACKTEST status=engineering_pass universe={len(codes)}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=GROUPS + ("backtest",), required=True)
    args = parser.parse_args()
    cfg = StorageConfig.from_environment()
    s3 = s3_client(cfg)
    preflight(s3, cfg.bucket)
    if args.group == "backtest":
        check_backtest(s3, cfg.bucket)
    else:
        verify_group(s3, cfg.bucket, args.group)

if __name__ == "__main__":
    main()
