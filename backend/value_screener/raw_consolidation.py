"""Non-destructive consolidation of verified historical objects into 01_raw_data.

No provider calls, database changes or source deletion. Each copied data object
is checked against the source bytes and its source manifest when present.
Destination manifests translate only archive object paths; the original source
manifests remain unchanged. Publish the catalog only after every group verifies.
"""
from __future__ import annotations
from .archive_storage import require_neon_only

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
import tempfile
from pathlib import Path

from .neon_archive import StorageConfig, preflight, s3_client, sha256_stream, verify_object
from .phase2_market import put_manifest, get_optional_json

SOURCE = "a-share-backtest/v1/"
DEST = "a-share-backtest/01_raw_data/"
GROUPS = {
    "daily_prices": "prices/",
    "adj_factor": "phase2/adj_factor/",
    "daily_basic": "phase2/daily_basic/",
    "stk_limit": "phase2/stk_limit/",
    "suspend_d": "phase2/suspend_d/",
    "stock_basic": "phase2/universe/",
    "financial": "phase2/financial_snapshot/",
    "disclosures": "phase2/disclosures/",
    "historical_st": "phase2/historical_st/",
}
YEARS = range(2020, 2026)
SHA = re.compile(r"^[a-f0-9]{64}$")


def destination(key):
    for group, old in GROUPS.items():
        prefix = SOURCE + old
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            if not suffix or any(x in ("", ".", "..") for x in suffix.split("/")):
                raise RuntimeError("Invalid archive object path")
            return DEST + group + "/" + suffix
    raise RuntimeError("Unmapped source archive object: " + key)


