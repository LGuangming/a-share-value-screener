import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from value_screener.archive_paths import RAW_PREFIX
from value_screener.archive_storage import archive_driver,require_neon_only
from value_screener.local_archive import LocalArchive,LOCAL_BUCKET,archive_lock,MissingObject
from value_screener.neon_archive import StorageConfig,s3_client,verify_object,preflight
from value_screener.phase2_market import put_manifest,get_optional_json
from value_screener.local_pipeline import run
from value_screener.raw_maintenance import backup_rows,web_gate,NotReady
from datetime import date

class LocalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.env=patch.dict(os.environ,{"ARCHIVE_STORAGE_DRIVER":"local","ARCHIVE_LOCAL_ROOT":str(self.root),
                    "ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY":"false","PIPELINE_RUN_ID":""})
        self.env.start();self.addCleanup(self.env.stop)
        self.cfg=StorageConfig.from_environment()
        self.s=s3_client(self.cfg)
        self.b=self.cfg.bucket

    def put(self,key,raw,**kwargs):
        return self.s.put_object(Bucket=self.b,Key=key,Body=raw,Metadata={"sha256":hashlib.sha256(raw).hexdigest()},**kwargs)

    def test_local_config_and_roundtrip(self):
        self.assertIsInstance(self.s,LocalArchive)
        self.assertEqual(self.b,LOCAL_BUCKET)
        preflight(self.s,self.b)
        key=RAW_PREFIX+"/financial/example.json"
        put_manifest(self.s,self.b,key,{"state":"verified"})
        self.assertEqual(get_optional_json(self.s,self.b,key),{"state":"verified"})
        h=self.s.head_object(Bucket=self.b,Key=key)
        self.assertEqual(h["ContentType"],"application/json")
        verify_object(self.s,self.b,key,h["ContentLength"],h["Metadata"]["sha256"])
        self.assertIsNone(get_optional_json(self.s,self.b,RAW_PREFIX+"/missing.json"))
        with patch.dict(os.environ,{"ARCHIVE_STORAGE_DRIVER":"bad"}):
            with self.assertRaises(RuntimeError): archive_driver()
        with self.assertRaises(RuntimeError): require_neon_only()

    def test_atomic_failure_and_corruption(self):
        key=RAW_PREFIX+"/daily_prices/reader.manifest.json"
        self.put(key,b"old",ContentType="application/json")
        with self.assertRaisesRegex(RuntimeError,"checksum"):
            self.s.put_object(Bucket=self.b,Key=key,Body=b"bad",Metadata={"sha256":"a"*64})
        self.assertEqual((self.root/key).read_bytes(),b"old")
        real_replace=os.replace
        def fail_data(src,dst):
            if Path(dst)==self.root/key: raise OSError("simulated interrupted publication")
            return real_replace(src,dst)
        with patch("value_screener.local_archive.os.replace",side_effect=fail_data):
            with self.assertRaises(OSError): self.put(key,b"new",ContentType="application/json")
        self.assertEqual(self.s.head_object(Bucket=self.b,Key=key)["Metadata"]["sha256"],hashlib.sha256(b"old").hexdigest())
        self.put(key,b"new",ContentType="application/json")
        self.assertEqual((self.root/key).read_bytes(),b"new")
        (self.root/key).write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError,"metadata missing"):
            self.s.head_object(Bucket=self.b,Key=key)
        unindexed=RAW_PREFIX+"/unindexed.json"
        (self.root/unindexed).write_bytes(b"{}")
        with self.assertRaisesRegex(RuntimeError,"metadata missing"):
            self.s.head_object(Bucket=self.b,Key=unindexed)

    def test_immutable_binary_and_listing(self):
        prefix=RAW_PREFIX+"/historical_st/universe="
        for n in ("a","b","c"):
            self.put(prefix+n+"/data.parquet",b"PAR1")
        with self.assertRaisesRegex(RuntimeError,"immutable"):
            self.put(prefix+"a/data.parquet",b"different")
        p=self.s.list_objects_v2(Bucket=self.b,Prefix=prefix,Delimiter="/",MaxKeys=2)
        self.assertTrue(p["IsTruncated"])
        self.assertEqual(len(p["CommonPrefixes"]),2)
        pages=list(self.s.get_paginator("list_objects_v2").paginate(Bucket=self.b,Prefix=prefix,MaxKeys=1))
        self.assertEqual([x["Contents"][0]["Key"] for x in pages],[prefix+n+"/data.parquet" for n in ("a","b","c")])
        for p in pages:
            obj=p["Contents"][0]
            self.assertEqual(obj["ETag"],self.s.head_object(Bucket=self.b,Key=obj["Key"])["ETag"])
        with self.assertRaises(RuntimeError): self.s.delete_object(Bucket=self.b,Key=prefix+"a/data.parquet")

    def test_safe_paths_and_delete_gate(self):
        key=RAW_PREFIX+"/test.json"
        self.put(key,b"test")
        for bad in ("/etc/passwd",RAW_PREFIX+"/../escape",RAW_PREFIX+"/a//b",RAW_PREFIX+"/a\\b"):
            with self.assertRaises(RuntimeError): self.s.head_object(Bucket=self.b,Key=bad)
        (self.root/RAW_PREFIX/"link").symlink_to("/etc/passwd")
        with self.assertRaises(RuntimeError): self.s.head_object(Bucket=self.b,Key=RAW_PREFIX+"/link")
        reader=RAW_PREFIX+"/daily_prices/year=2026/month=01/reader/sha256="+"a"*64+".parquet"
        self.put(reader,b"fixture")
        with self.assertRaises(RuntimeError): self.s.delete_object(Bucket=self.b,Key=reader)
        with patch.dict(os.environ,{"ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY":"true","PIPELINE_RUN_ID":"fixture"}):
            with self.assertRaises(RuntimeError): self.s.delete_object(Bucket=self.b,Key=key)
            self.s.delete_object(Bucket=self.b,Key=reader)
        with self.assertRaises(MissingObject): self.s.head_object(Bucket=self.b,Key=reader)

    def test_backup_and_lock_and_runner(self):
        report=backup_rows(self.s,self.b,"daily_prices",date(2026,1,1),[('{"ts_code":"000001.SZ"}',)])
        verify_object(self.s,self.b,report["object_key"],report["bytes"],report["sha256"])
        self.assertEqual(backup_rows(self.s,self.b,"daily_prices",date(2026,1,1),[('{"ts_code":"000001.SZ"}',)]),report)
        with archive_lock(self.root,"pipeline"):
            with self.assertRaisesRegex(RuntimeError,"Another"):
                with archive_lock(self.root,"pipeline"): pass
        with patch.dict(os.environ,{"DATABASE_URL":"fixture","GITHUB_RUN_ID":"fake"}):
            with patch("value_screener.local_pipeline.run_module") as m:
                run("market")
                self.assertEqual([x.args[0] for x in m.call_args_list],["cli","raw_incremental","cli"])
                ids={x.args[2]["PIPELINE_RUN_ID"] for x in m.call_args_list}
                self.assertEqual(len(ids),1)
                self.assertNotIn("GITHUB_RUN_ID",m.call_args_list[0].args[2])
                m.reset_mock();run("maintenance")
                self.assertEqual(m.call_args.args[1],[])
                with self.assertRaisesRegex(RuntimeError,"opt-in"):run("maintenance",apply=True)
        with patch.dict(os.environ,{"ARCHIVE_LOCAL_WEB_ORIGIN":"https://stock.guangming.blog"}):
            with self.assertRaises(NotReady): web_gate(date(2026,1,1))

if __name__=="__main__": unittest.main()
