from datetime import datetime,timezone,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest,json
import bridge,worker
from collection_runner import reserve,command
from test_bridge import Fake,request,NOW

class AsyncTests(unittest.TestCase):
    def test_nonblocking_queue_and_worker_receipt(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);spool=root/"spool";state=root/"state";state.mkdir()
            for lane in ("research","collection"):
                for kind in ("requests","results"):(spool/lane/kind).mkdir(parents=True)
            cfg={"repository_id":8,"actor_ids":[123],"profiles":{},"async_profiles":{"bt05-preflight":"research"}}
            gh=Fake(request("run_profile",profile="bt05-preflight"))
            def no_sync(*args):self.fail("sync execution must not occur")
            with patch.object(bridge,"SPOOL",spool):
                bridge.run(cfg,gh,state,NOW,no_sync)
                self.assertFalse(gh.reports)
                self.assertEqual(bridge.queue_status(json.loads((state/"issue-77.json").read_text())),"queued")
                # A second query is serviced while the first task is queued.
                gh.issue={**request("task_status"),"id":78,"number":8}
                bridge.run(cfg,gh,state,NOW,no_sync)
                self.assertEqual(gh.reports[0][1]["recent_tasks"][0]["status"],"queued")
                gh.issue={**request("run_profile",profile="bt05-preflight"),"id":79,"number":9}
                bridge.run(cfg,gh,state,NOW,no_sync)
                self.assertFalse((spool/"research"/"requests"/"issue-79.json").exists())
                calls=[]
                def work(*args):
                    calls.append(args)
                    self.assertEqual(bridge.queue_status(json.loads((state/"issue-77.json").read_text())),"running")
                    return {"status":"completed","exit_code":0,"elapsed_seconds":1.2}
                worker.run("research",{"repository_id":8,"profiles":{"bt05-preflight":{}}},executor=work)
                worker.run("research",{"repository_id":8,"profiles":{"bt05-preflight":{}}},executor=work)
                self.assertEqual(len(calls),1)
                bridge.run(cfg,gh,state,NOW,no_sync)
                self.assertEqual(gh.reports[-1][1]["status"],"completed")
                self.assertEqual(gh.closed,[8,7])
    def test_interruption_and_worker_identity(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            for k in ("requests","results"):(root/"collection"/k).mkdir(parents=True)
            req={"schema":"ashare-local-job-v1","repository_id":8,"issue_id":77,
                 "profile":"collect-reference","request_sha256":"a"*64,"lane":"collection"}
            (root/"collection/requests/issue-77.json").write_text(json.dumps(req))
            dst=root/"collection/results/issue-77.json"
            dst.write_text(json.dumps({**req,"schema":"ashare-local-result-v1","status":"running"}))
            with patch.object(bridge,"SPOOL",root):
                worker.run("collection",{"repository_id":8,"profiles":{}},executor=lambda *_:self.fail("do not repeat"))
                self.assertEqual(json.loads(dst.read_text())["status"],"interrupted_unknown_do_not_retry")
                with self.assertRaises(ValueError):worker.run("collection",{"repository_id":9,"profiles":{}})
    def test_daily_budget_and_fixed_capture_command(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            reserve(root,4,10,NOW);reserve(root,6,10,NOW)
            with self.assertRaisesRegex(ValueError,"budget exhausted"):reserve(root,1,10,NOW)
            reserve(root,1,10,NOW+timedelta(days=1))
            self.assertEqual(json.loads((root/"budget.json").read_text())["reserved"],1)
            d={"python":"/python","start":"2025-01-01","end":"2025-01-31","reference_budget":1,"dividend_budget":1,"calendar":None}
            self.assertIn("CAPTURE-REFERENCE",command("reference",d,root))
            with self.assertRaisesRegex(ValueError,"calendar"):command("dividend",d,root)
            with self.assertRaises(ValueError):command("shell",d,root)
if __name__=="__main__":unittest.main()
