import contextlib
from datetime import date
import gzip
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from scripts import fin19_standardize as f

ASOF = date(2026,11,28)
ROW = dict(ts_code="000001.SZ",end_date="20251231",f_ann_date="20260301",
           ann_date="20260301",report_type="1",comp_type="1",end_type="1",
           total_assets=100,total_liab=40,total_hldr_eqy_inc_min_int=60,
           total_cur_assets=80,total_cur_liab=20)

def write_gz(path, rows):
    with gzip.open(path,"wt",encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row)+"\n")

class FinancialStandardizationTests(unittest.TestCase):
    def test_balance_and_zero(self):
        result=f.normalize("balancesheet",ROW,ASOF)
        self.assertEqual(result[5],"20260301")
        self.assertEqual(result[6],[])
        self.assertEqual(result[8]["ncav_cny"],40)
        self.assertEqual(result[8]["current_ratio"],4)
        self.assertEqual(f.num(0),0)
        self.assertIsNone(f.num(True))
        self.assertIsNone(f.num(float("nan")))
        self.assertIsNone(f.num("0"))
    def test_date_and_anomaly(self):
        result=f.normalize("balancesheet",dict(ROW,total_assets=200,ann_date="20261201"),ASOF)
        self.assertEqual(result[5],"")
        self.assertIn("invalid_version_date",result[6])
        self.assertIn("balance_equation_anomaly",result[6])
        self.assertEqual(f.normalize("balancesheet",dict(ROW,report_type="6"),ASOF)[8],{})
        self.assertIn("updated_version_lineage_unverified",f.normalize("balancesheet",dict(ROW,update_flag="1"),ASOF)[6])
    def test_version_classes(self):
        def check(rows, expected):
            self.assertEqual(f.classify_payloads([f.canonical(r) for r in rows])[0],expected)
        check([ROW,dict(ROW,update_flag="1")],"metadata_only")
        check([ROW,dict(ROW,total_assets=101)],"non_null_value_conflict")
        check([ROW,dict(ROW,total_assets=None)],"null_population_difference")
        check([ROW,dict(ROW,new_field=1)],"schema_or_complementary_difference")
        check([ROW,dict(ROW,total_assets=100.0)],"null_population_difference")
    def test_missing_and_invalid_denominator(self):
        result=f.normalize("cashflow",dict(ROW,n_cashflow_act=0,c_pay_acq_const_fiolta=None),ASOF)
        self.assertIsNone(result[8]["operating_cash_less_capex_cny_ytd"])
        self.assertIsNone(f.ratio(10,0))
    def fixture(self,root):
        new=root/"new"; old=root/"old"
        (new/"archive").mkdir(parents=True)
        m={"state":"requests_finished_not_certified","incomplete_request_tasks":0,
           "archive":{},"tasks":{}}
        for api in f.KINDS:
            records=[ROW,dict(ROW,update_flag="1")] if api=="balancesheet" else []
            if api=="disclosure_date":
                records=[dict(ts_code="000001.SZ",end_date="20251231",actual_date="20260301")]
            path=new/"archive"/(api+".jsonl.gz")
            write_gz(path,[{"status":"done","rows":records}])
            m["archive"][api]=dict(file=path.name,bytes=path.stat().st_size,sha256=f.digest(path),responses=1)
            m["tasks"][api]={"done":{"rows":len(records)}}
        (new/"manifest.json").write_text(json.dumps(m))
        (new/"missing.csv").write_text("ts_code,period,api,reason\n000002.SZ,20251231,income,no_consolidated_row\n")
        for api in f.CORE:
            folder=old/"financial"/api
            folder.mkdir(parents=True)
            path=folder/"batch.jsonl.gz"
            records=[ROW] if api=="balancesheet" else []
            write_gz(path,records)
            (folder/"batch.manifest.json").write_text(json.dumps(dict(kind=api,format_version=1,source="Tushare official per-symbol",currency="CNY",financial_amount_unit="RMB yuan",rows=len(records),compressed_bytes=path.stat().st_size,sha256=f.digest(path))))
        return new,old
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); new,old=self.fixture(root)
            before=f.digest(new/"archive"/"balancesheet.jsonl.gz")
            out=root/"out"
            with contextlib.redirect_stdout(io.StringIO()):
                f.build(new,old,out,ASOF)
            doc=json.loads((out/"manifest.json").read_text())
            self.assertFalse(doc["pit_certified"])
            self.assertFalse(doc["production_published"])
            self.assertEqual(doc["ledger_sha256"],f.digest(out/"financial.sqlite3"))
            with sqlite3.connect(out/"financial.sqlite3") as db:
                self.assertEqual(db.execute("select count(*) from versions where api='balancesheet'").fetchone()[0],2)
                self.assertEqual(db.execute("select count(*) from lineage l join sources s on l.source_id=s.id where s.api='balancesheet'").fetchone()[0],3)
                self.assertEqual(db.execute("select classification from group_quality where api='balancesheet'").fetchone()[0],"metadata_only")
            self.assertEqual(before,f.digest(new/"archive"/"balancesheet.jsonl.gz"))
            self.assertEqual(doc["date_evidence"][0]["classification"],"calendar_exact_match")
            with self.assertRaises(ValueError):
                f.build(new,old,out,ASOF)
    def test_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); new,old=self.fixture(root)
            with (new/"archive"/"balancesheet.jsonl.gz").open("ab") as stream:
                stream.write(b"x")
            with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(ValueError):
                f.build(new,old,root/"out",ASOF)
            self.assertFalse((root/"out").exists())
    def test_output_inside_source_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); new,old=self.fixture(root)
            with self.assertRaises(ValueError):
                f.build(new,old,new/"out",ASOF)

if __name__=="__main__":
    unittest.main()
