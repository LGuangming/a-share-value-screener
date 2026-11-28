import contextlib
from contextlib import closing
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import fin19_standardize as s
from scripts import fin19_metrics as m
from scripts import fin19_quality as q
from scripts import fin20_archive as a
from scripts import fin21_latest as latest
import test_fin19_standardize as fixture


def daily(raw,stamp):
    from datetime import datetime
    d=datetime.strptime(stamp,"%Y%m%d").date()
    suffix=f"year={d.year}/month={d.month:02d}/day={stamp}"
    records=[]
    with closing(a.connection()) as con:
        for ds in sorted(latest.market.DATASETS):
            path=raw/ds/(suffix+".parquet")
            path.parent.mkdir(parents=True,exist_ok=True)
            if ds=="daily_prices":
                sql=f"SELECT '000001.SZ' ts_code,DATE '{d}' trade_date,10.0 close UNION ALL SELECT '000002.SZ',DATE '{d}',20.0"
            elif ds=="daily_basic":
                sql=f"""SELECT '000001.SZ' ts_code,DATE '{d}' trade_date,0.01 total_mv,0.001 total_share,2.0 pb,5.0 pe_ttm
                        UNION ALL SELECT '000002.SZ',DATE '{d}',0.04,0.002,3.0,6.0"""
            else:
                sql=f"SELECT '000001.SZ' ts_code,DATE '{d}' trade_date"
            con.execute("COPY ("+sql+") TO "+a.literal(path)+" (FORMAT PARQUET)")
            n=con.execute("SELECT count(*) FROM read_parquet(?)",[str(path)]).fetchone()[0]
            meta={"dataset":ds,"object_key":latest.market.PREFIX+"/"+path.relative_to(raw).as_posix(),
                  "trade_date":d.isoformat(),"status":"verified_daily_partition","format_version":1,
                  "rows":n,"parquet_bytes":path.stat().st_size,"sha256":s.digest(path)}
            path.with_suffix(".manifest.json").write_text(json.dumps(meta))
            records.append(meta)
    cal=raw/"trade_calendar"/(suffix+".json")
    cal.parent.mkdir(parents=True,exist_ok=True)
    cal.write_text(json.dumps({"trade_date":d.isoformat(),"exchange":"SSE","is_open":True}))
    commit=raw/"_incremental/market"/f"year={d.year}/day={stamp}.json"
    commit.parent.mkdir(parents=True,exist_ok=True)
    commit.write_text(json.dumps({"status":"verified_market_day","trade_date":d.isoformat(),"datasets":records}))


@unittest.skipUnless(importlib.util.find_spec("duckdb"),"Parquet runtime required")
class LatestTests(unittest.TestCase):
    def test_full_published_snapshot_latest(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            root=Path(d);new,old=fixture.FinancialStandardizationTests().fixture(root)
            archive=root/"archive";raw=archive/"a-share-backtest/01_raw_data"
            raw.parent.mkdir(parents=True);old.rename(raw)
            (new/"unfinished.csv").write_text("task,status\n")
            ledger=root/"ledger";metrics=root/"metrics";quality=root/"quality"
            s.build(new,raw,ledger,date(2026,11,28))
            m.build(ledger,metrics);q.build(ledger,metrics,raw,quality)
            stage=root/"stage";restored=root/"restored"
            a.build(ledger,metrics,quality,new,raw,stage)
            a.restore(stage,raw,restored)
            approve=s.digest(stage/"manifest.json")
            a.publish(stage,raw,restored,archive,approve)
            snap=archive/"a-share-backtest/financial_snapshots"/("snapshot=2026-11-28-"+approve[:16])
            daily(raw,"20261127")
            out=root/"latest"
            latest.build(snap,raw,approve,out)
            doc=json.loads((out/"manifest.json").read_text())
            self.assertEqual(doc["codes"],2)
            self.assertEqual(doc["market_date"],"2026-11-27")
            self.assertFalse(doc["pit_certified"])
            self.assertEqual(len(doc["files"]),7)
            with closing(a.connection()) as con:
                def read(name):
                    return "read_parquet("+a.literal(out/(name+".parquet"))+")"
                rows=con.execute("SELECT code,period,ncav,ncav_status FROM "+read("financial_metrics")+" ORDER BY code").fetchall()
                self.assertEqual(rows,[("000001.SZ","20251231",40.0,"ok"),("000002.SZ",None,None,"no_observed_financial_period")])
                self.assertEqual(con.execute("SELECT market_cap_cny,market_shares,cap_to_positive_ncav FROM "+read("valuation_metrics")+" WHERE code='000001.SZ'").fetchone(),(100.0,10.0,2.5))
                self.assertEqual(con.execute("SELECT count(*) FROM "+read("statement_versions")).fetchone()[0],2)
                self.assertEqual(con.execute("SELECT count(*) FROM "+read("financial_statements")).fetchone()[0],6)
                self.assertEqual(con.execute("SELECT count(*) FROM "+read("strategy_inputs")+" WHERE eligibility_certified").fetchone()[0],0)
            with self.assertRaises(ValueError):
                latest.build(snap,raw,"0"*64,root/"wrong")
            with self.assertRaises(ValueError):
                latest.build(snap,raw,approve,root/"future","2026-11-29")
            # A newer observed but incomplete commit must fail closed.
            marker=raw/"_incremental/market/year=2026/day=20261128.json"
            marker.write_text("{}")
            with self.assertRaises((ValueError,FileNotFoundError)):
                latest.build(snap,raw,approve,root/"bad")

    def test_market_day_no_silent_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            raw=Path(d)
            daily(raw,"20261126");daily(raw,"20261127")
            self.assertEqual(latest.market_day(raw,"2026-11-28",None),date(2026,11,27))
            self.assertEqual(latest.market_day(raw,"2026-11-28","2026-11-26"),date(2026,11,26))
            p=raw/"daily_basic/year=2026/month=11/day=20261127.parquet"
            with p.open("ab") as stream:
                stream.write(b"x")
            with closing(a.connection()) as con,self.assertRaises(ValueError):
                latest.market_views(con,raw,date(2026,11,27))


if __name__=="__main__":
    unittest.main()
