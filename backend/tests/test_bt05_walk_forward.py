import json
import unittest
from datetime import date,timedelta
from tempfile import TemporaryDirectory
from pathlib import Path
from value_screener import bt05_walk_forward as wf
from value_screener.bt05_research import identity
from value_screener.technical_strategies import TREND

START=date(2025,1,1)
DAYS=[START+timedelta(days=i) for i in range(20)]

def row(a,b,c,d):
    return dict(zip(("train_start","train_end","test_start","test_end"),[DAYS[i].isoformat() for i in (a,b,c,d)]))

class WalkForwardTests(unittest.TestCase):
    def test_plan_disjoint_advancing_and_bounded_grid(self):
        p={"folds":[row(1,5,6,9),row(4,10,11,15)],
           "signal_configs":[{},{"rsi_limit":25}],"execution_configs":[{},{"holding_sessions":5}]}
        folds,combos,mt,dd=wf.plan(p,DAYS)
        self.assertEqual((len(folds),len(combos),mt,dd),(2,4,10,.30))
        p["folds"][1]=row(4,7,8,15)
        with self.assertRaisesRegex(ValueError,"nonoverlapping"):wf.plan(p,DAYS)
        p["folds"]=[row(1,5,6,9)]
        p["signal_configs"]=[{},{}]
        with self.assertRaisesRegex(ValueError,"Duplicate"):wf.plan(p,DAYS)

    def test_benchmark_requires_coverage_provenance_and_type(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/"index.json"
            d={"status":"OPERATOR_REVIEWED_BENCHMARK_NOT_CERTIFIED","kind":"price_index","code":"SYNTH","source_ref":"fixture",
                "levels":[{"date":x.isoformat(),"close":100+i} for i,x in enumerate(DAYS)]}
            p.write_text(json.dumps(d))
            levels,ref=wf.benchmark(p,DAYS)
            self.assertEqual(levels[DAYS[-1]],119)
            self.assertEqual(ref["kind"],"price_index")
            d["levels"].pop();p.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,"coverage missing"):wf.benchmark(p,DAYS)
            d["kind"]="unknown";p.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,"provenance"):wf.benchmark(p,DAYS)

    def test_aligned_comparison_and_tamper_detection(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);days=DAYS[:3]
            body={"run_id":"fixture","previous_hash":None,"records":{TREND:[
                {"equity":{"day":d.isoformat(),"equity":v}} for d,v in zip(days,[100.,110.,105.])]}}
            h=identity(body);p=root/"month-2025-01.json"
            p.write_text(json.dumps({"body":body,"sha256":h}))
            report={"run_id":"fixture","checkpoints":[{"file":p.name,"sha256":h}]}
            c=wf.comparison(root,report,TREND,dict(zip(days,[100.,102.,101.])),days,100.)
            self.assertAlmostEqual(c["strategy_return"],.05)
            self.assertAlmostEqual(c["benchmark_return"],.01)
            self.assertAlmostEqual(c["excess_return_difference"],.04)
            self.assertAlmostEqual(c["benchmark_max_drawdown"],101/102-1)
            self.assertEqual(c["curve"][0]["strategy_nav"],1.)
            with self.assertRaisesRegex(ValueError,"session mismatch"):
                wf.comparison(root,report,TREND,dict(zip(days,[100.,102.,101.])),days[:2],100.)
            body["records"][TREND][1]["equity"]["equity"]=999.
            p.write_text(json.dumps({"body":body,"sha256":h}))
            with self.assertRaisesRegex(ValueError,"Checkpoint hash"):
                wf.comparison(root,report,TREND,dict(zip(days,[100.,102.,101.])),days,100.)

if __name__=="__main__":unittest.main()
