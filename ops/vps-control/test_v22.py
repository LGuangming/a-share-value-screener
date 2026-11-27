import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import bridge
import maintenance_runner as maint
import runtime_slots as slots

class V22Tests(unittest.TestCase):
    def plan(self):
        return {"python":"/opt/runtime/python","backend":"/opt/backend","archive":"/archive",
                "evidence":"/evidence","dataset":"bt03-test","batches":[
                {"stage":"market","year":2025,"month":1},{"stage":"security","year":2025,"month":1}]}

    def test_maintenance_fixed_batch_and_resume(self):
        d=maint.validate(self.plan())
        with TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            def invoke(argv,**kwargs):
                calls.append(argv);return 2 if len(calls)==2 else 0
            self.assertEqual(maint.run(d,root,invoke),0)
            self.assertEqual(maint.run(d,root,invoke),2)
            self.assertEqual(maint.run(d,root,invoke),0)
            self.assertEqual(maint.run(d,root,invoke),0)
            self.assertEqual(len(calls),3)
            self.assertIn("value_screener.backtest_dataset",calls[0])
            self.assertIn("value_screener.backtest_security_evidence",calls[1])
            self.assertEqual(calls[1],calls[2])
            report=json.loads((root/"bt03-test/maintenance.json").read_text())
            self.assertEqual(bridge.clean_report("maintenance",report)["completed_batches"],2)
            changed=self.plan();changed["batches"].append({"stage":"market","year":2025,"month":2})
            with self.assertRaisesRegex(ValueError,"Plan changed"):maint.run(changed,root,invoke)
    def test_invalid_plan(self):
        d=self.plan();d["batches"].reverse()
        with self.assertRaisesRegex(ValueError,"matching market"):maint.validate(d)
        d=self.plan();d["batches"][0]["month"]=13
        with self.assertRaises(ValueError):maint.validate(d)
    def test_reuse_read_only_market(self):
        d=self.plan()
        d["existing_market"]="/existing/market"
        d["batches"]=d["batches"][1:]
        maint.validate(d)
        args=maint.command(d,d["batches"][0],Path("/work"))
        self.assertEqual(args[args.index("--market")+1],"/existing/market")
        self.assertEqual(args[args.index("--output")+1],"/work/security")
        d["batches"].append({"stage":"market","year":2025,"month":2})
        with self.assertRaisesRegex(ValueError,"read-only"):maint.validate(d)
    def test_runtime_selection_fail_closed(self):
        d={"baseline":{"revision":"a"*40},"candidate":{"revision":"b"*40}}
        with TemporaryDirectory() as tmp:
            base=Path(tmp)
            self.assertEqual(slots.selected(d,base),"baseline")
            with patch.object(slots,"verify",side_effect=lambda doc,slot:doc[slot]):
                slots.switch("candidate",d,base)
                self.assertEqual(slots.selected(d,base),"candidate")
                slots.switch("baseline",d,base)
                self.assertEqual(slots.selected(d,base),"baseline")
            d["baseline"]["revision"]="c"*40
            with self.assertRaises(ValueError):slots.selected(d,base)
            with patch.object(slots,"verify",side_effect=ValueError("digest mismatch")):
                before=(base/"runtime-selection.json").read_bytes()
                with self.assertRaises(ValueError):slots.switch("candidate",d,base)
                self.assertEqual((base/"runtime-selection.json").read_bytes(),before)
if __name__=="__main__":unittest.main()
