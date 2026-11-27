from datetime import date
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
from unittest.mock import patch
import unittest
import bridge
import collection_runner as collection
import pilot_reference_review as pilot

class PilotReviewTests(unittest.TestCase):
    def fixture(self):
        sessions=pilot.expected_sessions()
        indexes={c:[{"date":d,"close":1000.0} for d in sessions]
                 for c in ("000300.SH","000905.SH","000852.SH")}
        for c,d,v,_ in pilot.SAMPLES:
            next(x for x in indexes[c] if x["date"]==d)["close"]=v
        return {"status":"BT05_REFERENCE_CANDIDATE_NOT_REVIEWED","from":"2020-01-01",
                "through":"2025-12-31","sessions":sessions,"indexes":indexes,"inputs":[]}

    def test_calendar_and_samples(self):
        d=self.fixture()
        self.assertEqual(len(pilot.check(d)["sessions"]),143)
        self.assertNotIn("2025-01-28",d["sessions"])
        d["sessions"].remove("2024-12-30")
        with self.assertRaisesRegex(ValueError,"session mismatch"):pilot.check(d)
        d=self.fixture()
        next(x for x in d["indexes"]["000300.SH"] if x["date"]=="2024-12-31")["close"]+=1
        with self.assertRaisesRegex(ValueError,"sample mismatch"):pilot.check(d)

    def test_dividend_probe_resume_budget_and_redaction(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/"dividend").mkdir()
            sessions=[date(2025,1,d) for d in (2,3,6)]
            captured=set();calls=[];reports=[]
            div=types.ModuleType("value_screener.bt05_dividend_capture")
            div.read_calendar=lambda *a:sessions
            def audit(*a):
                return {"expected_sessions":len(sessions),"verified_sessions":len(captured),
                        "rows":len(captured)*2,"empty_responses":0}
            div.audit=audit
            def collect(root,sessions,client,budget):
                todo=[x for x in sessions if x not in captured][:budget]
                calls.append(budget);captured.update(todo)
                return {"new_requests":len(todo)}
            div.collect=collect
            pkg=types.ModuleType("value_screener");pkg.bt05_dividend_capture=div
            client=types.ModuleType("value_screener.tushare_client")
            client.TushareClient=lambda *a,**k:object()
            d={"backend":"/pinned/backend","calendar":"/reviewed/cal.json","start":"2020-01-01",
               "end":"2025-12-31","dividend_start":"2025-01-01","dividend_end":"2025-01-31",
               "dividend_budget":50,"daily_budget":2}
            with patch.dict(sys.modules,{"value_screener":pkg,"value_screener.bt05_dividend_capture":div,
                    "value_screener.tushare_client":client}),patch.object(collection,"BASE",root),patch.object(collection,"store",side_effect=lambda p,d:reports.append(d)),patch.object(sys,"path",list(sys.path)):
                self.assertEqual(collection.dividend_capture(d,"dummy"),0)
                self.assertEqual(reports[-1]["verified_sessions"],1)
                self.assertEqual(collection.dividend_capture(d,"dummy"),0)
                self.assertEqual(reports[-1]["verified_sessions"],2)
                self.assertEqual(collection.dividend_capture(d,"dummy"),0)
                self.assertEqual(reports[-1]["state"],"budget_exhausted")
                self.assertEqual(calls,[1,1])
                d["daily_budget"]=3
                self.assertEqual(collection.dividend_capture(d,"dummy"),0)
                self.assertEqual(reports[-1]["state"],"capture_complete")
                self.assertEqual(collection.dividend_capture(d,"dummy"),0)
                self.assertEqual(calls,[1,1,1])
            report=bridge.clean_report("dividend_capture",{**reports[-1],"token":"dummy","path":"/private"})
            self.assertEqual(report["verified_sessions"],3)
            self.assertNotIn("token",report)
            self.assertNotIn("path",report)
            self.assertFalse(report["formal_backtest_ready"])

if __name__=="__main__":unittest.main()
