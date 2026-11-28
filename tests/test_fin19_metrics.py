from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from scripts import fin19_metrics as m
from scripts import fin19_standardize as s


def group(api,period,values,vid=1,comp="1",end="1",eff=None):
    eff=eff or period
    g=m.group_record((api,period,comp,end,eff),[(vid,values,[])])
    g["id"]=vid
    return g


class MetricTests(unittest.TestCase):
    def test_conflicts_and_zero(self):
        d=m.decisions([(1,{"x":0,"y":1,"z":None},[]),
                       (2,{"x":0.0,"y":2},[])],["x","y","z"])
        self.assertEqual(d["x"]["value"],0)
        self.assertEqual(d["y"]["state"],"conflicting_non_null")
        self.assertIsNone(d["y"]["value"])
        self.assertEqual(d["z"]["state"],"no_value")
        self.assertEqual(d["z"]["absent"],1)
        self.assertEqual(d["z"]["null"],1)

    def test_no_synthetic_statement(self):
        g=m.group_record(("balancesheet","20251231","1","1","20260301"),
                         [(1,{"total_cur_assets":100},[]),(2,{"total_liab":40},[])])
        self.assertEqual(m.extract(g,["total_cur_assets","total_liab"])[2],"no_coherent_source_version")
        g=m.group_record(("balancesheet","20251231","1","1","20260301"),
                         [(1,{"total_cur_assets":100,"total_liab":40},[]),(2,{"total_cur_assets":100},[])])
        self.assertEqual(m.extract(g,["total_cur_assets","total_liab"])[:2],([100,40],[1]))

    def test_no_fallback(self):
        a=group("income","20251231",{"revenue":100},eff="20260301")
        b=group("income","20251231",{},eff="20260401")
        self.assertIs(m.choose([a,b])[0],b)
        self.assertEqual(m.extract(b,["revenue"])[2],"no_value")
        bad=group("income","20251231",{},eff="20250101")
        self.assertEqual(m.choose([bad])[1],"invalid_latest_group")

    def test_legacy_overlap(self):
        a=group("income","20251231",{"revenue":100},eff="20260301")
        b=group("income","20251231",{"revenue":99},end="",eff="20260301")
        self.assertEqual(m.choose([a,b])[1],"legacy_overlap_conflict")
        b=group("income","20251231",{"revenue":100,"n_income":9},end="",eff="20260301")
        self.assertIs(m.choose([a,b])[0],a)
        self.assertEqual(m.extract(a,["n_income"])[2],"no_value")
        self.assertEqual(m.extract(b,["revenue"])[2],"unknown_report_period_type")

    def test_derived_only_conflict(self):
        g=m.group_record(("cashflow","20251231","1","1","20260301"),
                         [(1,{"n_cashflow_act":120,"c_pay_acq_const_fiolta":40,"free_cashflow":80},[]),
                          (2,{"n_cashflow_act":120,"c_pay_acq_const_fiolta":40,"free_cashflow":60},[])])
        g["id"]=1
        self.assertEqual(m.extract(g,["free_cashflow"])[2],"conflicting_non_null")
        self.assertEqual(m.extract(g,["n_cashflow_act","c_pay_acq_const_fiolta"])[0],[120,40])
        rows={r[3]:r for r in m.metric_builder({("cashflow","20251231","1"):g},"000001.SZ","20251231","1")}
        self.assertEqual(rows["fcf_ttm"][4],80)

    def test_ttm_and_quarter(self):
        selected={}
        for p,rev,profit,assets,equity in [
            ("20250331",80,8,180,80),("20250630",180,18,200,90),
            ("20251231",400,40,240,100),("20260331",110,11,250,110),
            ("20260630",240,24,300,150)]:
            selected[("income",p,"1")]=group("income",p,{"revenue":rev,"n_income":profit,
                "n_income_attr_p":profit},vid=len(selected)+1)
            selected[("balancesheet",p,"1")]=group("balancesheet",p,{"total_assets":assets,
                "total_hldr_eqy_exc_min_int":equity},vid=len(selected)+1)
        rows={r[3]:r for r in m.metric_builder(selected,"000001.SZ","20260630","1")}
        self.assertEqual(rows["revenue_quarter"][4],130)
        self.assertEqual(rows["revenue_ttm"][4],460)
        self.assertAlmostEqual(rows["roe_ttm"][4],46/120)
        self.assertAlmostEqual(rows["roa_ttm"][4],46/250)
        self.assertAlmostEqual(rows["revenue_yoy"][4],60/180)
        del selected[("income","20251231","1")]
        rows={r[3]:r for r in m.metric_builder(selected,"000001.SZ","20260630","1")}
        self.assertIsNone(rows["revenue_ttm"][4])

    def test_financial_industry_and_type(self):
        g=group("balancesheet","20251231",{"total_cur_assets":100,"total_liab":40},comp="2")
        rows={r[3]:r for r in m.metric_builder({("balancesheet","20251231","2"):g},"000001.SZ","20251231","2")}
        self.assertEqual(rows["ncav"][6],"industry_not_applicable_or_unverified")
        a=group("income","20251231",{"n_income":10},end="1")
        b=group("cashflow","20251231",{"n_cashflow_act":10},end="2")
        rows={r[3]:r for r in m.metric_builder({("income","20251231","1"):a,("cashflow","20251231","1"):b},"000001.SZ","20251231","1")}
        self.assertEqual(rows["cash_to_profit_ytd"][6],"report_period_type_mismatch")

    def test_full_read_only_build(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); ledger=root/"ledger";ledger.mkdir()
            path=ledger/"financial.sqlite3"
            with sqlite3.connect(path) as db:
                db.executescript(s.SCHEMA)
                db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(
                    1,"balancesheet","hash","000001.SZ","20251231","1","1","1","20260301",
                    json.dumps({"total_cur_assets":100,"total_liab":40}),"[]","{}","{}"))
            h=s.digest(path)
            (ledger/"manifest.json").write_text(json.dumps({"format_version":2,"state":"standardized_staging_not_pit_certified","pit_certified":False,"production_published":False,"ledger_bytes":path.stat().st_size,"ledger_sha256":h,"asof":"2026-11-28"}))
            output=root/"out"
            m.build(ledger,output)
            self.assertEqual(s.digest(path),h)
            doc=json.loads((output/"manifest.json").read_text())
            self.assertFalse(doc["pit_certified"])
            self.assertEqual(doc["metrics_sha256"],s.digest(output/"metrics.sqlite3"))
            with sqlite3.connect(output/"metrics.sqlite3") as db:
                self.assertEqual(db.execute("SELECT value,status FROM metrics WHERE metric='ncav'").fetchone(),(60,"ok"))
            with self.assertRaises(ValueError):
                m.build(ledger,output)
            with path.open("ab") as f:
                f.write(b"x")
            with self.assertRaises(ValueError):
                m.build(ledger,root/"bad")
            self.assertFalse((root/"bad").exists())


if __name__=="__main__":
    unittest.main()
