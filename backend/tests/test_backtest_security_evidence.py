from datetime import date
import unittest
from value_screener.backtest_security_evidence import listing_evidence,name_evidence_asof


class SecurityEvidenceTests(unittest.TestCase):
    def test_listing_dates(self):
        row={"list_date":"20200102","delist_date":"20260105","list_status":"D","name":"future name"}
        self.assertEqual(listing_evidence(row,date(2020,1,1)),"before_snapshot_listing_date")
        self.assertEqual(listing_evidence(row,date(2024,1,1)),"within_snapshot_listing_dates_not_certified")
        self.assertEqual(listing_evidence(row,date(2026,1,5)),"at_or_after_snapshot_delisting_date")
        self.assertEqual(listing_evidence(None,date(2026,1,5)),"snapshot_missing")

    def test_st_and_announcement(self):
        day=date(2026,1,5)
        row={"start_date":"20260101","end_date":None,"ann_date":"20260101","name":"*ST示例"}
        self.assertEqual(name_evidence_asof([row],day,day),("STAR_ST","namechange_candidate_not_certified"))
        self.assertEqual(name_evidence_asof([row],day,date(2026,1,4))[0],None)
        self.assertEqual(name_evidence_asof([{**row,"ann_date":"20260105"}],day,day)[0],None)
        self.assertEqual(name_evidence_asof([row,{**row,"name":"正常"}],day,day)[1],"name_interval_conflict")
        self.assertEqual(name_evidence_asof([],day,day)[0],None)


    def test_vectorized_historical_equivalence(self):
        import importlib.util
        if not importlib.util.find_spec("duckdb"):
            self.skipTest("requires worker DuckDB")
        import duckdb,tempfile
        from pathlib import Path
        from value_screener.backtest_security_evidence import security_rows
        from value_screener.backtest_bulk import insert_columns
        day=date(2025,1,2)
        snapshot={
            "600001.SH":{"list_date":"20200101","delist_date":None},
            "600002.SH":{"list_date":"20250103"},
            "600003.SH":{"list_date":"20200101","delist_date":"20250102"},
            "600004.SH":{"list_date":"bad"},
            "600005.SH":{},
            "600006.SH":{"list_date":"20200101","delist_date":"bad"},
        }
        rows=[
            ("600001.SH",day,"NORMAL",date(2024,12,31),"both_agree",True),
            ("600002.SH",day,"STAR_ST",day,"namechange_only",True),
            ("600003.SH",day,"DELISTING",None,"unknown",True),
            ("600004.SH",day,None,date(2024,12,31),"unknown",True),
            ("600005.SH",day,"",date(2024,12,31),"unknown",True),
            ("600006.SH",day,"UNKNOWN",None,"unknown",True),
            ("600007.SH",day,"NORMAL",date(2024,12,31),"both_agree",True),
        ]
        with tempfile.TemporaryDirectory() as tmp,duckdb.connect() as db:
            path=Path(tmp)/"market.parquet"
            db.execute("""CREATE TABLE fixture(ts_code VARCHAR,trade_date DATE,st_type VARCHAR,
                      st_ann_date DATE,st_resolution VARCHAR,is_mainboard_code BOOLEAN)""")
            insert_columns(db,"fixture",("VARCHAR","DATE","VARCHAR","DATE","VARCHAR","BOOLEAN"),rows)
            db.execute("COPY fixture TO ? (FORMAT PARQUET)",[str(path)])
            stats,hashes=security_rows(db,path,snapshot,Path(tmp),None)
            self.assertEqual(stats["rows"],len(rows))
            self.assertEqual(hashes,{})
            expected=[]
            for code,d,st,ann,_,_ in rows:
                status="historical_merged_not_certified" if st else "historical_ST_unknown"
                if ann is None or ann>=d:
                    status="historical_announcement_unresolved_or_same_day"
                expected.append((code,d,listing_evidence(snapshot.get(code),d),st,status,False,None))
            self.assertEqual(db.execute("SELECT * FROM security_daily ORDER BY ts_code").fetchall(),expected)

    def test_incremental_unknown_bulk(self):
        import importlib.util
        if not importlib.util.find_spec("duckdb"):
            self.skipTest("requires worker DuckDB")
        import duckdb,tempfile
        from pathlib import Path
        from value_screener.backtest_security_evidence import security_rows
        with tempfile.TemporaryDirectory() as tmp,duckdb.connect() as db:
            path=Path(tmp)/"market.parquet"
            db.execute("""COPY (SELECT '600001.SH' ts_code,DATE '2026-01-05' trade_date,
                NULL::VARCHAR st_type,NULL::DATE st_ann_date,NULL::VARCHAR st_resolution,true is_mainboard_code)
                TO ? (FORMAT PARQUET)""",[str(path)])
            stats,hashes=security_rows(db,path,{},Path(tmp),None)
            self.assertEqual(stats,{"rows":1,"st_evidence_counts":{"namechange_capture_missing":1}})
            self.assertEqual(hashes,{})
            self.assertEqual(db.execute("SELECT historical_st_type,st_certified FROM security_daily").fetchone(),(None,False))
