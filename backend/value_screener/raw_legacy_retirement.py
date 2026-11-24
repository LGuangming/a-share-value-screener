"""Retire only the exact verified duplicate legacy inventory; never delete new data."""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from .raw_consolidation import SOURCE, DEST, GROUPS, destination, inventory, download, publish_file, receipt_key
from .neon_archive import StorageConfig, s3_client, preflight, verify_object
from .phase2_market import get_optional_json, put_manifest

PLAN = DEST + "_inventory/legacy_retirement_plan.json"
BACKUP = DEST + "_inventory/legacy_original_manifests.json.gz"
DONE = DEST + "_inventory/legacy_retirement.json"


def web_gate():
    origin = "https://stock.guangming.blog"
    checks = {
        "/api/quotes/600000.SH?year=2025": "bars",
        "/api/quotes/600000.SH/factors?year=2025": "factors",
        "/api/research/600000.SH/financial": "points",
    }
    for route, field in checks.items():
        with urlopen(origin + route, timeout=75) as response:
            doc = json.load(response)
            if response.status != 200 or doc.get("archive_prefix") != DEST.rstrip("/") or not doc.get(field):
                raise RuntimeError("Production archive API gate failed: " + field)
            if field == "points" and (len(doc.get("sources", [])) != 4 or any(s.get("state") != "ready" for s in doc["sources"])):
                raise RuntimeError("Production financial sources incomplete")
        print(f"LEGACY_WEB_GATE endpoint={field} status=passed", flush=True)
    with urlopen(origin + "/api/health", timeout=30) as response:
        doc = json.load(response)
        if response.status != 200 or doc.get("status") != "ok":
            raise RuntimeError("Production health gate failed")


def expected_entries(s3, bucket):
    catalog = get_optional_json(s3, bucket, DEST + "dataset_catalog.json")
    if not catalog or catalog.get("status") != "consolidated_verified" or catalog.get("raw_prefix") != DEST:
        raise RuntimeError("Missing verified consolidation catalog")
    groups = {g["dataset"]: g for g in catalog["datasets"]}
    if set(groups) != set(GROUPS):
        raise RuntimeError("Unexpected catalog datasets")
    entries = {}
    for group, summary in groups.items():
        if summary["inventory"] != receipt_key(group):
            raise RuntimeError("Unexpected inventory pointer")
        receipt = get_optional_json(s3, bucket, receipt_key(group))
        if not receipt or receipt["status"] != "copied_and_verified" or receipt["dataset"] != group or len(receipt["entries"]) != summary["objects"]:
            raise RuntimeError("Invalid consolidation receipt")
        for e in receipt["entries"]:
            key = e["source_key"]
            if key in entries or not key.startswith(SOURCE + GROUPS[group]) or e["object_key"] != destination(key):
                raise RuntimeError("Unexpected duplicate or destination")
            if not key.endswith(".json") and (e["sha256"], e["bytes"]) != (e["source_sha256"], e["source_bytes"]):
                raise RuntimeError("Binary source/destination mismatch")
            entries[key] = e
    if len(entries) != catalog["objects"] or sum(e["source_bytes"] for e in entries.values()) != catalog["source_bytes"]:
        raise RuntimeError("Inventory totals do not reconcile")
    return entries


def validate_remaining(found, entries, resume):
    unexpected = set(found) - set(entries)
    missing = set(entries) - set(found)
    if unexpected or (missing and not resume):
        raise RuntimeError(f"Legacy inventory differs: unexpected={len(unexpected)} missing={len(missing)}")
    for key, info in found.items():
        e = entries[key]
        if info["size"] != e["source_bytes"] or info["etag"] != e["source_etag"]:
            raise RuntimeError("Legacy source changed: " + key)


