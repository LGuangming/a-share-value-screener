"""Offline storage integration tests: fake S3 can read, write, verify, corrupt."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from value_screener.neon_archive import (
    StorageConfig, local_manifest, preflight, upload_archive, validate_manifest,
    verify_archive, verify_object,
)


class FakeS3:
    def __init__(self):
        self.objects={}
        self.uploads=0

    def put_object(self, Bucket, Key, Body, Metadata=None, ContentType=None):
        self.objects[(Bucket,Key)]=(bytes(Body), dict(Metadata or {}))
        self.uploads+=1

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.put_object(Bucket,Key,Path(Filename).read_bytes(),
                        Metadata=(ExtraArgs or {}).get("Metadata"))

    def head_object(self, Bucket, Key):
        body,metadata=self.objects[(Bucket,Key)]
        return {"ContentLength":len(body),"Metadata":metadata}

    def get_object(self, Bucket, Key):
        body,_=self.objects[(Bucket,Key)]
        return {"Body":io.BytesIO(body)}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket,Key),None)


def build_archive(root,year=2025):
    base=root/"daily_prices"/f"year={year}"
    base.mkdir(parents=True)
    parts=[]
    for month in range(1,13):
        payload=b"PAR1"+b"sample-"+f"{year}-{month}".encode("ascii")
        file=base/f"month={month:02d}.parquet"
        file.write_bytes(payload)
        parts.append({
            "path":f"daily_prices/year={year}/month={month:02d}.parquet",
            "month":f"{month:02d}","rows":50000,"sessions":20,
            "bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest(),
            "min_session":f"{year}{month:02d}01",
            "max_session":f"{year}{month:02d}28",
        })
    manifest={
        "format_version":1,"dataset":"a_share_daily",
        "adjustment":"unadjusted","year":year,
        "partition_files":parts,"rows":600000,
        "parquet_bytes":sum(x["bytes"] for x in parts),
        "sessions":240,"expected_sessions":240,
    }
    (root/"manifest.json").write_text(json.dumps(manifest),encoding="utf-8")
    return manifest


class TestConfig(unittest.TestCase):
    def test_fails_closed_without_secrets(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,"Neon storage settings missing"):
                StorageConfig.from_environment()

    def test_neon_endpoint_and_private_confirmation_required(self):
        values={
            "NEON_STORAGE_ENDPOINT":"https://br-example.storage.c-2.us-east-2.aws.neon.tech",
            "NEON_STORAGE_BUCKET":"a-share-backtests",
            "NEON_STORAGE_ACCESS_KEY_ID":"fake_access",
            "NEON_STORAGE_SECRET_ACCESS_KEY":"fake_secret",
        }
        with patch.dict(os.environ,values,clear=True):
            with self.assertRaisesRegex(RuntimeError,"PRIVATE_CONFIRMED"):
                StorageConfig.from_environment()
        values["NEON_STORAGE_PRIVATE_CONFIRMED"]="true"
        values["NEON_STORAGE_ENDPOINT"]="http://example.com"
        with patch.dict(os.environ,values,clear=True):
            with self.assertRaisesRegex(RuntimeError,"branch-scoped HTTPS"):
                StorageConfig.from_environment()
        values["NEON_STORAGE_ENDPOINT"]="https://br-example.storage.c-2.us-east-2.aws.neon.tech"
        with patch.dict(os.environ,values,clear=True):
            cfg=StorageConfig.from_environment()
        self.assertEqual(cfg.region,"us-east-2")
        self.assertEqual(cfg.bucket,"a-share-backtests")


class TestArchive(unittest.TestCase):
    def test_preflight_read_write_delete(self):
        store=FakeS3()
        preflight(store,"test-bucket")
        self.assertEqual(store.objects,{})

    def test_upload_idempotent_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/"year"
            root.mkdir()
            data=build_archive(root)
            store=FakeS3()
            result=upload_archive(store,"private",2025,root)
            self.assertEqual(result["rows"],data["rows"])
            self.assertEqual(len(store.objects),13)
            n=store.uploads
            upload_archive(store,"private",2025,root)
            self.assertEqual(store.uploads,n)
            dest=Path(folder)/"restored"
            recovered=verify_archive(store,"private",2025,dest)
            self.assertEqual(recovered["partition_files"],data["partition_files"])
            local_manifest(dest,2025)

    def test_corrupt_remote_object_is_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            build_archive(root)
            store=FakeS3()
            upload_archive(store,"private",2025,root)
            key="a-share-backtest/01_raw_data/daily_prices/year=2025/month=01.parquet"
            original,meta=store.objects[("private",key)]
            store.objects[("private",key)]=(b"BAD!"+original[4:],meta)
            with self.assertRaisesRegex(RuntimeError,"SHA-256 mismatch"):
                verify_archive(store,"private",2025)

    def test_reject_manifest_path_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            manifest=build_archive(root)
            manifest["partition_files"][0]["path"]="../secrets.parquet"
            with self.assertRaisesRegex(RuntimeError,"Unexpected partition path"):
                validate_manifest(manifest,2025)

    def test_incomplete_upload_recovers_only_unpublished_months(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            build_archive(root)
            store=FakeS3()
            key="a-share-backtest/01_raw_data/daily_prices/year=2025/month=01.parquet"
            store.put_object(Bucket="private",Key=key,Body=b"broken",
                             Metadata={"sha256":"0"*64})
            upload_archive(store,"private",2025,root)
            self.assertEqual(len(store.objects),13)
            self.assertEqual(verify_archive(store,"private",2025)["sessions"],240)


if __name__=="__main__":
    unittest.main()
