import os
import copy
from datetime import datetime, timezone, timedelta
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import boto3
from moto import mock_aws
from common import write_json,load
from storage import inventory,sync,verify,safe_key,same_modified,main

@mock_aws
class StorageTest(unittest.TestCase):
    def setUp(self):
        self.client=boto3.client("s3",region_name="us-east-1")
        for b in ("archive-fixture","second-fixture"):
            self.client.create_bucket(Bucket=b)
        self.client.put_object(Bucket="archive-fixture",Key="a-share-backtest/01_raw_data/daily_prices/a.parquet",Body=b"fixture")
        self.client.put_object(Bucket="second-fixture",Key="elsewhere/manifest.json",Body=b"{}")
        self.buckets=["archive-fixture","second-fixture"]
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def manifest(self):
        m=inventory(self.client,self.buckets)
        write_json(self.root/"inventory-storage.json",m)
        return m

    def test_inventory_sync_repeat_append_corruption(self):
        m=self.manifest()
        self.assertEqual(len(m["objects"]),2)
        self.assertEqual(m["total_bytes"],9)
        self.assertTrue(sync(self.client,m,self.root)["passed"])
        with patch.object(self.client,"get_object",side_effect=AssertionError("repeat downloaded")):
            self.assertTrue(sync(self.client,m,self.root)["passed"])
        self.client.put_object(Bucket="second-fixture",Key="elsewhere/new.json",Body=b"new")
        m=self.manifest()
        self.assertEqual(sync(self.client,m,self.root)["objects"],3)
        file=self.root/"files/second-fixture/elsewhere/new.json"
        file.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            verify(m,self.root)
        with self.assertRaises(ValueError):
            sync(self.client,m,self.root)
        self.assertEqual(file.read_bytes(),b"bad")

    def test_incomplete_bucket_scope_and_unsafe_keys(self):
        with self.assertRaises(ValueError):
            inventory(self.client,["archive-fixture"])
        for key in ("../bad","/absolute","foo//bar","foo/","a\\b"):
            with self.assertRaises(ValueError):
                safe_key(key)

    def test_changed_object_no_overwrite(self):
        m=self.manifest()
        sync(self.client,m,self.root)
        self.client.put_object(Bucket="second-fixture",Key="elsewhere/manifest.json",Body=b"changed")
        with self.assertRaises(ValueError):
            sync(self.client,self.manifest(),self.root)
        self.assertEqual((self.root/"files/second-fixture/elsewhere/manifest.json").read_bytes(),b"{}")

    def test_source_changes_after_listing(self):
        m=self.manifest()
        self.client.put_object(Bucket="archive-fixture",Key=m["objects"][0]["key"],Body=b"changed")
        with self.assertRaises(Exception):
            sync(self.client,m,self.root)
        self.assertFalse((self.root/"files/archive-fixture"/m["objects"][0]["key"]).exists())

    def test_extra_file_fails_without_delete(self):
        m=self.manifest()
        sync(self.client,m,self.root)
        extra=self.root/"files/extra"
        extra.write_text("keep")
        with self.assertRaises(ValueError):
            verify(m,self.root)
        self.assertTrue(extra.exists())

    def test_pagination(self):
        for i in range(1001):
            self.client.put_object(Bucket="second-fixture",Key=f"page/{i:04}",Body=b"x")
        self.assertEqual(len(inventory(self.client,self.buckets)["objects"]),1003)

    def test_new_generation_reuses_only_unchanged(self):
        old=self.manifest()
        sync(self.client,old,self.root)
        self.client.put_object(Bucket="second-fixture",Key="elsewhere/manifest.json",Body=b"changed")
        new=inventory(self.client,self.buckets)
        dest=self.root/"next-generation"
        write_json(dest/"inventory-storage.json",new)
        with patch.object(self.client,"get_object",wraps=self.client.get_object) as get:
            self.assertTrue(sync(self.client,new,dest,self.root)["passed"])
            self.assertEqual(get.call_count,1)
        self.assertEqual((self.root/"files/second-fixture/elsewhere/manifest.json").read_bytes(),b"{}")
        self.assertEqual((dest/"files/second-fixture/elsewhere/manifest.json").read_bytes(),b"changed")

    def test_stream_interruption_resumes_object(self):
        m=self.manifest()
        original=self.client.get_object
        def broken(**kw):
            response=original(**kw)
            def chunks(chunk_size):
                yield b"x"
                raise OSError("interrupted stream")
            response["Body"].iter_chunks=chunks
            return response
        with patch.object(self.client,"get_object",side_effect=broken):
            with self.assertRaises(OSError):
                sync(self.client,m,self.root)
        self.assertFalse(list((self.root/"files").rglob(".download-*")))
        self.assertTrue(sync(self.client,m,self.root)["passed"])

    def test_failed_reverification_invalidates_receipt(self):
        m=self.manifest()
        sync(self.client,m,self.root)
        self.assertTrue((self.root/"verify-storage.json").exists())
        path=self.root/"files/second-fixture/elsewhere/manifest.json"
        path.write_bytes(b"bad")
        with self.assertRaises(ValueError):
            verify(m,self.root)
        self.assertFalse((self.root/"verify-storage.json").exists())

    def test_parallel_interruption_after_partial_publication(self):
        m=self.manifest()
        original=self.client.get_object
        broken_key=m["objects"][1]["key"]
        def broken(**kw):
            if kw["Key"]==broken_key:
                raise OSError("fixture failure")
            return original(**kw)
        with patch.object(self.client,"get_object",side_effect=broken):
            with self.assertRaises(OSError):
                sync(self.client,m,self.root,workers=2)
        self.assertTrue((self.root/"checkpoint.jsonl").exists())
        self.assertFalse((self.root/"verify-storage.json").exists())
        with patch.object(self.client,"get_object",wraps=original) as get:
            self.assertTrue(sync(self.client,m,self.root,workers=2)["passed"])
            self.assertEqual(get.call_count,1)
        self.assertFalse((self.root/"checkpoint.jsonl").exists())


    def test_neon_list_fractional_get_seconds_and_resume(self):
        m=self.manifest()
        original=self.client.get_object
        for obj in m["objects"]:
            obj["modified"]=datetime.fromisoformat(obj["modified"]).replace(microsecond=768000).isoformat()
        write_json(self.root/"inventory-storage.json",m)
        def get(**kw):
            obj=next(x for x in m["objects"] if x["key"]==kw["Key"])
            self.assertEqual(kw["IfMatch"],obj["etag"])
            response=original(**kw)
            response["LastModified"]=datetime.fromisoformat(obj["modified"]).replace(microsecond=0)
            return response
        with patch.object(self.client,"get_object",side_effect=get):
            self.assertTrue(sync(self.client,m,self.root,workers=2)["passed"])
        with patch.object(self.client,"get_object",side_effect=AssertionError("repeat downloaded")):
            self.assertTrue(sync(self.client,m,self.root,workers=2)["passed"])
        self.assertEqual(m["objects"][0]["modified"],load(self.root/"inventory-storage.json")["objects"][0]["modified"])

    def test_get_mismatches_still_rejected(self):
        original=self.client.get_object
        for case in ("time","fraction","etag","size","sha256","naive"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                m=inventory(self.client,self.buckets)
                for obj in m["objects"]:
                    obj["modified"]=datetime.fromisoformat(obj["modified"]).replace(microsecond=197000).isoformat()
                write_json(root/"inventory-storage.json",m)
                if case=="size":
                    m["objects"][0]["size"]+=1
                def get(**kw):
                    response=original(**kw)
                    stamp=datetime.fromisoformat(m["objects"][0]["modified"])
                    response["LastModified"]=stamp.replace(microsecond=0)
                    if case=="time":
                        response["LastModified"]+=timedelta(seconds=1)
                    elif case=="fraction":
                        response["LastModified"]=stamp.replace(microsecond=198000)
                    elif case=="etag":
                        response["ETag"]='"different"'
                    elif case=="sha256":
                        response["Metadata"]={"sha256":"0"*64}
                    elif case=="naive":
                        response["LastModified"]=stamp.replace(tzinfo=None)
                    return response
                with patch.object(self.client,"get_object",side_effect=get):
                    with self.assertRaises(ValueError):
                        sync(self.client,m,root)
                self.assertFalse((root/"verify-storage.json").exists())
                self.assertFalse(list((root/"files").rglob(".download-*")))
                self.assertFalse(any(p.is_file() for p in (root/"files").rglob("*")))

    def test_list_fractional_change_before_or_after_sync_rejected(self):
        env={"NEON_STORAGE_ENDPOINT":"https://fixture.invalid","NEON_STORAGE_REGION":"us-east-1",
             "NEON_STORAGE_ACCESS_KEY_ID":"fixture","NEON_STORAGE_SECRET_ACCESS_KEY":"fixture"}
        for after in (False,True):
            with self.subTest(after=after), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                before=inventory(self.client,self.buckets)
                before["objects"][0]["modified"]=datetime.fromisoformat(before["objects"][0]["modified"]).replace(microsecond=197000).isoformat()
                changed=copy.deepcopy(before)
                changed["objects"][0]["modified"]=datetime.fromisoformat(changed["objects"][0]["modified"]).replace(microsecond=198000).isoformat()
                write_json(root/"inventory-storage.json",before)
                args=["storage.py","sync","--root",str(root),"--approved-live-read"]
                for bucket in self.buckets:
                    args.extend(["--bucket",bucket])
                manifests=[before,changed] if after else [changed]
                with patch.dict(os.environ,env), patch("sys.argv",args), patch("storage.boto3.client",return_value=self.client), patch("storage.inventory",side_effect=manifests):
                    with self.assertRaisesRegex(ValueError,"source listing changed" if after else "source changed or removed"):
                        main()
                self.assertFalse((root/"verify-storage.json").exists())


class ModifiedTimestampTest(unittest.TestCase):
    def test_precision_and_timezone(self):
        listed="2026-11-24T10:46:45.768000+00:00"
        for stamp in ("2026-11-24T10:46:45+00:00","2026-11-24T18:46:45+08:00",
                      "2026-11-24T10:46:45.768000+00:00"):
            self.assertTrue(same_modified(listed,datetime.fromisoformat(stamp)))
        for stamp in ("2026-11-24T10:46:44+00:00","2026-11-24T10:46:46+00:00",
                      "2026-11-24T10:46:45.769000+00:00"):
            self.assertFalse(same_modified(listed,datetime.fromisoformat(stamp)))
        for invalid in ("not-a-date","2026-11-24T10:46:45"):
            with self.assertRaises(ValueError):
                same_modified(invalid,datetime(2026,11,24,10,46,45,tzinfo=timezone.utc))
        for invalid in (None,datetime(2026,11,24,10,46,45)):
            with self.assertRaises(ValueError):
                same_modified(listed,invalid)