def translate_manifest(value, price_paths=False):
    if isinstance(value, list):
        return [translate_manifest(v, price_paths) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "object_key" and isinstance(v, str) and v.startswith(SOURCE):
                out[k] = destination(v)
            elif price_paths and k == "path" and isinstance(v, str) and v.startswith("prices/"):
                out[k] = "daily_prices/" + v[len("prices/"):]
            else:
                out[k] = translate_manifest(v, price_paths)
        return out
    return value


def inventory(s3, bucket, prefix):
    items = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/") and item["Size"] == 0:
                continue
            if key in items:
                raise RuntimeError("Duplicate object in inventory")
            items[key] = {"size": int(item["Size"]), "etag": item.get("ETag")}
    return items


def head_optional(s3, bucket, key):
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound") or isinstance(exc, KeyError):
            return None
        raise


def download(s3, bucket, key, path, info):
    head = s3.head_object(Bucket=bucket, Key=key)
    if int(head["ContentLength"]) != info["size"] or (info.get("etag") and head.get("ETag") != info["etag"]):
        raise RuntimeError("Source changed after inventory: " + key)
    response = s3.get_object(Bucket=bucket, Key=key)
    try:
        digest, size = sha256_stream(response["Body"], path)
    finally:
        response["Body"].close()
    meta = (head.get("Metadata") or {}).get("sha256")
    if size != info["size"] or (meta is not None and digest != meta):
        raise RuntimeError("Source size/checksum mismatch: " + key)
    return digest, size, head.get("ContentType") or "application/octet-stream"


def publish_file(s3, bucket, key, path, sha, size, content_type):
    old = head_optional(s3, bucket, key)
    if old is not None:
        # Never overwrite a different destination: an explicit new version is required.
        if int(old["ContentLength"]) != size or (old.get("Metadata") or {}).get("sha256") != sha:
            raise RuntimeError("Destination already exists with different content: " + key)
        verify_object(s3, bucket, key, size, sha)
        return "reused_verified"
    with path.open("rb") as fp:
        s3.put_object(Bucket=bucket, Key=key, Body=fp, ContentLength=size,
                      Metadata={"sha256": sha}, ContentType=content_type)
    verify_object(s3, bucket, key, size, sha)
    return "copied_verified"


def required_keys(group, found):
    base = SOURCE + GROUPS[group]
    result = set()
    if group in ("daily_prices", "adj_factor", "daily_basic", "stk_limit", "suspend_d"):
        for year in YEARS:
            root = f"{base}year={year}/"
            result.add(root + "manifest.json")
            for month in range(1, 13):
                result.add(root + f"month={month:02d}.parquet")
                if group != "daily_prices":
                    result.add(root + f"month={month:02d}.manifest.json")
    elif group == "stock_basic":
        result.add(base + "stock_basic_snapshot.json")
    elif group == "financial":
        for kind in ("balancesheet", "income", "cashflow", "fina_indicator"):
            for start in range(0, 5924, 50):
                stem = f"{base}{kind}/start={start:05d}_count=050"
                result.update((stem + ".jsonl.gz", stem + ".manifest.json"))
    elif group == "disclosures":
        for year in YEARS:
            for md in ("0331", "0630", "0930", "1231"):
                stem = f"{base}period={year}{md}"
                result.update((stem + ".jsonl.gz", stem + ".manifest.json"))
    elif group == "historical_st":
        pattern = re.compile(re.escape(base) + r"v2/range=2020-2025/universe=[a-f0-9]{64}/merged/manifest\.json$")
        merged = [k for k in found if pattern.fullmatch(k)]
        if len(merged) != 1:
            raise RuntimeError("Expected one published 2020-2025 merged ST namespace")
        ns = merged[0].removesuffix("merged/manifest.json")
        for source in ("baostock", "namechange", "merged"):
            root = ns + source + "/"
            result.add(root + "manifest.json")
            for year in YEARS:
                result.add(root + f"year={year}/manifest.json")
                for month in range(1, 13):
                    stem = root + f"year={year}/month={month:02d}.parquet"
                    result.update((stem, stem + ".manifest.json"))
            if source != "merged":
                for index in range(284):
                    stem = root + f"raw/batch={index:05d}.json.gz"
                    result.update((stem, stem + ".manifest.json"))
        result.add(ns + "audit/baostock-coverage.json")
    missing = result - set(found)
    if missing:
        raise RuntimeError(f"Missing required {group} archive objects: {len(missing)}; first={sorted(missing)[:3]}")
    return result


def references(doc, price_paths=False):
    """Extract data-object checksums and sizes from nested source manifests."""
    if isinstance(doc, list):
        for value in doc:
            yield from references(value, price_paths)
    elif isinstance(doc, dict):
        key = doc.get("object_key")
        if not key and price_paths and isinstance(doc.get("path"), str) and doc["path"].startswith("prices/"):
            key = SOURCE + doc["path"]
        if isinstance(key, str) and key.startswith(SOURCE):
            sha = doc.get("sha256")
            size = next((doc[k] for k in ("bytes", "parquet_bytes", "compressed_bytes") if k in doc), None)
            if not isinstance(sha, str) or not SHA.fullmatch(sha) or type(size) is not int or size < 0:
                raise RuntimeError("Invalid referenced data object metadata: " + key)
            yield key, sha, size
        for value in doc.values():
            if isinstance(value, (dict, list)):
                yield from references(value, price_paths)


def receipt_key(group):
    return DEST + "_inventory/" + group + ".json"


def migrate_group(s3, bucket, group):
    found = inventory(s3, bucket, SOURCE + GROUPS[group])
    required = required_keys(group, found)
    # Data first, then child manifests, then annual/root manifests.
    ordered = sorted(found, key=lambda k: (k.endswith(".json"), -k.count("/"), k))
    entries, refs, modes = {}, {}, Counter()
    with tempfile.TemporaryDirectory(prefix="raw-consolidation-") as tmp:
        path = Path(tmp) / "object"
        for key in ordered:
            sha, size, ct = download(s3, bucket, key, path, found[key])
            target = destination(key)
            if key.endswith(".json"):
                doc = json.loads(path.read_bytes())
                for ref, digest, length in references(doc, group == "daily_prices"):
                    if ref not in found:
                        raise RuntimeError("Manifest points outside inventory: " + ref)
                    if ref in refs and refs[ref] != (digest, length):
                        raise RuntimeError("Conflicting source manifest checksums: " + ref)
                    refs[ref] = (digest, length)
                translated = translate_manifest(doc, group == "daily_prices")
                if translated != doc:
                    path.write_bytes(json.dumps(translated, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode("utf-8"))
                    ct = "application/json"
            with path.open("rb") as fp:
                dest_sha, dest_size = sha256_stream(fp)
            mode = publish_file(s3, bucket, target, path, dest_sha, dest_size, ct)
            modes[mode] += 1
            entries[key] = {"source_key": key, "source_sha256": sha, "source_bytes": size,
                            "source_etag": found[key]["etag"], "object_key": target,
                            "sha256": dest_sha, "bytes": dest_size}
            if len(entries) % 50 == 0:
                print(f"RAW_GROUP_PROGRESS dataset={group} verified={len(entries)} total={len(found)}", flush=True)
    for key, (digest, length) in refs.items():
        if (entries[key]["source_sha256"], entries[key]["source_bytes"]) != (digest, length):
            raise RuntimeError("Data does not match archived source manifest: " + key)
    required_data = {k for k in required if not k.endswith(".json")}
    if not required_data.issubset(refs):
        raise RuntimeError("Required binary data lacks source manifest references")
    current = inventory(s3, bucket, SOURCE + GROUPS[group])
    if current != found:
        raise RuntimeError("Source inventory changed during consolidation")
    doc = {"dataset": group, "status": "copied_and_verified", "objects": len(entries),
           "required_objects": len(required), "source_bytes": sum(e["source_bytes"] for e in entries.values()),
           "bytes": sum(e["bytes"] for e in entries.values()), "entries": list(entries.values()),
           "unreferenced_binary_objects": sorted(k for k in found if not k.endswith(".json") and k not in refs),
           "captured_at_utc": datetime.now(timezone.utc).isoformat()}
    put_manifest(s3, bucket, receipt_key(group), doc)
    print(f"RAW_GROUP_STATUS dataset={group} objects={len(entries)} bytes={doc['bytes']} modes={dict(modes)}", flush=True)


def publish_catalog(s3, bucket):
    whole = inventory(s3, bucket, SOURCE)
    mapped = {k for k in whole if not k.startswith(SOURCE + "_healthcheck/")}
    # Unknown prefixes must not silently disappear from the consolidated archive.
    for key in mapped:
        destination(key)
    all_keys, groups = set(), []
    for group in GROUPS:
        receipt = get_optional_json(s3, bucket, receipt_key(group))
        if not receipt or receipt.get("dataset") != group or receipt.get("status") != "copied_and_verified":
            raise RuntimeError("Missing verified group receipt: " + group)
        entries = receipt["entries"]
        actual = {k for k in mapped if k.startswith(SOURCE + GROUPS[group])}
        if len(entries) != len(actual) or {e["source_key"] for e in entries} != actual:
            raise RuntimeError("Receipt no longer matches source inventory: " + group)
        required_keys(group, actual)
        for e in entries:
            key = e["source_key"]
            if e["source_bytes"] != whole[key]["size"] or e["source_etag"] != whole[key]["etag"]:
                raise RuntimeError("Source changed since group verification: " + key)
            if destination(key) != e["object_key"]:
                raise RuntimeError("Invalid consolidated reference")
            head = s3.head_object(Bucket=bucket, Key=e["object_key"])
            if int(head["ContentLength"]) != e["bytes"] or (head.get("Metadata") or {}).get("sha256") != e["sha256"]:
                raise RuntimeError("Consolidated object changed after verification")
        all_keys.update(actual)
        groups.append({"dataset": group, "prefix": DEST + group + "/", "inventory": receipt_key(group),
                       "objects": len(entries), "source_bytes": receipt["source_bytes"], "bytes": receipt["bytes"],
                       "unreferenced_binary_objects": len(receipt["unreferenced_binary_objects"])})
    if all_keys != mapped:
        raise RuntimeError("Consolidated source inventory mismatch")
    doc = {"format_version": 1, "status": "consolidated_verified", "source_prefix": SOURCE,
           "raw_prefix": DEST, "legacy_source_retained": True, "readers_switched": False,
           "objects": len(all_keys), "source_bytes": sum(g["source_bytes"] for g in groups),
           "bytes": sum(g["bytes"] for g in groups), "datasets": groups,
           "captured_at_utc": datetime.now(timezone.utc).isoformat()}
    put_manifest(s3, bucket, DEST + "dataset_catalog.json", doc)
    print("RAW_CATALOG_STATUS " + json.dumps({k: doc[k] for k in ("status", "objects", "source_bytes", "bytes")}), flush=True)


def main():
    require_neon_only()
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=tuple(GROUPS) + ("catalog",), required=True)
    args = parser.parse_args()
    cfg = StorageConfig.from_environment()
    s3 = s3_client(cfg)
    preflight(s3, cfg.bucket)
    if args.group == "catalog":
        publish_catalog(s3, cfg.bucket)
    else:
        migrate_group(s3, cfg.bucket, args.group)


if __name__ == "__main__":
    main()
