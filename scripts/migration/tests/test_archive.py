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


    def test_catalog_and_financial_jsonl(self):
        import gzip
        import hashlib
        from archive_verify import FIN_FIELDS,RAW
        b=self.bucket
        code="000001.SZ"
        snapshot=RAW+"stock_basic/stock_basic_snapshot.json"
        self.s3.put_object(Bucket=b,Key=snapshot,Body=json.dumps({"dataset":"stock_basic_snapshot","rows":1,"records":[{"ts_code":code}]}).encode())
        key=RAW+"financial/income/start=00000_count=020.jsonl.gz"
        row={f:None for f in FIN_FIELDS["income"].split(",")}
        row.update(ts_code=code,end_date="20250930",f_ann_date="20251031")
        blob=gzip.compress((json.dumps(row)+"\n").encode(),mtime=0)
        self.s3.put_object(Bucket=b,Key=key,Body=blob)
        fin={"object_key":key,"sha256":hashlib.sha256(blob).hexdigest(),"compressed_bytes":len(blob),"rows":1,"kind":"income","start_index":0,"requested_codes":1,"codes_sha256":hashlib.sha256(json.dumps([code],ensure_ascii=False).encode()).hexdigest(),"missing_codes":[],"missing_codes_count":0,"missing_f_ann_date_rows":0}
        self.s3.put_object(Bucket=b,Key=key+".manifest.json",Body=json.dumps(fin).encode())
        entry={"object_key":key,"bytes":len(blob),"source_bytes":len(blob),"sha256":fin["sha256"]}
        receipt=RAW+"_migration/financial.json"
        self.s3.put_object(Bucket=b,Key=receipt,Body=json.dumps({"dataset":"financial","status":"copied_and_verified","objects":1,"bytes":len(blob),"source_bytes":len(blob),"entries":[entry]}).encode())
        catalog={"status":"consolidated_verified","objects":1,"bytes":len(blob),"source_bytes":len(blob),"datasets":[{"dataset":"financial","prefix":RAW+"financial/","inventory":receipt,"objects":1,"bytes":len(blob),"source_bytes":len(blob)}]}
        self.s3.put_object(Bucket=b,Key=RAW+"dataset_catalog.json",Body=json.dumps(catalog).encode())
        r=inspect(self.materialize(self.doc))
        self.assertEqual(r["gzip_rows"],1)
        self.assertEqual(len(r["catalogs"]),1)

    def test_gzip_bad_crc(self):
        import gzip
        from archive_verify import read_gzip
        path=self.root/"bad.jsonl.gz"
        path.write_bytes(gzip.compress(b'{"ts_code":"000001.SZ"}\n')[:-5])
        with self.assertRaises((EOFError,OSError)):
            read_gzip(path,path.name)


    def legacy_fixture(self,case="valid"):
        import base64
        import gzip
        import hashlib
        from archive_verify import RAW,LEGACY_BACKUP,LEGACY_PLAN,LEGACY_DONE
        sha=lambda value: hashlib.sha256(value).hexdigest()
        source="a-share-backtest/v1/prices/day.manifest.json"
        dest=RAW+"daily_prices/day.manifest.json"
        original=json.dumps({"object_key":"a-share-backtest/v1/prices/data.parquet"}).encode()
        current=json.dumps(self.doc).encode()
        def put(key,value):
            self.s3.put_object(Bucket=self.bucket,Key=key,Body=value)
        def put_json(key,value):
            put(key,json.dumps(value).encode())
        put(dest,current)
        entry={"source_key":source,"object_key":dest,"sha256":sha(current),"bytes":len(current),
               "source_sha256":sha(original),"source_bytes":len(original)}
        receipt=RAW+"_inventory/daily_prices.json"
        totals={"objects":1,"bytes":len(current),"source_bytes":len(original)}
        put_json(receipt,{"dataset":"daily_prices","status":"copied_and_verified","entries":[entry],**totals})
        put_json(RAW+"dataset_catalog.json",{"status":"consolidated_verified","raw_prefix":RAW,**totals,
                 "datasets":[{"dataset":"daily_prices","prefix":RAW+"daily_prices/","inventory":receipt,**totals}]})
        payload={source:base64.b64encode(original).decode()}
        if case=="coverage": payload={}
        if case=="bad_base64": payload[source]="!!!"
        if case=="original_hash": payload[source]=base64.b64encode(original.replace(b"prices",b"quotes")).decode()
        raw=json.dumps(payload).encode()
        if case=="duplicate": raw=b"{"+raw[1:-1]+b","+raw[1:]
        if case=="array": raw=b"[]"
        blob=gzip.compress(raw,mtime=0)
        if case!="missing_backup": put(LEGACY_BACKUP,blob)
        inventory_sha=sha(json.dumps({source:entry},sort_keys=True,separators=(",",":")).encode())
        plan={"status":"all_copies_verified_before_delete","inventory_sha256":inventory_sha,
              "objects":1,"source_bytes":len(original),"backup_key":LEGACY_BACKUP,
              "backup_sha256":sha(blob),"backup_bytes":len(blob),"original_manifests_preserved":1}
        done={"status":"retired_verified","legacy_source_retained":False,"inventory_sha256":inventory_sha,
              "deleted_objects":1,"deleted_source_bytes":len(original),
              "original_manifest_backup":LEGACY_BACKUP,"backup_bytes":len(blob)}
        if case=="backup_hash": plan["backup_sha256"]="0"*64
        if case=="retirement_digest": done["inventory_sha256"]="0"*64
        if case=="status": done["status"]="pending"
        if case=="count": plan["original_manifests_preserved"]=2
        put_json(LEGACY_PLAN,plan)
        if case!="missing_done": put_json(LEGACY_DONE,done)
        return self.materialize(self.doc)

    def test_legacy_original_manifest_backup(self):
        report=inspect(self.legacy_fixture())
        self.assertTrue(report["passed"])
        self.assertEqual(report["legacy_original_manifests"],1)
        self.assertEqual(report["legacy_manifest_backups"][0]["original_manifests"],1)
        self.assertEqual(report["gzip_rows"],0)
        self.assertEqual(report["unreferenced_parquet"],[])

    def test_legacy_backup_rejects_invalid_evidence(self):
        for case in ("coverage","bad_base64","original_hash","duplicate","array","missing_backup",
                     "backup_hash","retirement_digest","status","count","missing_done"):
            with self.subTest(case=case):
                self.setUp()
                root=self.legacy_fixture(case)
                with self.assertRaises(ValueError):
                    inspect(root)
                self.assertFalse((root/"verify-archive.json").exists())

    def test_other_gzip_dictionary_still_rejected(self):
        import gzip
        from archive_verify import RAW
        self.s3.put_object(Bucket=self.bucket,Key=RAW+"unknown.json.gz",Body=gzip.compress(b"{}"))
        with self.assertRaisesRegex(ValueError,"unsupported gzip array"):
            inspect(self.materialize(self.doc))