def retire(s3, bucket):
    entries = expected_entries(s3, bucket)
    found = inventory(s3, bucket, SOURCE)
    old_plan = get_optional_json(s3, bucket, PLAN)
    old_done = get_optional_json(s3, bucket, DONE)
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    if old_plan and (old_plan.get("inventory_sha256") != digest or old_plan.get("objects") != len(entries)):
        raise RuntimeError("Retirement plan no longer matches migration inventory")
    validate_remaining(found, entries, bool(old_plan))
    if old_done:
        if found or not old_plan or old_done.get("inventory_sha256") != digest:
            raise RuntimeError("Completed retirement does not match inventory")
        verify_object(s3, bucket, BACKUP, old_plan["backup_bytes"], old_plan["backup_sha256"])
        print("LEGACY_RETIREMENT status=already_completed", flush=True)
        return
    # Verify every destination byte before deleting ANY source object.
    originals = {}
    with tempfile.TemporaryDirectory(prefix="legacy-retire-") as tmp:
        path = Path(tmp) / "object"
        for i, (key, e) in enumerate(sorted(entries.items()), 1):
            verify_object(s3, bucket, e["object_key"], e["bytes"], e["sha256"])
            if key in found:
                sha, size, _ = download(s3, bucket, key, path, found[key])
                if (sha, size) != (e["source_sha256"], e["source_bytes"]):
                    raise RuntimeError("Source content changed since migration")
                if key.endswith(".json") and e["sha256"] != sha and not old_plan:
                    originals[key] = base64.b64encode(path.read_bytes()).decode("ascii")
            if i % 250 == 0:
                print(f"LEGACY_VERIFY verified={i} total={len(entries)}", flush=True)
        if old_plan:
            verify_object(s3, bucket, BACKUP, old_plan["backup_bytes"], old_plan["backup_sha256"])
        else:
            payload = gzip.compress(json.dumps(originals, sort_keys=True, separators=(",", ":")).encode(), mtime=0)
            path.write_bytes(payload)
            backup_sha = hashlib.sha256(payload).hexdigest()
            publish_file(s3, bucket, BACKUP, path, backup_sha, len(payload), "application/gzip")
            old_plan = {"status":"all_copies_verified_before_delete", "inventory_sha256":digest,
                        "objects":len(entries), "source_bytes":sum(e["source_bytes"] for e in entries.values()),
                        "backup_key":BACKUP, "backup_sha256":backup_sha, "backup_bytes":len(payload),
                        "original_manifests_preserved":len(originals)}
            put_manifest(s3, bucket, PLAN, old_plan)
    if inventory(s3, bucket, SOURCE) != found:
        raise RuntimeError("Legacy inventory changed during pre-deletion verification")
    # Check live readers again immediately before destructive operations.
    web_gate()
    for i, key in enumerate(sorted(found), 1):
        head = s3.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != found[key]["size"] or head.get("ETag") != found[key]["etag"]:
            raise RuntimeError("Source changed immediately before deletion")
        s3.delete_object(Bucket=bucket, Key=key)
        if i % 250 == 0:
            print(f"LEGACY_DELETE deleted={i} remaining_batch={len(found)-i}", flush=True)
    remaining = inventory(s3, bucket, SOURCE)
    if remaining:
        raise RuntimeError(f"Legacy retirement incomplete: {len(remaining)} objects")
    result = {"status":"retired_verified", "inventory_sha256":digest, "legacy_source_retained":False,
              "deleted_objects":len(entries), "deleted_source_bytes":old_plan["source_bytes"],
              "original_manifest_backup":BACKUP, "backup_bytes":old_plan["backup_bytes"],
              "completed_at_utc":datetime.now(timezone.utc).isoformat()}
    put_manifest(s3, bucket, DONE, result)
    print("LEGACY_RETIREMENT " + json.dumps(result,sort_keys=True), flush=True)


def main():
    web_gate()
    cfg = StorageConfig.from_environment()
    s3 = s3_client(cfg)
    preflight(s3,cfg.bucket)
    retire(s3,cfg.bucket)


if __name__ == "__main__":
    main()
