import importlib.util
import tempfile
from pathlib import Path
from datetime import date
import unittest
from value_screener.backtest_financial_daily import candidate_events,daily_join


class CandidateTests(unittest.TestCase):
    def test_calendar_gate(self):
        row={"ts_code":"600001.SH","end_date":"20240930","f_ann_date":"20241020","ann_date":"20241021",
            "report_type":"1","total_cur_assets":100,"total_liab":50}
        key=(row["ts_code"],row["end_date"])
        events=list(candidate_events([row],"balancesheet",{key:{date(2024,10,20)}}))
        self.assertEqual(events[-1][0],date(2024,10,22))
        self.assertEqual(events[-1][-3:],("calendar_correlated_not_pit_certified",100,50))
        self.assertEqual(list(candidate_events([row],"balancesheet",{}))[-1][-3:],("calendar_missing",None,None))
        self.assertEqual(list(candidate_events([row],"balancesheet",{key:{date(2024,10,19)}}))[-1][-3:],("calendar_actual_conflict",None,None))

    @unittest.skipUnless(importlib.util.find_spec("duckdb"),"requires worker DuckDB")
    def test_daily_join(self):
        import duckdb
        with tempfile.TemporaryDirectory() as tmp:
            db=duckdb.connect()
            p=Path(tmp)/"market.parquet"
            db.execute("""COPY (SELECT '600001.SH' ts_code, d AS trade_date, true is_mainboard_code, 25.0 total_mv_cny
                FROM (VALUES (DATE '2024-10-21'),(DATE '2024-10-22')) t(d)) TO ? (FORMAT PARQUET)""",[str(p)])
            common="ts_code VARCHAR,available DATE,period DATE,disclosed DATE,version VARCHAR,status VARCHAR"
            db.execute("CREATE TABLE balancesheet_events ("+common+",total_cur_assets DOUBLE,total_liab DOUBLE)")
            db.execute("CREATE TABLE income_events ("+common+",n_income DOUBLE,n_income_attr_p DOUBLE)")
            db.execute("CREATE TABLE cashflow_events ("+common+",n_cashflow_act DOUBLE)")
            db.execute("INSERT INTO balancesheet_events VALUES ('600001.SH','2024-10-22','2024-09-30','2024-10-21','v','candidate',100,50)")
            self.assertEqual(daily_join(db,p)["rows"],2)
            self.assertEqual(db.execute("SELECT ncav_cny,market_to_ncav,pit_certified FROM financial_daily ORDER BY trade_date").fetchall(),[(None,None,False),(50,0.5,False)])
            db.close()


    def test_event_sweep_matches_original_selector(self):
        from value_screener.backtest_financial_evidence import evidence,select_candidate,canonical
        from datetime import timedelta
        import hashlib,random
        rng=random.Random(2025)
        for _ in range(50):
            rows=[]
            calendar={}
            for month in (3,6,9,12):
                period=date(2023,month,28)
                actual=period+timedelta(days=20)
                for v in range(rng.randrange(1,4)):
                    a=actual+timedelta(days=rng.randrange(3))
                    row={"ts_code":"600001.SH","end_date":period.strftime("%Y%m%d"),
                         "f_ann_date":a.strftime("%Y%m%d") if rng.randrange(5) else None,
                         "ann_date":a.strftime("%Y%m%d"),"report_type":"1" if rng.randrange(5) else "2",
                         "total_cur_assets":rng.choice([100,200,None]),"total_liab":50}
                    rows.append(row)
                    calendar[(row["ts_code"],row["end_date"])]={actual}
            rng.shuffle(rows)
            expected=[]
            prev=None
            days=set()
            for row in rows:
                p,d,_=evidence(row,"balancesheet")
                if p: days.add(p)
                if d: days.add(d+timedelta(days=1))
            for day in sorted(days):
                row,status=select_candidate(rows,"balancesheet",day)
                p=d=version=None
                metrics=(None,None)
                if row is not None:
                    p,d,_=evidence(row,"balancesheet")
                    actual=date.fromisoformat(row["f_ann_date"][:4]+"-"+row["f_ann_date"][4:6]+"-"+row["f_ann_date"][6:])
                    if calendar[(row["ts_code"],row["end_date"])]!={actual}:
                        status="calendar_actual_conflict"
                    else:
                        status="calendar_correlated_not_pit_certified"
                        metrics=(float(row["total_cur_assets"]),float(row["total_liab"]))
                    version=hashlib.sha256(canonical(row).encode()).hexdigest()
                state=(p,d,version,status,*metrics)
                if state!=prev:
                    expected.append((day,*state))
                    prev=state
            self.assertEqual(list(candidate_events(rows,"balancesheet",calendar)),expected)

    @unittest.skipUnless(importlib.util.find_spec("duckdb"),"requires worker DuckDB")
    def test_typed_bulk_insert(self):
        import duckdb
        from value_screener.backtest_bulk import insert_columns
        with duckdb.connect() as db:
            db.execute("CREATE TABLE fixture(code VARCHAR,d DATE,value DOUBLE,flag BOOLEAN)")
            rows=[("600001.SH",date(2025,1,2),None,False)]*5001
            insert_columns(db,"fixture",("VARCHAR","DATE","DOUBLE","BOOLEAN"),rows)
            self.assertEqual(db.execute("SELECT count(*),count(value),min(d),bool_or(flag) FROM fixture").fetchone(),
                             (5001,0,date(2025,1,2),False))

    @unittest.skipUnless(importlib.util.find_spec("duckdb"),"requires worker DuckDB")
    def test_verified_event_cache(self):
        import duckdb
        from unittest.mock import patch
        from value_screener import backtest_financial_daily as f
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def fake_load(db,raw):
                for kind in f.KINDS:
                    types=f.event_types(kind)
                    db.execute("CREATE TEMP TABLE "+kind+"_events AS SELECT "+
                        ",".join("NULL::"+t+" AS "+n for t,n in zip(types,("ts_code","available","period","disclosed","version","status",*f.METRICS[kind])))+
                        " WHERE false")
                return {"source":"fixture"}
            with patch.object(f,"source_identity",return_value={"source":"hash"}), patch.object(f,"load_events",side_effect=fake_load) as load:
                for _ in range(2):
                    with duckdb.connect() as db:
                        self.assertEqual(f.cached_events(db,root,root/"cache"),{"source":"fixture"})
                self.assertEqual(load.call_count,1)
                path=next((root/"cache").glob("*/balancesheet-*.parquet"))
                path.write_bytes(b"corrupt")
                with duckdb.connect() as db,self.assertRaisesRegex(ValueError,"Corrupt event cache"):
                    f.cached_events(db,root,root/"cache")
