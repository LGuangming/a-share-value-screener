import json
import sqlite3
import importlib.util
import tempfile
import unittest
from pathlib import Path

from scripts import fin19_quality as q
from scripts import fin19_market_shares as m
from scripts import fin19_standardize as s
from scripts.fin19_metrics import decisions, build as build_metrics


class QualityTests(unittest.TestCase):
    def test_identity_and_coherent_version(self):
        fields=("total_assets","total_liab","total_hldr_eqy_inc_min_int")
        g={"key":("balancesheet","20251231","1","1","20260301"),
           "fields":decisions([(1,dict(zip(fields,[100,60,40])),[])],fields)}
        self.assertEqual(q.check_identity(g,fields,(1,-1,-1))[:2],("within_tolerance",0))
        g["fields"]=decisions([(1,dict(zip(fields,[100,60,35])),[])],fields)
        self.assertEqual(q.check_identity(g,fields,(1,-1,-1))[:2],("outside_tolerance",5))
        g["fields"]=decisions([(1,{fields[0]:100},[]),(2,{fields[1]:60,fields[2]:40},[])],fields)
        self.assertEqual(q.check_identity(g,fields,(1,-1,-1))[0],"no_coherent_source_version")

    def test_share_proxy_and_missing(self):
        fields=["total_hldr_eqy_exc_min_int"]
        g={"key":("balancesheet","20251231","1","1","20260301"),
           "fields":decisions([(1,{fields[0]:2000000},[])],fields)}
        evidence={"status":"verified_market_session","session_relation":"previous_market_session_proxy"}
        self.assertEqual(q.share_candidate(g,1000000,evidence)[:2],(2,"previous_market_session_proxy"))
        self.assertEqual(q.share_candidate(g,None,evidence)[1],"no_valid_share_on_market_session")
        self.assertEqual(q.share_candidate(g,1000000,{"status":"market_period_unverified"})[1],"market_period_unverified")

    def test_difference_zero_denominator(self):
        self.assertEqual(q.compare_values(4,2),(2,1,"diagnostic_difference"))
        self.assertEqual(q.compare_values(4,0),(4,None,"diagnostic_difference"))
        self.assertEqual(q.compare_values(None,0)[2],"not_comparable")

    def test_missing_calendar_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            cal=root/"trade_calendar/year=2026/month=03"
            cal.mkdir(parents=True)
            (cal/"day=20260330.json").write_text(json.dumps(
                {"exchange":"SSE","trade_date":"2026-03-30","is_open":True}))
            self.assertIsNone(m.incremental_source(root,s.day("20260331")))

    @unittest.skipUnless(importlib.util.find_spec("duckdb"),"Parquet runtime fixture")
    def test_verified_parquet_and_full_sidecar(self):
        import duckdb
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); raw=root/"raw"; ledger=root/"ledger";ledger.mkdir()
            path=ledger/"financial.sqlite3"
            with sqlite3.connect(path) as db:
                db.executescript(s.SCHEMA)
                payload={"total_assets":3000000,"total_liab":1000000,
                         "total_hldr_eqy_inc_min_int":2000000,
                         "total_hldr_eqy_exc_min_int":2000000,"minority_int":0,
                         "total_cur_assets":2500000}
                db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                    1,"balancesheet","hash","000001.SZ","20251231","1","1","1","20260301",
                    json.dumps(payload),"[]","{}","{}"))
            h=s.digest(path)
            (ledger/"manifest.json").write_text(json.dumps({
                "format_version":2,"state":"standardized_staging_not_pit_certified",
                "pit_certified":False,"production_published":False,
                "ledger_bytes":path.stat().st_size,"ledger_sha256":h,"asof":"2026-11-28"}))
            metrics=root/"metrics";build_metrics(ledger,metrics)
            folder=raw/"daily_basic/year=2025";folder.mkdir(parents=True)
            p=folder/"month=12.parquet"
            with duckdb.connect(":memory:") as con:
                con.execute("""COPY (SELECT '000001.SZ' ts_code,DATE '2025-12-30' trade_date,
                              99.0 total_share UNION ALL SELECT '000001.SZ',DATE '2025-12-31',100.0
                              UNION ALL SELECT '000002.SZ',DATE '2025-12-30',50.0)
                              TO '"""+str(p).replace("'","''")+"""' (FORMAT PARQUET)""")
            meta={"format_version":1,"dataset":"daily_basic","source":"Tushare official",
                  "year":2025,"month":"12","fields":["ts_code","trade_date","total_share"],
                  "object_key":m.PREFIX+"/daily_basic/year=2025/month=12.parquet",
                  "rows":3,"sessions":2,"nonempty_sessions":2,"min_session":"20251230",
                  "max_session":"20251231","parquet_bytes":p.stat().st_size,"sha256":s.digest(p)}
            (folder/"month=12.manifest.json").write_text(json.dumps(meta))
            months=[{"month":f"{i:02d}"} for i in range(1,12)]+[meta]
            (folder/"manifest.json").write_text(json.dumps({"format_version":1,
                "dataset":"daily_basic","year":2025,"months":months}))
            out=root/"quality"
            q.build(ledger,metrics,raw,out)
            with sqlite3.connect(out/"quality.sqlite3") as db:
                self.assertEqual(db.execute("SELECT reported,market_candidate,market_status,shares FROM bvps").fetchone(),
                    (None,2,"same_calendar_day",1000000))
                self.assertEqual(db.execute("SELECT status FROM accounting_checks WHERE check_name='balance_identity'").fetchone()[0],
                    "within_tolerance")
            self.assertEqual(s.digest(path),h)
            with duckdb.connect(":memory:") as con:
                shares,evidence=m.load_period(raw,"20251231",con)
                self.assertNotIn("000002.SZ",shares)
                self.assertEqual(shares["000001.SZ"],1000000)
            with self.assertRaises(ValueError):
                q.build(ledger,metrics,raw,out)

    def test_tampered_market_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"x.parquet"
            p.write_bytes(b"original")
            meta={"rows":1,"parquet_bytes":p.stat().st_size,"sha256":s.digest(p)}
            m.verified(p,meta)
            p.write_bytes(b"modified")
            with self.assertRaises(ValueError):
                m.verified(p,meta)


if __name__=="__main__":
    unittest.main()
