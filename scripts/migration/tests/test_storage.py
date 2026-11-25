import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import boto3
from moto import mock_aws
from common import write_json
from storage import inventory,sync,verify,safe_key

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
