import importlib.util
from pathlib import Path
import tempfile
import unittest

from value_screener import backtest_dataset as bt


@unittest.skipUnless(importlib.util.find_spec("duckdb"), "requires worker DuckDB")
class DatasetTests(unittest.TestCase):
    def test_join_resume_and_missing_key(self):
        import duckdb
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=duckdb.connect()
            sqls={
                "daily_prices":"SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date, 10.0 open, 11.0 high, 9.0 low, 10.0 close, 100.0 vol, 1000.0 amount",
                "adj_factor":"SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date, 2.0 adj_factor",
                "daily_basic":"SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date, 200.0 total_mv, 100.0 circ_mv, 20.0 total_share, 10.0 float_share, 8.0 free_share, 1.0 turnover_rate, 1.0 pb, 10.0 pe_ttm",
                "stk_limit":"SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date, 11.0 up_limit, 9.0 down_limit",
                "suspend_d":"SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date, 'S' suspend_type, '' suspend_timing UNION ALL SELECT '600001.SH', DATE '2025-01-02', 'R',''",
                "historical_st":"SELECT '600001.SH' ts_code, DATE '2025-01-02' trade_date, 0 is_st, 'NORMAL' st_type, 'both_agree' resolution, DATE '2024-12-01' namechange_ann_date",
            }
            sources={}
            def write(ds, query):
                p=root/(ds+".parquet")
                db.execute("COPY ("+query+") TO ? (FORMAT PARQUET)",[str(p)])
                rows=db.execute("SELECT count(*) FROM read_parquet(?)",[str(p)]).fetchone()[0]
                sources[ds]=(p,{"rows":rows,"bytes":p.stat().st_size,"sha256":bt.digest(p)})
            for ds,q in sqls.items():
                write(ds,q)
            out=root/"out"
            out.mkdir()
            doc,action=bt.write_partition(db,sources,out,2025,1)
            self.assertEqual(action,"built")
            self.assertEqual(doc["quality"]["rows"],1)
            self.assertEqual(doc["quality"]["suspension_event_rows"],2)
            self.assertEqual(db.execute("SELECT factor_scaled_close,total_mv_cny FROM market").fetchone(),(20,2000000))
            self.assertEqual(bt.write_partition(db,sources,out,2025,1)[1],"reused")
            path=out/"year=2025"/"month=01"/doc["outputs"]["market"]["file"]
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError,"Corrupt existing output"):
                bt.write_partition(db,sources,out,2025,1)
            write("adj_factor",sqls["adj_factor"]+" WHERE false")
            with self.assertRaisesRegex(ValueError,"Blocking input quality"):
                bt.build_table(db,sources,2025,1)
            write("adj_factor",sqls["adj_factor"]+" UNION ALL "+sqls["adj_factor"])
            with self.assertRaisesRegex(ValueError,"Duplicate source keys"):
                bt.build_table(db,sources,2025,1)
            db.close()


class IntegrityTests(unittest.TestCase):
    def test_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"file"
            p.write_bytes(b"original")
            meta={"bytes":8,"sha256":bt.digest(p),"rows":1}
            self.assertEqual(bt.verified(p,meta),(p,meta))
            p.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError,"checksum"):
                bt.verified(p,meta)
