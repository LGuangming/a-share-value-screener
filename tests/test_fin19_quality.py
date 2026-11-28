import json
import tempfile
import unittest
from pathlib import Path

from scripts import fin19_quality as q
from scripts import fin19_market_shares as m
from scripts import fin19_standardize as s
from scripts.fin19_metrics import decisions


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
