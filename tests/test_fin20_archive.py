import contextlib
from contextlib import closing
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts import fin20_archive as a
from scripts import fin19_standardize as s
from scripts import fin19_metrics as m
from scripts import fin19_quality as q
from test_fin19_standardize import FinancialStandardizationTests


@unittest.skipUnless(importlib.util.find_spec("duckdb"),"Parquet runtime required")
class ArchiveTests(unittest.TestCase):
    def test_exact_types_and_chunks(self):
        with tempfile.TemporaryDirectory() as d, closing(a.connection()) as con:
            root=Path(d)
            cols=[{"name":"id","type":"INTEGER","pk":1},{"name":"period","type":"TEXT","pk":0},
                  {"name":"txt","type":"TEXT","pk":0},{"name":"amount","type":"REAL","pk":0}]
            with closing(sqlite3.connect(":memory:")) as db:
                db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY,period TEXT,txt TEXT,amount REAL)")
                rows=[(1,"20251231","",0.0),(2,"20251231",None,None),
                      (3,"20260331",'含引号,"换行\n',-1.23456789012345),
                      (4,"bad","null",1e-200),(5,None,"",1e200)]
                db.executemany("INSERT INTO sample VALUES (?,?,?,?)",rows)
                defn={"columns":cols}
                result=a.export_table(db,con,root,"ledger","sample",defn)
                self.assertEqual(result["rows"],5)
                self.assertEqual([p["partition"] for p in result["partitions"]],["2025","2026","unknown"])
                a.verify_table(con,root,defn,result)
                a.verify_source(db,"sample",defn,result)
                file=root/result["partitions"][0]["files"][0]["path"]
                with file.open("ab") as f:
                    f.write(b"x")
                with self.assertRaises(ValueError):
                    a.verify_table(con,root,defn,result)

    def test_full_snapshot_restore(self):
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            root=Path(d);new,old=FinancialStandardizationTests().fixture(root)
            (new/"unfinished.csv").write_text("task,status\n")
            ledger=root/"ledger"; metrics=root/"metrics";quality=root/"quality"
            s.build(new,old,ledger,date(2026,11,28))
            m.build(ledger,metrics)
            q.build(ledger,metrics,old,quality)
            snapshot=root/"snapshot"
            a.build(ledger,metrics,quality,new,old,snapshot)
            doc=a.verify(snapshot,old)
            self.assertFalse(doc["pit_certified"])
            self.assertEqual(len(doc["weekend_sources"]),9)
            self.assertEqual(sum(len(x["tables"]) for x in doc["layers"].values()),17)
            self.assertEqual(s.digest(new/"archive"/"balancesheet.jsonl.gz"),
                s.digest(snapshot/"01_raw_data/financial_weekend/archive/balancesheet.jsonl.gz"))
            restored=root/"restored"
            a.restore(snapshot,old,restored)
            for layer,d in doc["layers"].items():
                with closing(a.ro(restored/a.LAYERS[layer][0])) as db:
                    for name,table in d["tables"].items():
                        a.verify_source(db,name,d["schema"]["tables"][name],table)
            with closing(a.ro(restored/"financial.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM statement_date_evidence").fetchone()[0],2)
            with self.assertRaises(ValueError):
                a.build(ledger,metrics,quality,new,old,snapshot)
            ref=old/doc["existing_raw_references"][0]["path"]
            with ref.open("ab") as f:
                f.write(b"x")
            with self.assertRaises(ValueError):
                a.verify(snapshot,old)

    def test_unsafe_output_and_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with self.assertRaises(ValueError):
                a.safe(root,"../outside")
            with self.assertRaises(ValueError):
                a.separate(root/"inside",root)


if __name__=="__main__":
    unittest.main()
