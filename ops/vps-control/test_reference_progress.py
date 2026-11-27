import json
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import bridge
import collection_runner as collection

class ReferenceProgressTests(unittest.TestCase):
    def test_probe_then_resume_then_no_network(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/"reference").mkdir()
            state=set();budgets=[];reports=[]
            ref=types.ModuleType("value_screener.bt05_reference_capture")
            ref.INDEXES=("000300.SH","000905.SH","000852.SH")
            ref.slices=lambda start,end:[(start,end)]
            ref.read_slice=lambda root,*item: {} if item in state else None
            def capture(root,start,end,codes,client,budget):
                budgets.append(budget)
                expected=[("calendar","SSE",start,end),("calendar","SZSE",start,end)]+[
                    ("index",c,start,end) for c in codes]
                todo=[x for x in expected if x not in state][:budget]
                state.update(todo)
                return {"new_requests":len(todo)}
            ref.capture=capture
            ref.build=lambda *args:{"status":"candidate"}
            ref.write_new=lambda p,d:p.write_text(json.dumps(d))
            pkg=types.ModuleType("value_screener")
            pkg.bt05_reference_capture=ref
            client=types.ModuleType("value_screener.tushare_client")
            client.TushareClient=lambda *a,**k:object()
            d={"backend":"/pinned/backend","start":"2025-01-01","end":"2025-12-31",
               "reference_budget":30,"daily_budget":31}
            with patch.dict(sys.modules,{"value_screener":pkg,"value_screener.bt05_reference_capture":ref,
                "value_screener.tushare_client":client}),patch.object(collection,"BASE",root),patch.object(collection,"store",side_effect=lambda p,d:reports.append(d)),patch.object(sys,"path",list(sys.path)):
                self.assertEqual(collection.reference_capture(d,"dummy"),0)
                self.assertEqual(reports[-1]["verified_slices"],1)
                self.assertEqual(reports[-1]["state"],"partial")
                self.assertEqual(collection.reference_capture(d,"dummy"),0)
                self.assertEqual(reports[-1]["verified_slices"],5)
                self.assertEqual(reports[-1]["state"],"candidate_ready")
                self.assertEqual(collection.reference_capture(d,"dummy"),0)
                self.assertEqual(budgets,[1,4])
                self.assertEqual(json.loads((root/"budget.json").read_text())["reserved"],5)
            cleaned=bridge.clean_report("reference_capture",{**reports[-1],"token":"dummy","path":"/private"})
            self.assertNotIn("token",cleaned)
            self.assertNotIn("path",cleaned)
            self.assertFalse(cleaned["formal_backtest_ready"])
if __name__=="__main__":unittest.main()
