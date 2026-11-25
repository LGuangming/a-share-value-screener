import tempfile
from pathlib import Path
import unittest
import json
import boto3
from moto import mock_aws
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import date
from common import digest,write_json
from storage import inventory,sync
from archive_verify import inspect

@mock_aws
class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.s3=boto3.client("s3",region_name="us-east-1")
        self.bucket="archive-fixture"
        self.s3.create_bucket(Bucket=self.bucket)
        self.key="a-share-backtest/01_raw_data/daily_prices/year=2026/month=11/data.parquet"
        path=self.root/"fixture.parquet"
        pq.write_table(pa.table({"ts_code":["000001.SZ","600000.SH"],"trade_date":[date(2026,11,24)]*2,"close":[10.0,11.0]}),path)
        self.s3.put_object(Bucket=self.bucket,Key=self.key,Body=path.read_bytes())
        self.doc={"object_key":self.key,"rows":2,"fields":["ts_code","trade_date","close"],"sha256":digest(path),"bytes":path.stat().st_size,"trade_date":"2026-11-24","dataset":"daily_prices"}

    def materialize(self,doc):
        self.s3.put_object(Bucket=self.bucket,Key=self.key+".manifest.json",Body=json.dumps(doc).encode())
        root=self.root/"generation"
        m=inventory(self.s3,[self.bucket])
        write_json(root/"inventory-storage.json",m)
        sync(self.s3,m,root)
        return root

    def test_valid_archive(self):
        report=inspect(self.materialize(self.doc))
        self.assertTrue(report["passed"])
        self.assertEqual(report["parquet_rows"],2)

    def test_missing_reference(self):
        self.doc["object_key"]+=".missing"
        with self.assertRaises(ValueError):
            inspect(self.materialize(self.doc))

    def test_bad_count(self):
        self.doc["rows"]=3
        with self.assertRaises(ValueError):
            inspect(self.materialize(self.doc))

    def test_bad_sha(self):
        self.doc["sha256"]="0"*64
        with self.assertRaises(ValueError):
            inspect(self.materialize(self.doc))

    def test_unclassified_manifest(self):
        with self.assertRaises(ValueError):
            inspect(self.materialize({"status":"unknown"}))

    def test_bad_schema(self):
        self.doc["fields"]=["close"]
        with self.assertRaises(ValueError):
            inspect(self.materialize(self.doc))
