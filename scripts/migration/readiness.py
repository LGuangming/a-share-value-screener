"""Fail-closed checklist for opening the paid migration window.
Operator attestations are NOT substitutes for independently reviewed evidence.
"""
import argparse
from pathlib import Path
from common import digest, load, main_guard

REQUIRED = {
    "postgres_17_18_restore", "s3_full_inventory_sync", "archive_parquet_manifest",
    "vps_capacity_permissions", "vps_isolated_database", "vps_restricted_operations",
    "source_credentials_scope", "bucket_scope_confirmed", "application_security",
    "application_test_deployment", "business_regression", "cutover_rollback",
    "neon_billing_downgrade", "final_write_freeze_plan",
}

def validate(root):
    root=Path(root)
    evidence=load(root/"readiness.json")
    if set(evidence)!=REQUIRED:
        raise ValueError("checklist incomplete or unexpected")
    for name,item in evidence.items():
        if item.get("approved") is not True or not item.get("reviewer") or not item.get("reviewed_at"):
            raise ValueError("missing review")
        path=root/(name+".json")
        if path.is_symlink() or not path.is_file() or digest(path)!=item.get("sha256"):
            raise ValueError("missing or altered evidence")
        if load(path).get("passed") is not True:
            raise ValueError("evidence did not pass")
    print("All supplied evidence files and attestations verified; separate Launch approval still required.")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True)
    validate(p.parse_args().root)

if __name__=="__main__":
    main_guard(main)
