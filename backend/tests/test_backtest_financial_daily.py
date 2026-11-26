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
